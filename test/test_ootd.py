"""OOTD 纯逻辑单测。

只保留「改错了一眼看不出来」的静默失效面：缓存条目不落盘/键漂移、
缓存无限增长、prompt 协议块被改、脏数据落库、配置键被静默忽略。
看得出的一律不测（解析失败会直接表现为生成失败）。
"""

import datetime
import random

from astrbot_ootd.ootd import (
    DEFAULT_OUTFIT_STYLE_POOL,
    DEFAULT_RANDOM_SLOT_POOL,
    DEFAULT_RANDOM_THEME_POOL,
    build_outfit_prompt,
    get_cached_outfit,
    load_ootd_cache,
    pick_random_boundary,
    prune_outfit_cache,
    put_cached_outfit,
    save_ootd_cache,
    standalone_persona_hash,
    validate_outfit,
)


def test_cache_round_trip_keeps_entry_and_date_key(tmp_path):
    """守：条目没落盘（缓存永不命中、每次重复调 LLM）与 date/字符串键不一致。"""
    path = str(tmp_path / "ootd.yaml")
    data = load_ootd_cache(path)
    assert data["ootd"] == {}

    put_cached_outfit(
        data,
        "persona_hash_demo",
        datetime.date(2026, 8, 23),
        {"outfit_style": "运动活力风", "outfit": "一件黑色速干T恤，搭灰色运动长裤和白色跑鞋。"},
    )
    assert save_ootd_cache(path, data) is True

    reloaded = load_ootd_cache(path)
    entry = get_cached_outfit(reloaded, "persona_hash_demo", "2026-08-23")
    assert entry["outfit_style"] == "运动活力风"
    assert get_cached_outfit(reloaded, "persona_hash_demo", "2026-08-24") is None


def test_prune_drops_out_of_window(tmp_path):
    """守：缓存无限增长（过期条目永不被清理）。"""
    path = str(tmp_path / "ootd.yaml")
    data = load_ootd_cache(path)
    put_cached_outfit(data, "p1", "2026-07-01", {"outfit_style": "a", "outfit": "b"})
    put_cached_outfit(data, "p1", "2026-08-23", {"outfit_style": "c", "outfit": "d"})
    put_cached_outfit(data, "p2", "2026-08-22", {"outfit_style": "e", "outfit": "f"})

    prune_outfit_cache(data, datetime.date(2026, 8, 23), retention_days=30)
    assert get_cached_outfit(data, "p1", "2026-07-01") is None
    assert get_cached_outfit(data, "p1", "2026-08-23") is not None
    assert get_cached_outfit(data, "p2", "2026-08-22") is not None


def test_outfit_prompt_keeps_protocol_blocks():
    """守：prompt 数据块标签被改（模型收到错误协议）与缺上下文时降级占位丢失。"""
    prompt = build_outfit_prompt(
        persona_prompt="温柔可靠的大姐姐",
        today=datetime.date(2026, 8, 23),
        theme="演习日",
        style="活力",
        slots_text="08:00-12:00 晨训",
        style_pool=["运动活力风", "极简都市风"],
    )
    for tag, value in (
        ("DATE", "2026-08-23（星期日）"),
        ("PERSONA", "温柔可靠的大姐姐"),
        ("TODAY_BOUNDARY", "主题：演习日；状态色彩：活力"),
        ("TODAY_SLOTS", "08:00-12:00 晨训"),
        ("STYLE_POOL", "运动活力风/极简都市风"),
    ):
        assert f"<{tag}>{value}</{tag}>" in prompt
    assert "<WEATHER>" not in prompt  # 不接天气

    degraded = build_outfit_prompt(
        persona_prompt="", today=datetime.date(2026, 8, 23)
    )
    assert "<TODAY_BOUNDARY>无今日主题数据</TODAY_BOUNDARY>" in degraded
    assert "<TODAY_SLOTS>无今日日程数据</TODAY_SLOTS>" in degraded
    assert "<PERSONA>无（按通用设定生成）</PERSONA>" in degraded
    assert f"<STYLE_POOL>{'/'.join(DEFAULT_OUTFIT_STYLE_POOL)}</STYLE_POOL>" in degraded


def test_validate_outfit_rejects_bad_output():
    """守：不合规输出被接受并落库（脏数据进缓存，界面上不报错）。"""
    assert validate_outfit({"outfit_style": "运动活力风", "outfit": "黑" * 60}) == []
    assert "缺少 outfit_style 字段" in validate_outfit({"outfit": "x" * 60})
    assert "缺少 outfit 字段" in validate_outfit({"outfit_style": "x"})
    assert any(
        "长度" in reason
        for reason in validate_outfit({"outfit_style": "x", "outfit": "太短"})
    )


def test_standalone_persona_hash_stable_and_anonymous():
    """守：缓存键漂移（同一 persona 键变化 → 缓存永不命中）与原始 persona_id 泄漏。"""
    first = standalone_persona_hash("persona_demo")
    assert first == standalone_persona_hash("persona_demo")
    assert first.startswith("rand_")
    assert "persona_demo" not in first
    assert standalone_persona_hash("other") != first

    try:
        standalone_persona_hash("")
    except ValueError:
        pass
    else:
        raise AssertionError("空 persona_id 应抛 ValueError")


def test_pick_random_boundary_respects_slots_count():
    """守：random_slots_count 被忽略（配置键无可见症状地失效）。"""
    rng = random.Random(7)
    theme, style, slots = pick_random_boundary(
        DEFAULT_RANDOM_THEME_POOL,
        DEFAULT_OUTFIT_STYLE_POOL,
        DEFAULT_RANDOM_SLOT_POOL,
        slots_count=2,
        rng=rng,
    )
    assert theme in DEFAULT_RANDOM_THEME_POOL
    assert style in DEFAULT_OUTFIT_STYLE_POOL
    assert len(slots) == 2
    assert all(slot["name"] in DEFAULT_RANDOM_SLOT_POOL for slot in slots)

    _, _, none = pick_random_boundary([], [], [], slots_count=3, rng=rng)
    assert none == []
