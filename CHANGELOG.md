# 更新日志

## 1.0.4 — 2026-09-17

- **修复 time_awareness 探针**：`_resolve_time_awareness` 改用实际依赖（`config`/`time_context`/`daily_schedule_store`）判定实例，不再依赖已移除的 `create_external_task`，避免 TA 模式整体降级
- **修正 persona 解析**：不再读/传已废弃的 `provider_settings`（该键已迁移到 `agent_runner`），`[%None]` 按「显式无人格」语义原样处理
- **日志区分 Bot 实例**：新增顶层 `log_with_bot_id` 配置，开启后前缀为 `[astrbot_ootd][platform:{platform_id}]`
- **测试精简**：按「改错了一眼看不出来」口径把 27 项压到 11 项，并逐条变异验证

## 1.0.3 — 2026-09-03

- **修正 store 消费**：改用只读 `get` 校验时笺 store，不再重复调用 `load()`（时笺初始化时已加载）

## 1.0.2 — 2026-09-03

- **修复 time_awareness 集成**：不再直接 `from time_awareness...` import 私有模块（AstrBot 将插件加载为 `data.plugins.` 命名空间，直接 import 会在部署端 ModuleNotFoundError），改用 `get_registered_star` 取时笺公开的 `daily_schedule_store`
- **修复 persona 解析**：conversation 的 `[%None]` 归一化为未指定并回退 provider 默认 persona，避免命令报「无法解析当前角色」

## 1.0.1 — 2026-08-31

- **命令查询**：`/ootd` 查看今日穿搭（无缓存时立即生成并返回）；`/ootd new` 强制重新生成

## 1.0.0 — 2026-08-26

- **每日 OOTD**：复用 time_awareness 的每日主题/风格（`daily_theme`/`daily_style`/`ai_slots`）生成穿搭并注入本轮上下文
- **每日 cron 生成**：读取 time_awareness `ai_daily.generation_time`，`HH:MM` 在 `HH:MM+5` 判快照就绪、`-HH:MM` 在次日 0 点判；未就绪轮询等待、就绪即生成，每会话每天一次
- **缓存与隐私**：缓存 `plugin_data/astrbot_ootd/ootd.yaml` 按 Persona HMAC 键存 30 天，不落原始 persona_id
- **不接天气**：提示词按「当季通配」降级
- **随机自生成模式**：新增 `mode` 配置（`time_awareness`/`random`，默认 `time_awareness`）；`random` 模式不依赖 time_awareness，随机挑选主题/状态色彩/当日日程生成穿搭，身份解析改用 AstrBot 自带 PersonaManager 并以 `rand_` 前缀 sha256 匿名键缓存
- **随机模式配置**：`random_theme_pool`（主题池）、`random_slots_pool`（日程池）、`random_slots_count`（日程条数，0–6）
- **缓存命名空间隔离**：`time_awareness` 与 `random` 两种模式的缓存键互不串读
