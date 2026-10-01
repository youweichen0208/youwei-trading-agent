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

## 3. 阻断项与事实记录

按 [s00-registration.v2.json `blocking_items`](../protocols/s00-registration.v2.json) 与 [数据许可核对](data-license-checklist.md)，区分「阻断正式启动」与「非阻断的事实记录」。

### 3.1 阻断项（需逐项解除）

| # | 阻断项 | 性质 | 谁解除 |
| --- | --- | --- | --- |
| 1 | **正式模型未实现/未验证**（方向已定：baseline 常量 + quant Logistic Regression，Trial 已预登记，结果待运行） | 模型验证 | 实现 + Trial 结果 + 项目所有者确认 |
| 2 | **实际 panel + 完整 manifest hash 未生成** | 冻结执行 | 执行 `prepare_s06_campaign.py`（数据许可已满足，可直接冻结） |
| 3 | **日历 build 版本（年份范围 + 内容 hash）与 SPY 永久 ID 未固定**（tzdb 已固定 tzdata==2026.4 / IANA 2026d；日历 RULES_VERSION=nyse-rules-v1 已固定，build 版本待冻结时确定年份范围与 hash；SPY 永久 ID 待数据采集时登记） | 固定引用 | 工程（冻结/采集时完成，无需再选技术方案） |
| 4 | **正式 release 未批准** | 人工批准 | 项目所有者批准具体 release hash |

### 3.2 事实记录（非阻断，但须在 manifest 中记录）

| 事实 | 说明 | 是否阻断 Phase 1A |
| --- | --- | --- |
| **数据许可已满足** | Tiingo Personal $30/月 + EODHD 成分数据，均个人自用、内部留存/备份；停订后删除为已知义务（见 [data-license-checklist](data-license-checklist.md)） | **否** |
| **source_effective_at 未知** | EODHD 不暴露供应商发布时间/生效时间；已记录 `observed_at`（系统采集时刻）与 `usable_at`，协议（campaign-policy.v2）允许 null + 缺失依据 | **否** |
| **LLM 转发授权未取得** | Phase 1B 首次向模型发送供应商数据前才需取得；Phase 1A 完全不调 LLM | **否** |

## 4. 可执行准备入口

`ops/prepare_s06_campaign.py` 串联现有函数（采集 → frame → 抽样 → 冻结 → 校验），输出候选包 JSON：

```bash
EODHD_API_KEY=... YOUWEI_DATABASE_URL=... python ops/prepare_s06_campaign.py
```

- 读 `s00-registration.v2.json` 的 seed/n/stratification_level/协议 hash（单一事实来源）。
- 不填 `formal_campaign_allowed`、不填批准字段、不注册 campaign、不绑定 release。
- 输出 `remaining_missing_items` 诚实列出当前缺口（见 §3）。
- 应在**一次性/受控库**运行，不在生产库运行，直到人工批准。

## 5. 下一步

1. **模型实现 + Trial 结果**：方向已定（baseline 常量 + quant Logistic Regression），Trial `registered` 已登记；下一步实现模型、按 Trial 登记的历史验证执行、登记 `started`/结果事件，再交你确认（§3.1 #1）。
2. **冻结执行**：数据许可已满足，在受控库运行 `prepare_s06_campaign.py`，生成实际 panel + 完整 hash（§3.1 #2）。
3. **固定引用**：日历 build 版本（年份范围 + hash）与 SPY 永久 ID（§3.1 #3，工程工作，冻结/采集时完成）。
4. **最终 release**：所有引用固定后，生成 release 候选 hash，交项目所有者批准（§3.1 #4）。

在全部前置解除前，`formal_campaign_allowed=false`，不启动 campaign。
