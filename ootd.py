"""每日 OOTD 的纯逻辑：缓存读写、提示词构造、响应解析与随机模式挑选。

本模块不 import astrbot / time_awareness，全部可本地单测。
生成编排（LLM 调用、provider 解析、后台任务）位于 main.py。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import random
from typing import Any

import yaml

OOTD_CACHE_SCHEMA_VERSION = 1
OOTD_CACHE_FILE_NAME = "ootd.yaml"
OOTD_RETENTION_DAYS = 30

# 穿搭风格池（默认 12 条）
DEFAULT_OUTFIT_STYLE_POOL = [
    "知性学院风",
    "街头休闲风",
    "温柔淑女风",
    "酷飒中性风",
    "慵懒居家风",
    "精致约会风",
    "运动活力风",
    "日系森女风",
    "法式优雅风",
    "韩系甜美风",
    "复古文艺风",
    "极简都市风",
]

# 随机模式「今天是什么日子」主题池（借鉴时笺 daily_theme 的常见基调）
DEFAULT_RANDOM_THEME_POOL = [
    "演习日",
    "宅家日",
    "外出日",
    "约会日",
    "工作冲刺日",
    "休闲放松日",
    "社交日",
    "创作日",
    "运动日",
    "出行日",
    "整顿收纳日",
    "探店日",
]

# 随机模式「当日日程」候选池（随机抽若干条，仅 name，不含严格时段）
DEFAULT_RANDOM_SLOT_POOL = [
    "晨间散步",
    "咖啡馆办公",
    "下午茶",
    "健身房训练",
    "逛街",
    "朋友聚餐",
    "居家观影",
    "图书馆自习",
    "公园慢跑",
    "超市采购",
    "线上会议",
    "手作/画画",
]

_WEEKDAY_FULL_CN = (
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
)

OOTD_SYSTEM_PROMPT = (
    "你是「今日穿搭」生成器。根据给出的人设、日期、当日主题/状态色彩、当日日程时段"
    "与天气，为角色设计一套今日穿搭（OOTD）。\n\n"
    "必须遵守：\n"
    "1. 只输出一个 JSON 对象，字段严格为 {\"outfit_style\": \"...\", \"outfit\": \"...\"}，"
    "不要输出任何解释、前后缀或 Markdown 代码块。\n"
    "2. outfit_style 必须从「穿搭风格池」中挑选一个最贴合当日主题/状态色彩的风格。\n"
    "3. outfit 为 50–120 字的陈述句，从里到外描述（上衣/下装/鞋袜/饰品），客观描述"
    "角色「今天正穿着什么」，不是给用户的穿搭建议。\n"
    "4. 穿搭必须体现当日主题与状态色彩（例如「演习日」偏向训练/行动装、「宅家日」"
    "偏向居家舒适、「外出日」偏向得体外出），但不要在 outfit 里直接写出主题词本身。\n"
    "5. 禁止指令口吻（如「建议你穿」「你应该穿」）；全程用陈述句。\n"
    "6. 未提供天气时按「当季通配」设计（符合当前季节的通用穿搭），不得自行臆测天气。"
)


# ==================== 缓存读写 ====================


def _normalize_date(value) -> str:
    """把日期统一成 ISO 字符串。"""
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value or "").strip()


def load_ootd_cache(path: str) -> dict:
    """读取 OOTD 缓存；缺失/损坏/版本不符时返回空结构。"""
    empty = {"schema_version": OOTD_CACHE_SCHEMA_VERSION, "ootd": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        return empty
    except Exception:
        return empty
    if not isinstance(raw, dict):
        return empty
    ootd = raw.get("ootd")
    if not isinstance(ootd, dict):
        return empty
    result = {
        str(key): value
        for key, value in ootd.items()
        if isinstance(value, dict)
    }
    return {"schema_version": OOTD_CACHE_SCHEMA_VERSION, "ootd": result}


def save_ootd_cache(path: str, data: dict) -> bool:
    """原子写入 OOTD 缓存。"""
    directory = os.path.dirname(path)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            return False
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                data,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        return False
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def get_cached_outfit(data: dict, persona_hash: str, date) -> dict | None:
    """命中缓存返回 entry 拷贝，否则 None。"""
    ootd = data.get("ootd") if isinstance(data, dict) else None
    if not isinstance(ootd, dict):
        return None
    by_date = ootd.get(persona_hash)
    if not isinstance(by_date, dict):
        return None
    entry = by_date.get(_normalize_date(date))
    if not isinstance(entry, dict):
        return None
    return dict(entry)


def put_cached_outfit(data: dict, persona_hash: str, date, entry: dict) -> None:
    """写入（或覆盖）某 Persona 某日的 OOTD entry。"""
    if not isinstance(data.get("ootd"), dict):
        data["ootd"] = {}
    by_date = data["ootd"].setdefault(persona_hash, {})
    if not isinstance(by_date, dict):
        by_date = {}
        data["ootd"][persona_hash] = by_date
    by_date[_normalize_date(date)] = dict(entry)


def prune_outfit_cache(
    data: dict,
    today,
    retention_days: int = OOTD_RETENTION_DAYS,
) -> None:
    """只保留 ``[today - (retention-1), today]`` 窗口内的条目。"""
    ootd = data.get("ootd") if isinstance(data, dict) else None
    if not isinstance(ootd, dict):
        return
    if isinstance(today, datetime.date):
        today_date = today
    else:
        try:
            today_date = datetime.date.fromisoformat(str(today))
        except ValueError:
            return
    retention_days = max(1, min(365, int(retention_days)))
    cutoff = today_date - datetime.timedelta(days=retention_days - 1)

    for persona_hash in list(ootd.keys()):
        by_date = ootd[persona_hash]
        if not isinstance(by_date, dict):
            ootd.pop(persona_hash, None)
            continue
        for date_key in list(by_date.keys()):
            try:
                entry_date = datetime.date.fromisoformat(str(date_key))
            except ValueError:
                by_date.pop(date_key, None)
                continue
            if entry_date < cutoff:
                by_date.pop(date_key, None)
        if not by_date:
            ootd.pop(persona_hash, None)


# ==================== 提示词构造 ====================


def build_outfit_prompt(
    *,
    persona_prompt: str,
    today: datetime.date,
    theme: str | None = None,
    style: str | None = None,
    slots_text: str = "",
    style_pool: list[str] | None = None,
) -> str:
    """构造 OOTD 生成的 user prompt（数据块 ``<TAG>`` + JSON 输出协议）。

    不接天气：不向模型提供天气数据，由 system prompt 要求「当季通配」。
    """
    weekday = _WEEKDAY_FULL_CN[today.weekday()]
    date_text = f"{today.isoformat()}（{weekday}）"

    boundary_parts = []
    if theme:
        boundary_parts.append(f"主题：{theme}")
    if style:
        boundary_parts.append(f"状态色彩：{style}")
    boundary_text = "；".join(boundary_parts) if boundary_parts else "无今日主题数据"

    slots_block = (slots_text or "").strip() or "无今日日程数据"
    persona_block = (persona_prompt or "").strip() or "无（按通用设定生成）"

    pool = style_pool if isinstance(style_pool, list) and style_pool else DEFAULT_OUTFIT_STYLE_POOL
    pool_text = "/".join(str(item).strip() for item in pool if str(item).strip())

    return (
        f"<DATE>{date_text}</DATE>\n"
        f"<PERSONA>{persona_block}</PERSONA>\n"
        f"<TODAY_BOUNDARY>{boundary_text}</TODAY_BOUNDARY>\n"
        f"<TODAY_SLOTS>{slots_block}</TODAY_SLOTS>\n"
        f"<STYLE_POOL>{pool_text}</STYLE_POOL>\n\n"
        "请生成今日穿搭 JSON。"
    )


# ==================== 随机模式 ====================


def standalone_persona_hash(persona_id: str) -> str:
    """随机模式的稳定匿名身份键（不落原始 persona_id）。

    前缀 ``rand_`` 与时笺模式的 ``persona_`` 键命名空间隔离，避免两种模式互相
    读到对方缓存；对高熵 persona_id 用 sha256 截断足够不可逆。
    """
    value = str(persona_id or "").strip()
    if not value:
        raise ValueError("persona_id 不能为空")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"rand_{digest[:24]}"


def _pick_from_pool(rnd: random.Random, pool) -> str | None:
    clean = [str(item).strip() for item in pool if str(item).strip()]
    if not clean:
        return None
    return rnd.choice(clean)


def pick_random_boundary(
    theme_pool,
    style_pool,
    slots_pool=(),
    slots_count: int = 3,
    rng: random.Random | None = None,
) -> tuple[str | None, str | None, list[dict]]:
    """随机模式：随机挑选主题/状态色彩，并从日程池抽若干条当日日程。

    返回 ``(theme, style, slots)``；``slots`` 为 ``[{"name": ...}]`` 列表，
    可直接交给 ``render_slots_text``。``rng`` 传入固定种子 ``random.Random(seed)``
    便于测试；缺省使用新的 ``random.Random()``（不依赖全局随机状态）。
    """
    rnd = rng if isinstance(rng, random.Random) else random.Random()
    theme = _pick_from_pool(rnd, theme_pool)
    style = _pick_from_pool(rnd, style_pool)
    clean_slots = [str(item).strip() for item in slots_pool if str(item).strip()]
    try:
        count = max(0, min(int(slots_count), len(clean_slots)))
    except (TypeError, ValueError):
        count = 0
    slots = [{"name": name} for name in rnd.sample(clean_slots, count)] if count else []
    return theme, style, slots


# ==================== 响应解析与校验 ====================


def _strip_json_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_outfit_response(text: str) -> dict | None:
    """解析 LLM 输出为 ``{"outfit_style": str, "outfit": str}``；失败返回 None。"""
    cleaned = _strip_json_fence(text)
    if not cleaned:
        return None
    try:
        value = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def validate_outfit(entry: dict | None) -> list[str]:
    """返回违反协议的原因列表；空列表表示合法。"""
    if not isinstance(entry, dict):
        return ["输出不是 JSON 对象"]
    reasons = []
    style = entry.get("outfit_style")
    if not isinstance(style, str) or not style.strip():
        reasons.append("缺少 outfit_style 字段")
    outfit = entry.get("outfit")
    if not isinstance(outfit, str) or not outfit.strip():
        reasons.append("缺少 outfit 字段")
        return reasons
    length = len(outfit.strip())
    if length < 30 or length > 200:
        reasons.append(f"outfit 长度 {length} 字不在合理区间 [30, 200]")
    return reasons


def normalize_outfit_entry(entry: dict) -> dict:
    """把解析结果规整为仅含两个字段的干净 dict。"""
    return {
        "outfit_style": str(entry.get("outfit_style") or "").strip(),
        "outfit": str(entry.get("outfit") or "").strip(),
    }
