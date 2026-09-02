# 更新日志

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
