# astrbot_ootd（穿什么？）

每日穿搭 OOTD：先有「今天是什么日子、什么基调」，再推「今天穿什么」。支持两种数据来源模式（配置 `mode` 切换）：

- **`time_awareness`（外接时笺）**：复用 `time_awareness`（时笺）的每日主题/状态色彩/日程时段（`daily_theme`/`daily_style`/`ai_slots`）作为穿搭输入。需安装并启用时笺。
- **`random`（随机自生成）**：不依赖时笺，随机挑选主题（`random_theme_pool`）、状态色彩（`outfit_style_pool`）与若干条当日日程（`random_slots_pool` + `random_slots_count`）自生成。

通用行为：

- **每日 cron 生成**：`time_awareness` 模式读取时笺 `daily_schedule.ai_daily.generation_time`，到就绪时刻（`HH:MM + 5 分钟`；`-HH:MM` 则为次日 0 点）后判一次时笺当天快照是否生成好——未好则轮询等待，好了就生成并缓存；`random` 模式每天 0 点直接生成。每个会话（Persona）每天只生成一次；会话晚于就绪时刻才首次出现时，首条消息补生成。
- 结果缓存 `plugin_data/astrbot_ootd/ootd.yaml`（`time_awareness` 模式用 Persona HMAC 键、`random` 模式用 `rand_` 前缀的 sha256 匿名键，均不落原始 persona_id，保留 30 天）。
- 生成完成后，`on_llm_request(priority=-260)` 把 `<OOTD>今日穿搭：…</OOTD>` 追加到本轮临时内容（不写历史），角色被问「今天穿的什么」时能自然引用。
- **命令查询**：`/ootd` 查看今日穿搭（无缓存时立即生成并返回）；`/ootd new` 强制重新生成。
- `provider_id` 留空用当前会话模型。
- 依赖与降级：`time_awareness` 模式时笺未安装 / 无今日快照时，仅按日期 + 人设生成，静默降级不报错；`random` 模式无额外依赖。LLM 失败次日/下次消息重试。
