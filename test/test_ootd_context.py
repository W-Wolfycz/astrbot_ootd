"""OOTD 时笺上下文适配的纯函数单测。

守的是：快照匹配字段/忽略时区语义被改（读不到快照 → 静默降级为无主题生成）、
时笺快照字段键映射被改（主题/状态色彩/日程静默为空）、就绪时刻算错（生成时机静默偏移）。
"""

import datetime

from astrbot_ootd.ootd_context import (
    extract_boundary_fields,
    find_today_snapshot,
    ootd_ready_minute,
)


class _FakeStore:
    """只提供 list_snapshots() 的假 store（对应时笺只读枚举 API）。"""

    def __init__(self, snapshots):
        self._snapshots = snapshots

    def list_snapshots(self):
        return self._snapshots


def _snapshot(persona_hash, local_date, timezone="system-local", **extra):
    snap = {
        "persona_hash": persona_hash,
        "local_date": local_date,
        "timezone": timezone,
        "status": "ready",
    }
    snap.update(extra)
    return snap


def test_find_today_snapshot_matches_persona_and_date():
    """守：匹配字段名/忽略时区语义被改（快照读不到 → 静默无主题生成）。"""
    store = _FakeStore(
        [
            _snapshot("persona_abc", "2026-08-23", timezone="Asia/Shanghai"),
            _snapshot("persona_abc", "2026-08-23", timezone="UTC"),
            _snapshot("persona_other", "2026-08-23"),
        ]
    )
    found = find_today_snapshot(store, "persona_abc", datetime.date(2026, 8, 23))
    assert found is not None
    assert found["timezone"] == "Asia/Shanghai"  # 忽略时区，取第一个命中

    assert find_today_snapshot(store, "persona_abc", "2026-08-24") is None
    assert find_today_snapshot(None, "persona_abc", "2026-08-23") is None


def test_extract_boundary_fields_maps_snapshot_keys():
    """守：时笺快照字段键映射被改（主题/状态色彩/日程静默为空）。"""
    theme, style, slots = extract_boundary_fields(
        {
            "boundary_state": {"daily_theme": "演习日", "daily_style": "活力"},
            "ai_slots": [{"start": "08:00", "end": "12:00", "name": "晨训"}],
        }
    )
    assert (theme, style) == ("演习日", "活力")
    assert slots == [{"start": "08:00", "end": "12:00", "name": "晨训"}]

    theme, style, slots = extract_boundary_fields(
        {"boundary_state": {}, "slots": [{"name": "外出"}]}
    )
    assert theme is None and style is None
    assert slots == [{"name": "外出"}]


def test_ootd_ready_minute_handles_signs_and_fallback():
    """守：就绪时刻算错（生成时机静默偏移或跨天错位）。"""
    assert ootd_ready_minute("12:00") == 12 * 60 + 5
    assert ootd_ready_minute("23:59") == 4  # 跨天取模
    assert ootd_ready_minute("-23:30") == 0
    assert ootd_ready_minute(None) == 10  # 缺省回退 00:05 + 5
    assert ootd_ready_minute("garbage") == 10
