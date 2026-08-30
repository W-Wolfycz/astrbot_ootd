import datetime
import random

from astrbot_ootd.ootd import (
    DEFAULT_OUTFIT_STYLE_POOL,
    DEFAULT_RANDOM_SLOT_POOL,
    DEFAULT_RANDOM_THEME_POOL,
    build_outfit_prompt,
    get_cached_outfit,
    load_ootd_cache,
    normalize_outfit_entry,
    parse_outfit_response,
    pick_random_boundary,
    prune_outfit_cache,
    put_cached_outfit,
    save_ootd_cache,
    standalone_persona_hash,
    validate_outfit,
)


def test_cache_round_trip(tmp_path):
    path = str(tmp_path / "ootd.yaml")
    data = load_ootd_cache(path)
    assert data["ootd"] == {}

    put_cached_outfit(
        data, "persona_hash_demo", "2026-08-23",
        {"outfit_style": "运动活力风", "outfit": "一件黑色速干T恤，搭灰色运动长裤和白色跑鞋。"},
    )
    assert save_ootd_cache(path, data) is True

    reloaded = load_ootd_cache(path)
    entry = get_cached_outfit(reloaded, "persona_hash_demo", "2026-08-23")
    assert entry["outfit_style"] == "运动活力风"
    assert get_cached_outfit(reloaded, "persona_hash_demo", "2026-08-24") is None


def test_cache_accepts_date_object_and_missing_file(tmp_path):
    path = str(tmp_path / "missing.yaml")
    data = load_ootd_cache(path)  # 文件不存在 → 空结构
    put_cached_outfit(data, "p1", datetime.date(2026, 8, 23), {"outfit_style": "x", "outfit": "y"})
    assert get_cached_outfit(data, "p1", "2026-08-23")["outfit"] == "y"


def test_prune_drops_out_of_window(tmp_path):
    path = str(tmp_path / "ootd.yaml")
    data = load_ootd_cache(path)
    put_cached_outfit(data, "p1", "2026-07-01", {"outfit_style": "a", "outfit": "b"})
    put_cached_outfit(data, "p1", "2026-08-23", {"outfit_style": "c", "outfit": "d"})
    put_cached_outfit(data, "p2", "2026-08-22", {"outfit_style": "e", "outfit": "f"})

    prune_outfit_cache(data, datetime.date(2026, 8, 23), retention_days=30)
    assert get_cached_outfit(data, "p1", "2026-07-01") is None
    assert get_cached_outfit(data, "p1", "2026-08-23") is not None
    assert get_cached_outfit(data, "p2", "2026-08-22") is not None


def test_build_outfit_prompt_includes_all_blocks():
    prompt = build_outfit_prompt(
        persona_prompt="温柔可靠的大姐姐",
        today=datetime.date(2026, 8, 23),
        theme="演习日",
        style="活力",
        slots_text="08:00-12:00 晨训",
        style_pool=["运动活力风", "极简都市风"],
    )
    assert "<DATE>2026-08-23（星期日）</DATE>" in prompt
    assert "<PERSONA>温柔可靠的大姐姐</PERSONA>" in prompt
    assert "<TODAY_BOUNDARY>主题：演习日；状态色彩：活力</TODAY_BOUNDARY>" in prompt
    assert "<TODAY_SLOTS>08:00-12:00 晨训</TODAY_SLOTS>" in prompt
    assert "<STYLE_POOL>运动活力风/极简都市风</STYLE_POOL>" in prompt
    assert "<WEATHER>" not in prompt  # 不接天气


def test_build_outfit_prompt_degrades_missing_context():
    prompt = build_outfit_prompt(
        persona_prompt="",
        today=datetime.date(2026, 8, 23),
        theme=None,
        style=None,
        slots_text="",
    )
    assert "<TODAY_BOUNDARY>无今日主题数据</TODAY_BOUNDARY>" in prompt
    assert "<TODAY_SLOTS>无今日日程数据</TODAY_SLOTS>" in prompt
    assert "<PERSONA>无（按通用设定生成）</PERSONA>" in prompt
    # 未传风格池时回退内置 12 条
    assert "<STYLE_POOL>" + "/".join(DEFAULT_OUTFIT_STYLE_POOL) + "</STYLE_POOL>" in prompt


def test_parse_outfit_response_handles_plain_and_fenced_json():
    assert parse_outfit_response('{"outfit_style":"x","outfit":"y"}') == {
        "outfit_style": "x",
        "outfit": "y",
    }
    assert parse_outfit_response('```json\n{"outfit_style":"x","outfit":"y"}\n```') == {
        "outfit_style": "x",
        "outfit": "y",
    }
    assert parse_outfit_response("不是 JSON") is None
    assert parse_outfit_response("[1, 2]") is None  # 数组不是对象


def test_validate_outfit_rules():
    valid = {"outfit_style": "运动活力风", "outfit": "黑" * 60}
    assert validate_outfit(valid) == []
    assert validate_outfit(None) == ["输出不是 JSON 对象"]
    assert "缺少 outfit_style 字段" in validate_outfit({"outfit": "x" * 60})
    assert "缺少 outfit 字段" in validate_outfit({"outfit_style": "x"})
    assert any("长度" in reason for reason in validate_outfit({"outfit_style": "x", "outfit": "太短"}))


def test_normalize_outfit_entry_strips_extra_fields():
    assert normalize_outfit_entry(
        {"outfit_style": " 运动活力风 ", "outfit": " 描述 ", "extra": "drop"}
    ) == {"outfit_style": "运动活力风", "outfit": "描述"}


def test_standalone_persona_hash_stable_anonymized_and_prefixed():
    h1 = standalone_persona_hash("persona_demo")
    h2 = standalone_persona_hash("persona_demo")
    assert h1 == h2
    assert h1.startswith("rand_")
    assert "persona_demo" not in h1
    assert standalone_persona_hash("other") != h1
    try:
        standalone_persona_hash("")
    except ValueError:
        pass
    else:
        raise AssertionError("空 persona_id 应抛 ValueError")


def test_pick_random_boundary_deterministic_with_seed():
    rng = random.Random(7)
    theme, style, slots = pick_random_boundary(
        DEFAULT_RANDOM_THEME_POOL,
        DEFAULT_OUTFIT_STYLE_POOL,
        DEFAULT_RANDOM_SLOT_POOL,
        slots_count=3,
        rng=rng,
    )
    assert theme in DEFAULT_RANDOM_THEME_POOL
    assert style in DEFAULT_OUTFIT_STYLE_POOL
    assert len(slots) == 3
    for slot in slots:
        assert slot["name"] in DEFAULT_RANDOM_SLOT_POOL


def test_pick_random_boundary_respects_count_and_empty_pools():
    rng = random.Random(1)
    _, _, two = pick_random_boundary(
        DEFAULT_RANDOM_THEME_POOL,
        DEFAULT_OUTFIT_STYLE_POOL,
        DEFAULT_RANDOM_SLOT_POOL,
        slots_count=2,
        rng=rng,
    )
    assert len(two) == 2

    _, _, none = pick_random_boundary(
        DEFAULT_RANDOM_THEME_POOL,
        DEFAULT_OUTFIT_STYLE_POOL,
        DEFAULT_RANDOM_SLOT_POOL,
        slots_count=0,
        rng=rng,
    )
    assert none == []

    theme, style, slots = pick_random_boundary(
        [],
        [],
        [],
        slots_count=3,
        rng=rng,
    )
    assert theme is None
    assert style is None
    assert slots == []
