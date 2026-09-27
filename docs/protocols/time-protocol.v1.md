# 时间协议 v1

protocol_id：time-protocol-v1\
登记日期：2026-09-27\
状态：Q4 周末锚定规则已选择；日历规则已实测（2026-09-27：规则生成 NYSE 日历与 686 个真实 SPY 交易日双向零差异，含卡特哀悼日/Good Friday/提前收盘；见 [实施计划 S04b](../IMPLEMENTATION_PLAN.md)）；tzdb 包固定、NTP 偏差阈值目标机实测待 S09（阈值已入 Settings）。

## 1. 周批次时刻

| 字段 | 规则 |
| --- | --- |
| business_timezone | America/New_York（ET，不固定写 EST） |
| storage_timezone | UTC |
| frequency | 每周一个批次 |
| decision_cutoff | 每周六 06:00:00 ET |
| entry_session | decision_cutoff 后第一个常规交易日 |
| entry_at | entry_session 的常规开盘，正常为 09:30 ET |
| prediction_deadline | entry_at 前15分钟，正常为入场日 09:15 ET |
| exit_at | 从 entry_session 算 D1，第 N 个交易日的常规收盘 |
| horizon_td | 1、20、60；主 horizon 为20 |
| calendar_policy | 版本化 NYSE 常规交易日历；保留每证券主要上市市场与停牌校验 |

常规开收盘与节假日/提前收盘基于交易所日历，不能用工作日加减或固定 UTC 小时数代替。[NYSE 官方日历](https://www.nyse.com/markets/hours-calendars)

纽约当地时间先按固定 tzdb 解析，再转 UTC 保存；manifest 同时记录 calendar_id/version/hash、tzdb_version、解析后的 UTC 时刻与原始本地时间。覆盖正常日、假日、提前收盘和 DST 测试。

## 2. 研究窗口不是单任务超时

正常周的周六06:00到周一09:15是51小时15分钟；周末发生 DST 变化时实际时长会增减一小时。遇周一休市，deadline 随下一个交易时段顺延。

这是整个批次的调度窗口，不是允许一个 Agent 运行51小时。单 run 的墙钟、模型费用、重试次数和 Pi Job 限额仍按批准 release 执行；延迟期间不能吸收 cutoff 后的新信息。

首次启动前，若计划、总体快照、release 或权限在本周 cutoff 时尚未准备好，使用下一次满足前置条件的 cutoff 作为起点，不回填已经错过的周六。

一旦固定 panel 与连续周次计划已登记，后续任何周次因权限、供应商、预算或依赖未就绪而无法执行，都必须保留计划批次及60个 case 槽位，记录 skipped/missing 原因并进入 coverage 分母。故障后根据事前登记的周期计划补记漏执行事实，记录实际补记时间；禁止把难周从计划中删除或补造准时预测。

## 3. 输入冻结

正式输入同时满足：

```text
source_available_at <= decision_cutoff
usable_at <= decision_cutoff
```

截止后到达的周五 EOD 数据也不加入本批次。保留 missing_late_arrival/数据质量原因；必要输入缺失时对应来源 unavailable，不能向过去改写 usable_at。历史财务、consensus 和公司行为的版本遵守同样规则。

manifest 可在 cutoff 后物化，但仅选择 cutoff 当时已可用的数据版本；衍生特征只使用冻结输入。S01 记录供应商 EOD 发布/采集延迟，用于验证当前截止是否可行；若需改变截止，先修订协议并用于未来批次。

## 4. 封存与持久提交确认

```text
decision_cutoff <= sealed_at <= durable_confirmation_at
durable_confirmation_at <= prediction_deadline < entry_at
```

sealed_at 是 SG 数据库在取得 case/chain 锁后，以 clock_timestamp() 产生的实时封存检查点，不是事务开始时间，也不声称是物理 commit 时间。应用不得传入或回填 sealed_at。

durable_confirmation_at 是提交成功已被可信核心确认的保守时间；S02/S05 选择可审计实现，保证与数据库时钟的可比较性。确认丢失标记 uncertain，确认晚于 deadline 标 late；原预测不删除，也不通过重试自动变成准时。

截止后拒绝新预测；回退也必须在截止前完成。入场前15分钟的缓冲不能被 Agent 自行缩减。

## 5. 时钟与日历变化

SG 数据库主机启用受监控的 NTP 同步，业务日期转换使用版本化 America/New_York 规则。时钟未同步或偏差越过部署阈值时停止正式封存，不能只在报告中补一条警告。偏差阈值由 S01 实测、S02 配置，在 campaign 启动前固定。

已封存 case 保留原计划日历与时间。若交易所临时停市或更改时段，追加事件与数据质量说明，按 TargetSpec 的异常政策处理；不能用更新后的日历静默重算旧 case。

## 6. 输入与结果时点分离

数据截止约束用于预测输入；Outcome 可以使用持有期间及之后取得的价格、分红和更正版本，但只写入新的结果版本，不回写预测输入。D1/D20/D60 分别等待自己的计划出场和数据可用时点。
