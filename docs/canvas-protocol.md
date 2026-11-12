# Canvas 与事件协议 v1.1

状态：设计，待实现验证。参见 [架构](./architecture.md) 与 [API 契约](./api-contract.md)。所有事件均由应用服务验证并持久化后发出，不直接信任模型生成的事件身份。

## 1. 事件信封

每个事件带 eventId、taskId、threadId、sequence、event、data；sequence 是所订阅根任务内严格递增的序号，重发保持身份不变。子任务事件附带 childTaskId、parentTaskId、role，统一进入根任务事件流。artifactId 只出现在 Canvas 操作中，不强制普通文本或状态事件携带。

```json
{
  "eventId": "evt_01",
  "taskId": "task_01",
  "threadId": "thread_01",
  "sequence": 1,
  "event": "canvas.upsert",
  "data": {
    "workspaceId": "workspace_01",
    "workspaceVersion": 3,
    "artifactId": "artifact_01",
    "component": "stock_chart",
    "datasetRef": "dataset_01",
    "title": "NVDA 日线",
    "props": {"showVolume": true}
  }
}
```

SSE id 使用应用 sequence；客户端用 Last-Event-ID 续传（首次可用 afterSequence）。游标只能用于对应任务，不能直接透传为 Hermes 游标。

## 2. 白名单

| 事件 | 行为 |
|---|---|
| assistant.delta | 主回答追加文本；携带稳定的服务端 messageId；专项文本不进入主消息 |
| run.progress | 角色、阶段、queued/running/stopping 状态 |
| run.unknown | 不确定状态和可显示原因，不是终态 |
| run.completed | 全部必需结果已归档，包含最终消息和结果引用 |
| run.failed | 确认失败及部分结果引用 |
| run.cancelled | 确认所有相关执行退出 |
| run.interrupted | 确认中断及部分结果引用 |
| canvas.upsert | 创建或替换受控组件 |
| canvas.patch | 修改字段白名单 |
| canvas.remove | 删除指定组件 |
| state.snapshot | 全量恢复快照及恢复游标 |

兼容沿用 run.* 名称，但 taskId 指应用任务，不是 Hermes run_id。专项事件更新进度，只有根任务的最终回答成为主 Chat 消息。任何终态都必须来自持久化状态机。

## 3. 首版组件与校验

| 组件 | 数据要求 |
|---|---|
| stock_chart | OHLCV datasetRef，股票/区间/口径由数据元信息取得 |
| indicator_panel | Quant 指标 resultRef 或指标 datasetRef |
| research_summary | 保存的结论 resultRef 与来源引用 |
| prediction_result | 预测、基线、样本外指标与时间窗 resultRef |
| portfolio_summary | 模拟持仓版本、行情日期及估值 resultRef |

backtest_result 属于阶段 2，首版拒绝。禁止自由 HTML、JS、React 源码及外部 iframe。Markdown 禁用原始 HTML，来源链接仅允许 http/https。

校验顺序：用户/任务/workspace 归属 → 组件白名单 → 数据引用归属与类型 → 元信息一致性 → 字段限制与版本 → 保存并发事件。标题不得覆盖服务端显示的股票、时区、单位、调整口径或数据截至时间。预测和持仓摘要必须显示探索性/模拟标识。

patch 使用明确对象替换字段，仅允许 title 和受控展示参数（颜色、showVolume）；未知字段拒绝，不能 patch datasetRef、resultRef、symbol 或数值。更换数据使用经过完整校验的 upsert。remove 只作用于指定 workspace 内已授权 artifact。

## 4. 顺序、版本与恢复

- 用户布局保存携带 expectedVersion；数据库比较版本后提交，冲突返回 409，客户端重新读取再操作。
- 任务提交时绑定 workspaceId 与研究修订号；新研究开始后，旧修订任务可归档产物，但不得自动覆盖当前画布。用户可从任务历史显式打开旧结果。
- Canvas 改动与 workspaceVersion 递增、事件写入同一事务；只在实际状态变化时增加版本。前端按 eventId 去重，按 sequence 顺序处理；发现缺口先恢复，不猜测 patch 顺序。
- Last-Event-ID 在保留范围内则重放后续事件；超出范围发送 state.snapshot，包含任务/子任务状态、累计主消息、当前画布、结果引用与 lastSequence。
- 快照和 lastSequence 必须来自一致数据库视图；随后只发送大于该序号的事件，避免快照与订阅之间漏事件。快照表示完整替换，不是增量补丁。
- 页面关闭仅停止订阅，不发送 stop。明确点击取消才调用任务停止接口。重连、重绘或渲染失败不重新计算；按既有引用重取结果。
- 事件 7 天后可清理，但最终消息、任务快照、产物和数据引用仍可恢复。

## 5. 待实现验收

1. 两用户交叉访问任务、数据和 workspace 均返回 404。
2. 非法组件、JS/HTML、价格数组、错误数据类型和冲突标签被拒绝。
3. 重复事件不产生重复组件，乱序事件触发恢复，旧任务不覆盖新研究。
4. 关闭页面后任务完成；超过事件保留期再进入仍恢复全文和画布。
5. 取消中保持状态可见，子任务尚未退出时不能显示已取消。
6. 部分失败保留引用；缺失指标使用 null，预测样本不足及持仓缺价明确展示。

这些是完整 Canvas 验收要求，尚未全部满足；已有模块测试与剩余工作见 [实施状态](./implementation-status.md)。


迁移 012 已实现主任务文本快照：`state.snapshot.data.task.stream_text` 是已事务保存的连续前缀，`stream_message_id` 是其消息 ID。刷新恢复先替换为该前缀，再消费快照游标之后的 assistant.delta；最终回答覆盖前缀，不重复追加。上游事件缺失会保存 `event_history_gap` 进度，随后使用最终回执补偿，不拼接缺段文本或把工具完成事件当作任务终态。
