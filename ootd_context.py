"""每日 OOTD 的时笺（time_awareness）上下文适配层。

经 time_awareness 实例公开的 ``daily_schedule_store`` 读写快照与 HMAC，不直接
import 其私有模块；Persona 解析复用独立模式的 ``resolve_standalone_persona``。

纯函数（find_today_snapshot / extract_boundary_fields / render_slots_text /
ootd_ready_minute）不依赖 astrbot，可本地用假 store 单测；时笺不可用时
``resolve_ootd_identity`` 返回 None（OOTD 静默关闭）。

不接天气：OOTD 不实现、不消费天气，生成直接按「当季通配」降级。
"""

from __future__ import annotations

import datetime
from typing import Any


class OutfitContext:
    """OOTD 生成所需的身份上下文（不含任何需要落盘的原始 persona_id）。

    ``theme/style/slots`` 不在此阶段读取——生成任务等待时笺就绪后另行读快照。
    """

    def __init__(
        self,
        *,
        persona_hash: str,
        persona_prompt: str,
        date: datetime.date,
    ):
        self.persona_hash = persona_hash
        self.persona_prompt = persona_prompt or ""
        self.date = date
        self.today = date.isoformat()

    def short_hash(self) -> str:
        return self.persona_hash[-8:] if self.persona_hash else "-"


# ==================== 纯函数（可本地单测） ====================


def find_today_snapshot(
    store,
    persona_hash: str,
    local_date,
) -> dict | None:
    """扫全部 ready 快照，匹配 persona_hash + local_date（忽略时区，自用单时区）。"""
    if store is None:
        return None
    try:
        snapshots = store.list_snapshots()
    except Exception:
        return None
    date_text = (
        local_date.isoformat()
        if isinstance(local_date, datetime.date)
        else str(local_date or "").strip()
    )
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        if (
            str(snapshot.get("persona_hash", "")) == persona_hash
            and str(snapshot.get("local_date", "")) == date_text
        ):
            return snapshot
    return None


def extract_boundary_fields(snapshot) -> tuple[str | None, str | None, list[dict]]:
    """从快照提取 daily_theme / daily_style / ai_slots 原始字段。"""
    if not isinstance(snapshot, dict):
        return None, None, []
    boundary = snapshot.get("boundary_state")
    if not isinstance(boundary, dict):
        boundary = {}
    theme = str(boundary.get("daily_theme") or "").strip() or None
    style = str(boundary.get("daily_style") or "").strip() or None
    slots = snapshot.get("ai_slots")
    if not isinstance(slots, list):
        slots = snapshot.get("slots")
    if not isinstance(slots, list):
        slots = []
    return theme, style, slots


def render_slots_text(slots, limit: int = 12) -> str:
    """把时段列表压成紧凑多行文本（用于提示词），超出 limit 截断并注明。"""
    if not slots:
        return ""
    lines = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        start = str(slot.get("start") or "").strip()
        end = str(slot.get("end") or "").strip()
        name = str(slot.get("name") or "").strip()
        state = str(slot.get("state") or "").strip()
        segment = f"{start}-{end}" if (start or end) else ""
        parts = [part for part in (segment, name, state) if part]
        if parts:
            lines.append(" ".join(parts))
    if not lines:
        return ""
    total = len(lines)
    if total > limit:
        lines = lines[:limit] + [f"…（共 {total} 段，仅展示前 {limit} 段）"]
    return "\n".join(lines)


def ootd_ready_minute(generation_time: str | None) -> int:
    """根据时笺 ``ai_daily.generation_time`` 计算 OOTD 生成就绪时刻（当天分钟数）。

    - 正 ``HH:MM``：当天 HH:MM 生成当天快照 → 就绪 = ``HH:MM + 5`` 分钟
    - ``-HH:MM``：前一天 HH:MM 预生成次日 → 次日 0 点就绪（返回 0）
    - 缺省/非法：与时笺 ``_parse_generation_time`` 同语义回退 00:05 → 就绪 00:10
    """
    raw = str(generation_time or "").strip()
    if raw.startswith("-"):
        return 0
    hour, minute = 0, 5
    if raw:
        try:
            hour_text, minute_text = raw.split(":", 1)
            parsed_hour = int(hour_text)
            parsed_minute = int(minute_text)
            if 0 <= parsed_hour <= 23 and 0 <= parsed_minute <= 59:
                hour, minute = parsed_hour, parsed_minute
        except (ValueError, AttributeError):
            pass
    return (hour * 60 + minute + 5) % 1440


# ==================== 时笺运行态适配 ====================


def resolve_now(ta_instance=None) -> datetime.datetime:
    """取「今天」的参考时刻：优先复用时笺运行时的时区 now，回退系统本地。"""
    if ta_instance is not None:
        try:
            time_context = getattr(ta_instance, "time_context", None)
            now_fn = getattr(time_context, "now", None)
            if callable(now_fn):
                value = now_fn()
                if isinstance(value, datetime.datetime):
                    return value
        except Exception:
            pass
    return datetime.datetime.now().astimezone()


async def resolve_ootd_identity(
    context,
    umo: str,
    event=None,
    *,
    store=None,
    ta_instance=None,
    now: datetime.datetime | None = None,
) -> OutfitContext | None:
    """解析 Persona 身份（persona_hash + prompt + date），**不读快照**。

    快照在生成任务等待时笺就绪后由 ``read_outfit_snapshot`` 另行读取。
    时笺不可用/无人格/无 secret 时返回 None。
    """
    if store is None:
        return None

    # Persona 解析复用独立模式（把 conversation 的 "[%None]" 归一化为未指定，
    # 回退 provider 默认 persona），避免时笺 resolver 对 "[%None]" 返回空。
    from .ootd_standalone import extract_persona_prompt, resolve_standalone_persona

    try:
        persona_id, persona = await resolve_standalone_persona(context, umo, event)
    except Exception:
        return None
    if not persona_id:
        return None

    prompt = extract_persona_prompt(persona)

    today = (now or resolve_now(ta_instance)).date()

    try:
        persona_hash = store.persona_hash(persona_id)
    except Exception:
        # secret 不可得 / 时笺数据目录不可用 → 无法计算 HMAC 缓存键，OOTD 关闭。
        return None

    return OutfitContext(
        persona_hash=persona_hash,
        persona_prompt=prompt,
        date=today,
    )


def read_outfit_snapshot(
    store,
    persona_hash: str,
    local_date,
) -> tuple[str | None, str | None, list[dict]]:
    """读时笺当日 ready 快照的原始字段；无快照返回 ``(None, None, [])``。"""
    if store is None:
        return None, None, []
    try:
        snapshot = find_today_snapshot(store, persona_hash, local_date)
    except Exception:
        return None, None, []
    if snapshot is None:
        return None, None, []
    return extract_boundary_fields(snapshot)


def has_today_snapshot(store, persona_hash: str, local_date) -> bool:
    """时笺是否已生成该 Persona 当天的 ready 快照（供就绪轮询判断）。"""
    if store is None:
        return False
    try:
        return find_today_snapshot(store, persona_hash, local_date) is not None
    except Exception:
        return False
