"""astrbot_ootd — 每日穿搭（穿什么？）

两种数据来源模式（``mode`` 配置切换）：

- ``time_awareness``：外接 time_awareness（时笺），复用其每日主题/风格/日程时段
  （daily_theme / daily_style / ai_slots）作为穿搭输入——先有「今天是什么日子、
  什么基调」，再推「今天穿什么」。
- ``random``：不依赖时笺，随机挑选主题/状态色彩/当日日程自生成。

- 每日 cron：``time_awareness`` 模式读取时笺 ``ai_daily.generation_time``，到就绪时刻
  （HH:MM+5 或 0 点）后判一次时笺当天快照是否就绪，未就绪轮询等待、就绪即生成；
  ``random`` 模式每天 0 点直接生成；每会话每天一次。
- 注入：``on_llm_request(priority=-260)`` 把 ``<OOTD>…</OOTD>`` 追加到本轮临时内容。
- 不接天气：提示词要求「当季通配」。
- 缓存：``plugin_data/astrbot_ootd/ootd.yaml``（Persona 匿名键，30 天，不落原始 ID）。
"""

import asyncio
import datetime as dt
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart

from .ootd import (
    DEFAULT_OUTFIT_STYLE_POOL,
    DEFAULT_RANDOM_SLOT_POOL,
    DEFAULT_RANDOM_THEME_POOL,
    OOTD_CACHE_FILE_NAME,
    OOTD_RETENTION_DAYS,
    OOTD_SYSTEM_PROMPT,
    build_outfit_prompt,
    get_cached_outfit,
    load_ootd_cache,
    normalize_outfit_entry,
    parse_outfit_response,
    pick_random_boundary,
    prune_outfit_cache,
    put_cached_outfit,
    save_ootd_cache,
    validate_outfit,
)
from .ootd_context import (
    has_today_snapshot,
    ootd_ready_minute,
    read_outfit_snapshot,
    render_slots_text,
    resolve_now,
    resolve_ootd_identity,
)
from .ootd_standalone import resolve_standalone_identity


@register(
    "astrbot_ootd",
    "Wolfycz",
    "每日穿搭 OOTD：告诉角色今天穿什么（外接 time_awareness 或随机自生成）",
    "1.0.3",
    "https://github.com/W-Wolfycz/astrbot_ootd",
)
class OotdPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._ootd_cache: dict | None = None
        self._ootd_sessions: set[str] = set()
        self._ootd_attempted: set[tuple[str, str]] = set()
        self._ootd_tasks: set[asyncio.Task] = set()
        self._ootd_daily_task: asyncio.Task | None = None
        logger.info(
            f"[astrbot_ootd] loaded | enabled={self._ootd_enabled()} "
            f"mode={self._ootd_mode()}"
        )

    async def initialize(self):
        """启动 OOTD 每日生成循环。"""
        if self._ootd_enabled():
            self._ootd_daily_task = asyncio.create_task(self._ootd_daily_loop())

    async def terminate(self):
        """停止 OOTD 每日循环与生成任务。"""
        if self._ootd_daily_task is not None:
            self._ootd_daily_task.cancel()
            try:
                await self._ootd_daily_task
            except asyncio.CancelledError:
                pass
            self._ootd_daily_task = None
        for task in list(self._ootd_tasks):
            task.cancel()
        if self._ootd_tasks:
            await asyncio.gather(*self._ootd_tasks, return_exceptions=True)
        self._ootd_tasks.clear()
        self._ootd_attempted.clear()

    # ── 注入 Hook ─────────────────────────────────────────────────────

    @filter.on_llm_request(priority=-260)
    async def inject_ootd(self, event: AstrMessageEvent, req: ProviderRequest):
        """把当日 OOTD 追加到本轮临时内容（priority=-260，晚于时笺 -150 与工具移除 -250）。"""
        if not self._ootd_enabled():
            return
        umo = getattr(event, "unified_msg_origin", "") or ""
        if umo:
            self._ootd_sessions.add(umo)
        try:
            outfit = await self._ootd_for_round(event)
        except Exception as exc:
            logger.debug(f"[astrbot_ootd] OOTD 注入失败: {type(exc).__name__}: {exc}")
            return
        if not outfit or not self._ootd_inject_enabled():
            return
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            parts = []
            req.extra_user_content_parts = parts
        block = (
            f"<OOTD>今日穿搭：{outfit}（角色可自然提及或回应相关话题时带出，不要每轮复读）</OOTD>"
        )
        parts.append(TextPart(text=block).mark_as_temp())

    # ── 命令 ─────────────────────────────────────────────────────────

    @filter.command("ootd")
    async def query_ootd(self, event: AstrMessageEvent, sub: str = ""):
        """查看今日穿搭；`/ootd new` 强制重新生成。"""
        if not self._ootd_enabled():
            yield event.plain_result("OOTD 功能未启用。")
            return
        umo = getattr(event, "unified_msg_origin", "") or ""
        ctx = await self._resolve_identity(umo, event)
        if ctx is None:
            yield event.plain_result("无法解析当前角色，暂时无法提供穿搭。")
            return
        force_new = str(sub or "").strip().lower() == "new"
        entry = None
        if not force_new:
            entry = get_cached_outfit(
                self._ootd_cache_data(), ctx.persona_hash, ctx.today
            )
        if force_new or not (entry and entry.get("outfit")):
            theme, style, slots = self._ootd_outfit_inputs_immediate(ctx)
            try:
                entry = await self._generate_and_cache(ctx, umo, theme, style, slots)
            except Exception as exc:
                logger.debug(
                    f"[astrbot_ootd] 命令生成异常: {type(exc).__name__}: {exc}"
                )
                entry = None
        if not entry or not entry.get("outfit"):
            yield event.plain_result("今日穿搭生成失败，请稍后再试。")
            return
        style_text = entry.get("outfit_style") or "穿搭"
        yield event.plain_result(f"今日穿搭（{style_text}）：\n{entry.get('outfit', '')}")

    async def _ootd_for_round(self, event: AstrMessageEvent) -> str | None:
        """返回本轮可注入的 OOTD 文本；缓存未命中时触发后台补生成。"""
        umo = getattr(event, "unified_msg_origin", "") or ""
        ctx = await self._resolve_identity(umo, event)
        if ctx is None:
            return None
        entry = get_cached_outfit(self._ootd_cache_data(), ctx.persona_hash, ctx.today)
        if entry and entry.get("outfit"):
            return entry["outfit"]
        self._spawn_ootd_generation(ctx, umo)
        return None

    async def _resolve_identity(self, umo: str, event):
        """按当前模式解析 Persona 身份上下文；随机模式不依赖时笺。"""
        if self._ootd_mode() == "random":
            return await resolve_standalone_identity(
                self.context, umo, event, now=resolve_now(None)
            )
        return await resolve_ootd_identity(
            self.context,
            umo,
            event,
            store=self._ootd_ta_store(),
            ta_instance=self._resolve_time_awareness(),
        )

    def _spawn_ootd_generation(self, ctx, umo: str) -> None:
        """后台生成（每 Persona 每天最多尝试一次，失败当日放弃、次日重试）。"""
        key = (ctx.persona_hash, ctx.today)
        if key in self._ootd_attempted:
            return
        self._ootd_attempted.add(key)
        task = asyncio.create_task(self._generate_ootd(ctx, umo))
        self._ootd_tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._ootd_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.debug(
                    f"[astrbot_ootd] OOTD 后台生成异常: {type(t.exception()).__name__}"
                )

        task.add_done_callback(_done)

    async def _generate_ootd(self, ctx, umo: str) -> None:
        """cron/后台生成：TA 模式等待时笺就绪后读快照，再走生成+缓存。"""
        try:
            theme, style, slots = await self._ootd_outfit_inputs(ctx)
            await self._generate_and_cache(ctx, umo, theme, style, slots)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(f"[astrbot_ootd] OOTD 生成异常: {type(exc).__name__}: {exc}")

    async def _generate_and_cache(
        self,
        ctx,
        umo: str,
        theme,
        style,
        slots,
    ) -> dict | None:
        """构造 prompt → LLM 生成 → 校验 → 写缓存；返回 entry 或 None。"""
        prompt = build_outfit_prompt(
            persona_prompt=ctx.persona_prompt,
            today=ctx.date,
            theme=theme,
            style=style,
            slots_text=render_slots_text(slots),
            style_pool=self._ootd_style_pool(),
        )
        provider_id = await self._ootd_provider_id(umo)
        if not provider_id:
            logger.debug("[astrbot_ootd] OOTD 无法确定 provider，放弃当日生成")
            return None
        entry = await self._llm_outfit(prompt, provider_id)
        if entry is None:
            logger.debug("[astrbot_ootd] OOTD 生成失败，放弃当日")
            return None
        cache = self._ootd_cache_data()
        put_cached_outfit(cache, ctx.persona_hash, ctx.today, entry)
        prune_outfit_cache(cache, ctx.date, OOTD_RETENTION_DAYS)
        save_ootd_cache(str(self._ootd_cache_path()), cache)
        logger.info(
            f"[astrbot_ootd] OOTD 已生成: persona={ctx.short_hash()} "
            f"style={entry.get('outfit_style')} len={len(entry.get('outfit', ''))}"
        )
        return entry

    async def _ootd_outfit_inputs(self, ctx) -> tuple[str | None, str | None, list[dict]]:
        """按模式获取主题/状态色彩/日程：随机模式随机挑选，TA 模式读时笺快照。"""
        if self._ootd_mode() == "random":
            return pick_random_boundary(
                theme_pool=self._ootd_random_theme_pool(),
                style_pool=self._ootd_style_pool(),
                slots_pool=self._ootd_random_slots_pool(),
                slots_count=self._ootd_random_slots_count(),
            )
        await self._ootd_wait_until_ready()
        return await self._ootd_read_snapshot_with_grace(ctx)

    def _ootd_outfit_inputs_immediate(
        self, ctx
    ) -> tuple[str | None, str | None, list[dict]]:
        """立即获取主题/状态色彩/日程（命令用）：TA 模式只读一次快照、不等待。"""
        if self._ootd_mode() == "random":
            return pick_random_boundary(
                theme_pool=self._ootd_random_theme_pool(),
                style_pool=self._ootd_style_pool(),
                slots_pool=self._ootd_random_slots_pool(),
                slots_count=self._ootd_random_slots_count(),
            )
        return read_outfit_snapshot(
            self._ootd_ta_store(), ctx.persona_hash, ctx.today
        )

    async def _llm_outfit(self, prompt: str, provider_id: str) -> dict | None:
        """带原因重写 1 次；两次都不合协议则返回 None（当日放弃）。"""
        current = prompt
        for _attempt in range(2):
            try:
                resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=current,
                    system_prompt=OOTD_SYSTEM_PROMPT,
                )
            except Exception as exc:
                logger.debug(f"[astrbot_ootd] OOTD LLM 调用失败: {type(exc).__name__}: {exc}")
                return None
            if not resp or getattr(resp, "role", "") != "assistant":
                return None
            text = (getattr(resp, "completion_text", "") or "").strip()
            if not text:
                return None
            entry = parse_outfit_response(text)
            reasons = validate_outfit(entry) if entry is not None else ["JSON 解析失败"]
            if entry is not None and not reasons:
                return normalize_outfit_entry(entry)
            current = (
                f"{prompt}\n\n上次输出不符合要求（{'；'.join(reasons)}），"
                "请严格按 JSON 协议重新输出。"
            )
        return None

    async def _ootd_provider_id(self, umo: str) -> str:
        provider_id = (self._ootd_config().get("provider_id") or "").strip()
        if provider_id:
            return provider_id
        try:
            return (await self.context.get_current_chat_provider_id(umo=umo) or "").strip()
        except Exception as exc:
            logger.debug(f"[astrbot_ootd] OOTD 获取会话 provider 失败: {type(exc).__name__}: {exc}")
            return ""

    def _ootd_ready_minute(self) -> int | None:
        """计算 OOTD 生成就绪时刻；时笺不生成快照时返回 None（立即生成、仅日期+人设）。

        - ``HH:MM`` → ``HH:MM+5``（当天分钟数）
        - ``-HH:MM`` → 0（次日 0 点就绪）
        - 时笺未启用 AI 日程 → None
        """
        generation_time = None
        ta = self._resolve_time_awareness()
        if ta is not None:
            try:
                daily = (getattr(ta, "config", {}) or {}).get("daily_schedule", {})
                ai_daily = daily.get("ai_daily", {}) if isinstance(daily, dict) else {}
                enabled = (
                    isinstance(daily, dict)
                    and bool(daily.get("enable_schedule", False))
                    and isinstance(ai_daily, dict)
                    and bool(ai_daily.get("enabled", False))
                )
                if enabled and isinstance(ai_daily, dict):
                    generation_time = str(ai_daily.get("generation_time", "") or "")
            except Exception:
                generation_time = None
        if generation_time is None:
            return None
        return ootd_ready_minute(generation_time)

    async def _ootd_wait_until_ready(self) -> None:
        """等待到时笺生成就绪时刻（HH:MM+5 或 0 点）后再读快照。"""
        ready_minute = self._ootd_ready_minute()
        if ready_minute is None:
            return
        now = resolve_now(self._resolve_time_awareness())
        ready = now.replace(
            hour=ready_minute // 60,
            minute=ready_minute % 60,
            second=0,
            microsecond=0,
        )
        if ready <= now:
            return
        logger.debug(
            f"[astrbot_ootd] OOTD 等待时笺就绪，睡到 {ready.strftime('%H:%M')}"
        )
        await asyncio.sleep((ready - now).total_seconds())

    async def _ootd_read_snapshot_with_grace(
        self, ctx
    ) -> tuple[str | None, str | None, list[dict]]:
        """就绪时刻后短轮询读快照；时笺未按时生成则超时降级（无主题/风格）。"""
        if self._ootd_ready_minute() is None:
            return None, None, []
        store = self._ootd_ta_store()
        for _attempt in range(10):
            if has_today_snapshot(store, ctx.persona_hash, ctx.today):
                return read_outfit_snapshot(store, ctx.persona_hash, ctx.today)
            await asyncio.sleep(60)
        return None, None, []

    async def _ootd_daily_loop(self) -> None:
        """每日 cron：到就绪时刻后为已知会话各生成一次当天 OOTD。

        ``time_awareness`` 模式在时笺就绪时刻触发；``random`` 模式每天 0 点直接生成。
        """
        try:
            while True:
                ready_minute = (
                    0
                    if self._ootd_mode() == "random"
                    else (self._ootd_ready_minute() or 0)
                )
                now = (
                    resolve_now(None)
                    if self._ootd_mode() == "random"
                    else resolve_now(self._resolve_time_awareness())
                )
                next_run = now.replace(
                    hour=ready_minute // 60,
                    minute=ready_minute % 60,
                    second=0,
                    microsecond=0,
                )
                if next_run <= now:
                    next_run += dt.timedelta(days=1)
                await asyncio.sleep(max(1.0, (next_run - now).total_seconds()))
                await self._ootd_generate_known_sessions()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                f"[astrbot_ootd] OOTD 每日循环异常: {type(exc).__name__}: {exc}"
            )

    async def _ootd_generate_known_sessions(self) -> None:
        """为已知会话（Persona）各生成一次当天 OOTD（缓存/去重兜底）。"""
        sessions = list(self._ootd_sessions)
        if not sessions:
            return
        generated = 0
        for umo in sessions:
            try:
                if await self._ootd_generate_for_session(umo):
                    generated += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(
                    f"[astrbot_ootd] OOTD 为会话生成失败: {type(exc).__name__}: {exc}"
                )
        if generated:
            logger.info(
                f"[astrbot_ootd] OOTD 每日扫描完成: generated={generated} sessions={len(sessions)}"
            )

    async def _ootd_generate_for_session(self, umo: str) -> bool:
        """为单个会话解析 Persona 并生成当天 OOTD；已缓存/已尝试则跳过。"""
        ctx = await self._resolve_identity(umo, None)
        if ctx is None:
            return False
        entry = get_cached_outfit(self._ootd_cache_data(), ctx.persona_hash, ctx.today)
        if entry and entry.get("outfit"):
            return False
        key = (ctx.persona_hash, ctx.today)
        if key in self._ootd_attempted:
            return False
        self._ootd_attempted.add(key)
        await self._generate_ootd(ctx, umo)
        return True

    # ── 配置/缓存 helpers ─────────────────────────────────────────────

    def _ootd_config(self) -> dict:
        return self.config if isinstance(self.config, dict) else {}

    def _ootd_enabled(self) -> bool:
        return bool(self._ootd_config().get("enabled", False))

    def _ootd_inject_enabled(self) -> bool:
        return bool(self._ootd_config().get("inject_enabled", True))

    def _ootd_mode(self) -> str:
        """返回数据来源模式：``time_awareness``（外接时笺，默认）或 ``random``（随机自生成）。"""
        mode = str(self._ootd_config().get("mode", "") or "").strip().lower()
        if mode in ("time_awareness", "random"):
            return mode
        return "time_awareness"

    def _ootd_random_theme_pool(self) -> list[str]:
        raw = self._ootd_config().get("random_theme_pool", [])
        if not isinstance(raw, list):
            return list(DEFAULT_RANDOM_THEME_POOL)
        pool = [str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()]
        return pool or list(DEFAULT_RANDOM_THEME_POOL)

    def _ootd_random_slots_pool(self) -> list[str]:
        raw = self._ootd_config().get("random_slots_pool", [])
        if not isinstance(raw, list):
            return list(DEFAULT_RANDOM_SLOT_POOL)
        return [str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()]

    def _ootd_random_slots_count(self) -> int:
        raw = self._ootd_config().get("random_slots_count", 3)
        try:
            return max(0, min(6, int(raw)))
        except (TypeError, ValueError):
            return 3

    def _ootd_style_pool(self) -> list[str]:
        raw = self._ootd_config().get("outfit_style_pool", [])
        if not isinstance(raw, list):
            return list(DEFAULT_OUTFIT_STYLE_POOL)
        pool = [str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()]
        return pool or list(DEFAULT_OUTFIT_STYLE_POOL)

    def _ootd_cache_path(self) -> Path:
        try:
            base = str(StarTools.get_data_dir("astrbot_ootd"))
        except Exception:
            base = str(
                Path(self.context.get_config().get("plugin.data_dir", "./data"))
                / "plugin_data"
                / "astrbot_ootd"
            )
        return Path(base) / OOTD_CACHE_FILE_NAME

    def _ootd_ta_store(self):
        """从 time_awareness 实例取公开的 daily_schedule_store（不直接 import 私有模块）。"""
        ta = self._resolve_time_awareness()
        for candidate in (ta, getattr(ta, "star", None) if ta is not None else None):
            if candidate is None:
                continue
            store = getattr(candidate, "daily_schedule_store", None)
            if store is not None and callable(getattr(store, "get", None)):
                return store
        return None

    def _ootd_cache_data(self) -> dict:
        if self._ootd_cache is None:
            self._ootd_cache = load_ootd_cache(str(self._ootd_cache_path()))
        return self._ootd_cache

    def _resolve_time_awareness(self):
        """返回已激活的 time_awareness 实例（供读取其配置与运行时 now）。"""
        try:
            star = self.context.get_registered_star("time_awareness")
            if star is None or not bool(getattr(star, "activated", True)):
                return None
            for candidate in (
                star,
                getattr(star, "star", None),
                getattr(star, "star_cls", None),
            ):
                if candidate is not None and callable(
                    getattr(candidate, "create_external_task", None)
                ):
                    return candidate
        except Exception as exc:
            logger.debug(f"[astrbot_ootd] 查询 time_awareness 失败: {exc}")
        return None
