# S06 正式启动候选包（审阅与批准材料）

日期：2026-10-01  
状态：工程侧准备到可审阅/批准的程度；**正式 campaign 未启动，formal_campaign_allowed=false，无 release 批准**。最终 release hash 由项目所有者批准，不由本工具或 Agent 生成放行。

本材料汇总 S06 正式启动所需的全部信息，供项目所有者审阅。分「已确定」「待冻结」「缺失项」三部分。

## 1. 已确定（无需再决策）

| 项 | 状态 | 依据 |
| --- | --- | --- |
| 分类方案 | **已确定方案 (a) `eodhd_sector`**（2026-10-01） | [campaign-policy.v2](../protocols/campaign-policy.v2.md)、[s00-registration.v2.json](../protocols/s00-registration.v2.json) |
| 分类语义 | 供应商板块分类，保留原始值和来源，**不宣称与官方 GICS 等价** | campaign-policy.v2 §2 |
| N、种子、抽样算法、目标、评分规则 | 沿用 v1（N=20、seed=20260927、sector-stratified-hash-v1、D1/D20/D60、主 D20、SPY） | s00-registration.v2.json `sampling`/`target`/`evaluation` |
| 时间协议 | 沿用 v1（周六 06:00 ET cutoff、入场前 15 分钟 deadline） | time-protocol.v1 |
| Phase 1A 来源 | `{baseline, quant_model}`，无回退，llm_adjusted=`unavailable/not_enabled` | s00-registration.v2.json `sources.phase_1a` |
| 预测管线 / 调度器 / 状态查询 | 已实现并测试（S06a/S06b） | 实施计划 |
| 抽样 / 采集 / panel 构建 / 冻结 / 恢复 / 校验 | 已实现并测试（S06c/S06d/S06e），真实 EODHD 数据端到端抽出 20 只 | 实施计划 |
| 供应商更正触发 / 月度汇总 | 已实现（S06b） | 实施计划 |

## 2. 待冻结（工程已就绪，待执行 + 待批准）

这些项的实现函数已存在且测试通过，但「正式」冻结需满足许可条件后执行，并生成最终引用。

| 项 | 现状 | 触发条件 |
| --- | --- | --- |
| 实际 panel 完整冻结 | `freeze_panel_registration` 已就绪；可执行入口见 `ops/prepare_s06_campaign.py` | EODHD 数据许可确认后，在目标库执行一次，生成 `registration_id` + 全套 hash |
| panel 注册校验报告 | `validate_panel_registration` 已就绪 | 冻结后执行，输出 `{ok, issues, checks}` |
| 正式 training manifest | `register_training_manifest` 已就绪；载具 manifest `tm-vehicles-v0` 可登记 | 正式模型选定后登记 |
| release 候选包 | 结构见 `prepare_s06_campaign.py::_candidate_package` | 冻结 + 校验完成后填充 |

## 3. 缺失项（阻断正式启动，需逐项解除）

按 [s00-registration.v2.json `blocking_items`](../protocols/s00-registration.v2.json) 与数据许可核对，列出当前明确未解除项：

| # | 缺失项 | 性质 | 谁解除 |
| --- | --- | --- | --- |
| 1 | **Tiingo 生产套餐 ToS 未确认**（留存/缓存/LLM 转发授权；当前 token 为 free/evaluation） | 数据许可 | 项目所有者（购买付费套餐时浏览器确认） |
| 2 | **EODHD 精确限额 / 留存与转发权限未在控制台确认** | 数据许可 | 项目所有者 |
| 3 | **source_effective_at 未知**（EODHD 不暴露来源时间，`frame_as_of` 不能冒充供应商生效时间） | 数据事实 | 保守处理：保留 null + 依据（协议已允许） |
| 4 | **正式模型未选定**（baseline/quant 仍为工程载具，见 [model-candidates](../trials/model-candidates.md)） | 模型决策 | 项目所有者决策 + Trial 登记 |
| 5 | **无真实模型比较 Trial**（`trial_count=0`） | 模型验证 | 候选方向确认后、比较开始前登记 |
| 6 | **实际 panel + 完整 manifest hash 未生成** | 冻结执行 | 许可解除后执行 `prepare_s06_campaign.py` |
| 7 | **日历/tzdb/SPY 永久 ID 未固定为正式引用** | 固定引用 | 首份 release 前固定 |
| 8 | **正式 release 未批准** | 人工批准 | 项目所有者批准具体 release hash |

## 4. 可执行准备入口

`ops/prepare_s06_campaign.py` 串联现有函数（采集 → frame → 抽样 → 冻结 → 校验），输出候选包 JSON：

```bash
EODHD_API_KEY=... YOUWEI_DATABASE_URL=... python ops/prepare_s06_campaign.py
```

- 读 `s00-registration.v2.json` 的 seed/n/stratification_level/协议 hash（单一事实来源）。
- 不填 `formal_campaign_allowed`、不填批准字段、不注册 campaign、不绑定 release。
- 输出 `remaining_missing_items` 诚实列出当前缺口（见 §3）。
- 应在**一次性/受控库**运行，不在生产库运行，直到人工批准。

## 5. 下一步（需项目所有者）

1. **数据许可**：确认 Tiingo 生产套餐 ToS（§3 #1）与 EODHD 限额/权限（§3 #2）。
2. **模型决策**：审阅 [model-candidates](../trials/model-candidates.md)，确定 baseline/quant 候选方向（§3 #4），首次比较前登记 Trial（§3 #5）。
3. **冻结执行**：许可解除后，在受控库运行 `prepare_s06_campaign.py`，生成实际 panel + 完整 hash（§3 #6）。
4. **固定引用**：日历/tzdb/SPY 永久 ID（§3 #7）。
5. **最终 release**：所有引用固定后，生成 release 候选 hash，交项目所有者批准（§3 #8）。

在全部前置解除前，`formal_campaign_allowed=false`，不启动 campaign。
