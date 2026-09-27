# S00 协议登记

登记日期：2026-09-27\
状态：S00 协议已确认定稿（2026-09-27，Q1–Q9）；尚无正式 campaign、release 批准或供应商快照，formal_campaign_allowed=false。

本次登记依据项目所有者的 Q1–Q9 选择；Q5 固定窗口停牌政策、Q7 区间方法与 Q9 Campaign/Batch 层级已确认并入协议。实际数据、模型、日历与权限等 S01+ 依赖在各协议与登记 JSON 中标注为未就绪。

| 文件 | 内容 |
| --- | --- |
| [target-spec.v1.md](target-spec.v1.md) | D1/D20/D60、SPY、总收益和异常处理 |
| [time-protocol.v1.md](time-protocol.v1.md) | 周末锚定、截止/提交/入退场、时钟和数据冻结 |
| [campaign-policy.v1.md](campaign-policy.v1.md) | 内部范围、总体、抽样、来源、评分、审批与 trial |
| [known-answer-cases.v1.md](known-answer-cases.v1.md) | 协议反例与合成已知答案 |
| [s00-registration.v1.json](s00-registration.v1.json) | 机器可读的已登记参数、阻断项和协议文件 hash |
| [Trial Registry](../trials/registry.md) | 试验登记格式与追加约束 |

v1 在草案状态可以修订并保留 Git 记录；一旦有 campaign 或批准记录绑定其内容 hash，后续变化创建新版本，不能覆盖旧语义。源数据、模型、日历、证券名单未就绪时保留空引用；禁止用示例 hash 或虚构批准时间填充。

批准人为项目所有者本人（人类用户），不是执行 Agent。当前消息确认使用范围与设计选择，不构成对未来尚未生成的 release hash 的上线批准。

## 登记与上线检查

| 项目 | 当前状态 |
| --- | --- |
| 使用范围、N=20、三种 horizon、周末锚定、Phase 1A 来源 | 按用户选择登记 |
| GICS 层级、配额、具体种子、规范化方式 | 文档中给出确定性实现约定 |
| Q5 停牌窗口、Q7 区间方法、Q9 Campaign/Batch 层级 | 已确认（2026-09-27） |
| PIT 成员/GICS 快照、实际20证券名单、名单 hash | 待 S01/S04 数据与授权验证 |
| NYSE 日历与 tzdb 版本、SPY 永久 ID | 待 S01/S04 固定 |
| quant 模型、training manifest、release hash | 待实现并登记 |
| 人工批准入库、归档与恢复 | 待 S02/S05/S06 实现并验收 |

这些协议允许继续准备 S01 的独立验证；所有阻断项解除前不得启动正式 campaign。
