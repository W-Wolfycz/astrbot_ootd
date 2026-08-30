"""OOTD 独立（随机）模式的 Persona 解析，不依赖 time_awareness。

随机模式复用 AstrBot 自带的 PersonaManager / ConversationManager 解析当前生效
Persona：session 强制 > conversation > provider 默认。身份键用
``standalone_persona_hash`` 生成稳定匿名键，缓存不落原始 persona_id；本模块
不 import 时笺任何符号，时笺未安装时随机模式仍可用。
"""

from __future__ import annotations

import datetime
from typing import Any

from .ootd import standalone_persona_hash
from .ootd_context import OutfitContext


def _platform_name(context: Any, umo: str, event: Any = None) -> str:
    """取得与 ``event.get_platform_name()`` 一致的平台名称。"""
    if event is not None:
        try:
            return (event.get_platform_name() or "").strip()
        except Exception:
            pass

    platform_id = umo.split(":", 1)[0] if umo else ""
    if not platform_id:
        return ""
    try:
        platform = context.get_platform_inst(platform_id)
        if platform is not None:
            return (platform.meta().name or "").strip()
    except Exception:
        pass
    return platform_id


async def resolve_standalone_persona(
    context: Any,
    umo: str,
    event: Any = None,
) -> tuple[str, Any | None]:
    """按「session 强制 > conversation > provider 默认」解析当前生效 Persona。

    ``None`` / ``[%None]`` / 异常返回 ``("", None)``；没有 conversation id 时
    自然降级到会话配置默认值，不主动创建 conversation。
    """
    try:
        conversation_persona_id = None
        try:
            conversation_manager = context.conversation_manager
            conversation_id = await conversation_manager.get_curr_conversation_id(umo)
            if conversation_id:
                conversation = await conversation_manager.get_conversation(
                    umo,
                    conversation_id,
                )
                conversation_persona_id = (
                    getattr(conversation, "persona_id", None) or None
                )
        except Exception:
            conversation_persona_id = None

        config = context.get_config(umo=umo) or {}
        provider_settings = config.get("provider_settings", {}) or {}
        resolved, persona, _, _ = await context.persona_manager.resolve_selected_persona(
            umo=umo,
            conversation_persona_id=conversation_persona_id,
            platform_name=_platform_name(context, umo, event),
            provider_settings=provider_settings,
        )
        if not resolved or resolved == "[%None]":
            return "", None
        return str(resolved), persona
    except Exception:
        return "", None


def extract_persona_prompt(persona: Any | None) -> str:
    """从 ``resolve_selected_persona`` 返回对象中提取 prompt。"""
    if not persona:
        return ""
    if isinstance(persona, dict):
        return (persona.get("prompt") or "").strip()
    return (getattr(persona, "prompt", "") or "").strip()


async def resolve_standalone_identity(
    context: Any,
    umo: str,
    event: Any = None,
    *,
    now: datetime.datetime | None = None,
) -> OutfitContext | None:
    """解析随机模式的身份上下文（persona_hash + prompt + date），**不读时笺快照**。

    主题/状态色彩/日程由调用方用 ``pick_random_boundary`` 随机挑选。
    """
    persona_id, persona = await resolve_standalone_persona(context, umo, event)
    if not persona_id:
        return None
    prompt = extract_persona_prompt(persona)
    today = (now or datetime.datetime.now().astimezone()).date()
    return OutfitContext(
        persona_hash=standalone_persona_hash(persona_id),
        persona_prompt=prompt,
        date=today,
    )
