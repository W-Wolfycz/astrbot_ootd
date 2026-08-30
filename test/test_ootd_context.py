import datetime

from astrbot_ootd.ootd_context import (
    extract_boundary_fields,
    find_today_snapshot,
    ootd_ready_minute,
    render_slots_text,
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


def test_find_today_snapshot_matches_persona_and_date_ignoring_tz():
    store = _FakeStore(
        [
            _snapshot("persona_abc", "2026-08-23", timezone="Asia/Shanghai"),
            _snapshot("persona_abc", "2026-08-23", timezone="UTC"),
            _snapshot("persona_other", "2026-08-23"),
        ]
    )
    found = find_today_snapshot(store, "persona_abc", datetime.date(2026, 8, 23))
    assert found is not None
    assert found["timezone"] == "Asia/Shanghai"  # 取第一个命中

    assert find_today_snapshot(store, "persona_abc", "2026-08-24") is None
    assert find_today_snapshot(store, "persona_other", "2026-08-23") is not None


def test_find_today_snapshot_none_store_or_empty():
    assert find_today_snapshot(None, "p", "2026-08-23") is None
    assert find_today_snapshot(_FakeStore([]), "p", "2026-08-23") is None


def test_extract_boundary_fields_reads_theme_style_and_slots():
    snapshot = {
        "boundary_state": {"daily_theme": "演习日", "daily_style": "活力"},
        "ai_slots": [{"start": "08:00", "end": "12:00", "name": "晨训"}],
    }
    theme, style, slots = extract_boundary_fields(snapshot)
    assert theme == "演习日"
    assert style == "活力"
    assert len(slots) == 1


def test_extract_boundary_fields_falls_back_to_slots_and_empty():
    assert extract_boundary_fields({}) == (None, None, [])
    assert extract_boundary_fields(None) == (None, None, [])
    theme, style, slots = extract_boundary_fields(
        {"boundary_state": {}, "slots": [{"name": "外出"}]}
    )
    assert theme is None and style is None
    assert slots == [{"name": "外出"}]


def test_render_slots_text_formats_and_truncates():
    slots = [
        {"start": "08:00", "end": "12:00", "name": "晨训", "state": "正在训练"},
        {"start": "12:00", "end": "13:00", "name": "午餐"},
    ]
    text = render_slots_text(slots)
    assert "08:00-12:00 晨训 正在训练" in text
    assert "12:00-13:00 午餐" in text

    many = [{"start": f"{h:02d}:00", "end": f"{h:02d}:30", "name": f"时段{h}"} for h in range(20)]
    truncated = render_slots_text(many, limit=12)
    assert "仅展示前 12 段" in truncated
    assert render_slots_text([]) == ""


def test_ootd_ready_minute_positive_adds_five_minutes():
    assert ootd_ready_minute("00:05") == 10
    assert ootd_ready_minute("12:00") == 12 * 60 + 5
    assert ootd_ready_minute("8:00") == 8 * 60 + 5  # 允许省略前导零
    assert ootd_ready_minute("23:59") == 4  # 23:59+5 跨天取模


def test_ootd_ready_minute_negative_means_midnight():
    assert ootd_ready_minute("-23:30") == 0
    assert ootd_ready_minute("-00:05") == 0


def test_ootd_ready_minute_missing_or_invalid_falls_back_to_default():
    assert ootd_ready_minute(None) == 10  # 回退 00:05 + 5
    assert ootd_ready_minute("") == 10
    assert ootd_ready_minute("garbage") == 10
    assert ootd_ready_minute("25:99") == 10
