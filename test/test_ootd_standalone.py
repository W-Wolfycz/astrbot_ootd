"""OOTD 独立模式 persona 解析单测。

守的是：conversation persona 未透传（静默用错人）、"[%None]"/空 resolved 未判空
（静默把 "[%None]" 当人格继续生成）、身份上下文组装缺字段（prompt/日期/匿名键）。
"""

import asyncio
import datetime

from astrbot_ootd.ootd_standalone import (
    resolve_standalone_identity,
    resolve_standalone_persona,
)


class _FakeConversation:
    def __init__(self, persona_id):
        self.persona_id = persona_id


class _FakeConversationManager:
    def __init__(self, conversation_persona_id=None):
        self._persona_id = conversation_persona_id

    async def get_curr_conversation_id(self, umo):
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

    def get_platform_inst(self, platform_id):
        return _FakePlatform("aiocqhttp")


def _resolve(context, umo="aiocqhttp:GroupMessage:10001"):
    return asyncio.run(resolve_standalone_persona(context, umo, event=None))


def test_resolve_standalone_persona_priority_and_empty():
    """守：conversation persona 未透传（静默用错人）与 "[%None]"/空 resolved 未判空
    （静默把 "[%None]" 当人格继续生成）。"""
    persona = {"prompt": "温柔可靠的大姐姐"}
    context = _FakeContext(
        conversation_persona_id="conv_persona", resolved="p_demo", persona=persona
    )
    persona_id, out = _resolve(context)
    assert persona_id == "p_demo"
    assert out is persona
    assert context.persona_manager.last_kwargs["conversation_persona_id"] == "conv_persona"

    no_conv = _FakeContext(conversation_persona_id=None, resolved="p_demo", persona={})
    _resolve(no_conv)
    assert no_conv.persona_manager.last_kwargs["conversation_persona_id"] is None

    for resolved in (None, "[%None]", ""):
        ctx = _FakeContext(conversation_persona_id=None, resolved=resolved, persona={})
        assert _resolve(ctx) == ("", None)


def test_resolve_standalone_identity_assembles_context():
    """守：身份上下文组装缺字段（prompt/日期/匿名键静默为空或漂移）。"""
    context = _FakeContext(
        conversation_persona_id=None, resolved="p_demo", persona={"prompt": "设定"}
    )
    now = datetime.datetime(2026, 8, 23, 12, 0)
    ctx = asyncio.run(
        resolve_standalone_identity(
            context, "aiocqhttp:GroupMessage:10001", event=None, now=now
        )
    )
    assert ctx is not None
    assert ctx.persona_prompt == "设定"
    assert ctx.today == "2026-08-23"
    assert ctx.persona_hash.startswith("rand_")
    assert "p_demo" not in ctx.persona_hash
