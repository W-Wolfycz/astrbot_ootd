import asyncio
import datetime

from astrbot_ootd.ootd_standalone import (
    extract_persona_prompt,
    resolve_standalone_identity,
    resolve_standalone_persona,
)


class _FakeConversation:
    def __init__(self, persona_id):
        self.persona_id = persona_id


class _FakeConversationManager:
    def __init__(self, conversation_persona_id=None):
        self._persona_id = conversation_persona_id
        self.last_umo = None

    async def get_curr_conversation_id(self, umo):
        self.last_umo = umo
        return "conv_demo" if self._persona_id is not None else None

    async def get_conversation(self, umo, conversation_id):
        return _FakeConversation(self._persona_id)


class _FakePlatform:
    def __init__(self, name):
        self._name = name

    def meta(self):
        return type("Meta", (), {"name": self._name})()


class _FakePersonaManager:
    def __init__(self, resolved, persona):
        self._resolved = resolved
        self._persona = persona
        self.last_kwargs = None

    async def resolve_selected_persona(self, **kwargs):
        self.last_kwargs = kwargs
        return self._resolved, self._persona, None, False


class _FakeContext:
    def __init__(self, conversation_persona_id=None, resolved=None, persona=None):
        self.conversation_manager = _FakeConversationManager(conversation_persona_id)
        self.persona_manager = _FakePersonaManager(resolved, persona)

    def get_config(self, umo=None):
        return {"provider_settings": {"default_personality": "default_demo"}}

    def get_platform_inst(self, platform_id):
        return _FakePlatform("aiocqhttp")


def _resolve(context, umo="aiocqhttp:GroupMessage:10001"):
    return asyncio.run(resolve_standalone_persona(context, umo, event=None))


def test_resolve_standalone_persona_returns_resolved_id_and_persona():
    persona = {"prompt": "温柔可靠的大姐姐"}
    context = _FakeContext(conversation_persona_id="conv_persona", resolved="p_demo", persona=persona)
    persona_id, out = _resolve(context)
    assert persona_id == "p_demo"
    assert out is persona


def test_resolve_standalone_persona_passes_conversation_persona_id():
    context = _FakeContext(conversation_persona_id="conv_persona", resolved="p_demo", persona={})
    _resolve(context)
    kwargs = context.persona_manager.last_kwargs
    assert kwargs["conversation_persona_id"] == "conv_persona"
    assert kwargs["provider_settings"] == {"default_personality": "default_demo"}
    assert kwargs["platform_name"] == "aiocqhttp"


def test_resolve_standalone_persona_none_when_no_conversation():
    context = _FakeContext(conversation_persona_id=None, resolved="p_demo", persona={})
    _resolve(context)
    assert context.persona_manager.last_kwargs["conversation_persona_id"] is None


def test_resolve_standalone_persona_normalizes_none_marker():
    context = _FakeContext(conversation_persona_id="[%None]", resolved="p_demo", persona={})
    _resolve(context)
    assert context.persona_manager.last_kwargs["conversation_persona_id"] is None


def test_resolve_standalone_persona_returns_empty_on_none_resolved():
    for resolved in (None, "[%None]", ""):
        context = _FakeContext(conversation_persona_id=None, resolved=resolved, persona={})
        persona_id, persona = _resolve(context)
        assert persona_id == ""
        assert persona is None


def test_resolve_standalone_identity_builds_anonymous_context():
    context = _FakeContext(conversation_persona_id=None, resolved="p_demo", persona={"prompt": "设定"})
    now = datetime.datetime(2026, 8, 23, 12, 0)
    ctx = asyncio.run(
        resolve_standalone_identity(context, "aiocqhttp:GroupMessage:10001", event=None, now=now)
    )
    assert ctx is not None
    assert ctx.persona_hash.startswith("rand_")
    assert "p_demo" not in ctx.persona_hash
    assert ctx.persona_prompt == "设定"
    assert ctx.today == "2026-08-23"


def test_extract_persona_prompt_handles_dict_and_none():
    assert extract_persona_prompt({"prompt": " x "}) == "x"
    assert extract_persona_prompt(None) == ""
    assert extract_persona_prompt(_FakeConversation("ignored")) == ""
