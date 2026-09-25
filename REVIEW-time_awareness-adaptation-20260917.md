# 审查记录：time_awareness 适配探针失效（2026-09-17）

## 背景

`time_awareness` v2.3.0 已整体移除主动提醒能力（调度器、`schedule_followup` 工具、
`create_external_task` 插件 API、`reminder` 配置组）。本次审查 ootd 对 time_awareness
的依赖，发现**一处探针选型缺陷**：即便 ootd 真正需要的能力都还在，也会因探针失灵而整体降级。

## 问题

`main.py:514-533` 的 `_resolve_time_awareness()` 用 `create_external_task`
的**存在性**作为"这是 time_awareness 实例"的判据：

```python
def _resolve_time_awareness(self):
    """返回已激活的 time_awareness 实例（供读取其配置与运行时 now）。"""
    star = self.context.get_registered_star("time_awareness")
    if star is None or not bool(getattr(star, "activated", True)):
        return None
    for candidate in (star, getattr(star, "star", None), getattr(star, "star_cls", None)):
        if candidate is not None and callable(
            getattr(candidate, "create_external_task", None)   # ← 仅作探针
        ):
            return candidate
    return None
```

全库检索确认：**ootd 从未调用 `create_external_task`**。它被选中只是因为需要从
`star` / `star.star` / `star.star_cls` 三种封装层级中挑出带业务方法的那个，随手取了
一个方法名做存在性判断。

而 ootd 真正依赖的三项接口，探针一个都没覆盖：

| ootd 实际使用 | 位置 |
|---|---|
| `ta.config`（读 `daily_schedule.ai_daily.generation_time` / `enabled`） | `main.py:323-326` |
| `ta.time_context.now()`（插件时区取「今天」） | `ootd_context.py:139-148` |
| `ta.daily_schedule_store`（读当日 OOTD 快照） | `main.py:498-508` |

## 影响

`_resolve_time_awareness()` 返回 `None` 后，以下调用点全部退化（time_awareness 模式下
OOTD 不生成、命令降级或跳过，按 `random` 模式兜底）：

- `main.py:182` 身份解析（`resolve_ootd_identity` 的 `ta_instance`）
- `main.py:321` 读取就绪时刻配置（`_ootd_ready_minute` → `None` 时不排程）
- `main.py:345` / `:387` 就绪时刻与每日循环的 `resolve_now`（回退系统本地时区，插件时区语义丢失）
- `main.py:500` 读取当日快照（`_ootd_ta_store` 返回 `None`）

注意：这不是 time_awareness 的能力缺失——`config`、`time_context`、`daily_schedule_store`
三项在 v2.3.0 中均保留，ootd 改对探针后即可恢复正常。

降级形态是**静默跳过**、不抛异常：`resolve_ootd_identity()` 在 `store=None` 时返回 `None`，
`_ootd_ready_minute()` 返回 `None` 后不排程（`ootd_context.py:167-168`、`main.py:321-343`）。
因此不会刷错误日志，只是 OOTD 不再按「时笺模式」生成。

## 修复建议

把探针换成实际依赖项（保持三层 candidate 遍历不变）：

```python
for candidate in (star, getattr(star, "star", None), getattr(star, "star_cls", None)):
    if candidate is None:
        continue
    # 按实际依赖探测：快照 store + 运行时 time_context（可再加 config）
    if (
        getattr(candidate, "daily_schedule_store", None) is not None
        and getattr(candidate, "time_context", None) is not None
    ):
        return candidate
```

若希望更保守，可同时要求 `getattr(candidate, "config", None) is not None`。

## 验证

1. reload ootd 后执行 OOTD 命令（TA 模式），确认不再降级为 `random`；
2. 日志中不应出现"时笺未就绪/跳过"类提示；
3. 检查生成的 OOTD 是否引用了当日日程主题/时段（说明 `daily_schedule_store` 读取成功）；
4. 跨时区场景确认取「今天」用的是 time_awareness 的 `time_context.now()` 而非系统本地时间。

## 附：time_awareness v2.3.0 保留的对外接口

- `config`（插件配置字典）
- `time_context`（`now()` / `enabled_builtin_categories()` 等）
- `daily_schedule_store`（快照读取：`get` / `persona_hash` / `load_readonly` 等）
- `daily_schedule_service`、`calendar_store`、`calendar_manager`、`builtin_manager`
- 命令：`/calendar …`、`/schedule …`
