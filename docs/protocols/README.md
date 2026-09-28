# S00 协议登记

登记日期：2026-09-27\
状态：v1 于2026-09-27确认；2026-09-28已起草供应商分类候选 v2。已有真实 EODHD 采集与抽样冒烟，正式 panel/具体 release 的批准和登记仍未完成，formal_campaign_allowed=false。

v1 登记依据项目所有者的 Q1–Q9 选择；Q5 固定窗口停牌政策、Q7 区间方法与 Q9 Campaign/Batch 层级继续沿用。候选 v2 推荐方案 (a)：明示采用 `eodhd_sector`，不宣称与官方 GICS 等价。它是待绑定下一份实际 release 的准备材料，未替任何具体 release 生成批准记录。

| 文件 | 内容 |
| --- | --- |
| [target-spec.v1.md](target-spec.v1.md) | D1/D20/D60、SPY、总收益和异常处理 |
| [time-protocol.v1.md](time-protocol.v1.md) | 周末锚定、截止/提交/入退场、时钟和数据冻结 |
| [campaign-policy.v1.md](campaign-policy.v1.md) | 内部范围、总体、抽样、来源、评分、审批与 trial |
| [known-answer-cases.v1.md](known-answer-cases.v1.md) | 协议反例与合成已知答案 |
| [s00-registration.v1.json](s00-registration.v1.json) | 机器可读的已登记参数、阻断项和协议文件 hash |
| [campaign-policy.v2.md](campaign-policy.v2.md) | 候选修订：供应商板块分类、首次前向时间证据、冻结映射和正式 panel 登记校验 |
| [s00-registration.v2.json](s00-registration.v2.json) | 候选参数与文件 hash；正式引用保持空，formal_campaign_allowed=false |
| [Trial Registry](../trials/registry.md) | 试验登记格式与追加约束 |

本轮保留 v1 文件及登记 hash，以新 v2 记录分类语义变化。一旦有 campaign 或批准记录绑定内容 hash，后续变化必须创建新版本；候选修订也要同步机器登记的文件 hash。实际引用未固定时保留空值，不能用一次性库冒烟的短 hash、示例名单或虚构批准时间填充。

历史完整性注记：`0401fd4` 曾修改 `time-protocol.v1.md` 的进度状态行，导致当前文件 hash 与 v1 登记不一致；时间规则未改变。v1 登记的旧字节可从 `52df87f` 恢复，本轮不改写它。v2 引用当前文件的真实 hash，并在 `inherited_file_reconciliation` 保留完整历史提交与前后 hash；不能宣称 v1 的旧登记对当前全部文件仍然通过。

批准人为项目所有者本人（人类用户），不是执行 Agent。当前消息确认使用范围与设计选择，不构成对未来尚未生成的 release hash 的上线批准。

## 登记与上线检查

| 项目 | 当前状态 |
| --- | --- |
| 使用范围、N=20、三种 horizon、周末锚定、Phase 1A 来源 | 按用户选择登记 |
| 分类、配额、种子、规范化方式 | v1 原为 GICS；候选 v2 推荐 eodhd_sector；配额、种子和规范化算法保持原登记 |
| Q5 停牌窗口、Q7 区间方法、Q9 Campaign/Batch 层级 | 已确认（2026-09-27） |
| 实际成员和分类、20证券 panel | S06d 采集/抽样冒烟通过；正式源时间、映射恢复、manifest 交叉校验与完整登记仍待收尾 |
| NYSE 日历与 tzdb 版本、SPY 永久 ID | 已有开发实现；首份实际 release 仍须固定真实引用 |
| quant 模型、training manifest、release hash | 已有载具 manifest 及登记机制；正式模型选择、实际登记与批准仍需落实 |
| 批准入库、归档与恢复 | 已有开发验收；真实 release 批准、目标运行环境及恢复依据单独验收 |
| 许可与预算 | Phase 1A 先确认数据计算/留存/备份许可并建议 LLM 上限0；LLM 数据转发授权与非零预算归 Phase 1B |

开发准备可以继续推进。当前仍有工程校验待办，详见 [实施计划 S06e](../IMPLEMENTATION_PLAN.md#s06e-分类方案与正式登记复核2026-09-28)；所有正式运行前置条件解除前不得启动 campaign。
