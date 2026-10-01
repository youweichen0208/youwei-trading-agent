# S06 正式启动候选包（审阅与批准材料）

日期：2026-10-01  
状态：工程侧准备到可审阅/批准的程度；**正式 campaign 未启动，formal_campaign_allowed=false，无 release 批准**。最终 release hash 已生成供审阅，批准由项目所有者完成，工具不生成放行。

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

## 2. 已冻结的候选材料（未批准）

以下工程已实际执行；本节旧入口供定位，实际引用与恢复报告以冻结记录为准。

| 项 | 现状 | 证据 |
| --- | --- | --- |
| 实际 panel 完整冻结 | 已冻结20证券，完整源/映射/名单hash齐备 | 真实冻结记录及私有panel-bundle |
| panel 注册校验报告 | 实际新库恢复、复算、幂等均通过 | panel-recovery-report.json |
| 候选 training manifest | Logistic/Ridge已登记独立manifest，未批准 | training-manifest.json |
| release 候选包 | 已生成并登记具体hash，批准数0 | release-candidate.json / release-summary.json |

## 3. 阻断项与事实记录

按 [s00-registration.v2.json `blocking_items`](../protocols/s00-registration.v2.json) 与 [数据许可核对](data-license-checklist.md)，区分「阻断正式启动」与「非阻断的事实记录」。

### 3.1 当前剩余与已完成项

真实完成证据见 [冻结与恢复记录](s06-campaign-freeze-20261001.md)。

| 项 | 当前状态 |
| --- | --- |
| 模型实现 + Trial | Logistic/Ridge与release适配已实现；真实历史比较完成，D20未优于基线，历史PIT限制完整披露 |
| panel + 完整hash | 503成员→20证券已冻结；完整包在新库恢复复算一致 |
| 日历 / tzdb / SPY | nyse-rules-v2、2016–2028 build、实际tzdata字节与SPY永久ID均固定 |
| training manifest / release候选 | 已在独立候选库登记，具体hash见冻结记录；无批准、无Campaign |
| 人工决定 | 审阅负向Trial及数据限制，决定是否允许未来实验；批准必须指向实际release hash |
| 生产部署 | SG权限、资源与运维验收仍归S09；本地候选完成不代表生产部署完成 |

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

本次工程和真实历史Trial已完成。先审阅 [Trial结果](../trials/trial-001-results.md) 与候选release。若继续改变模型或特征，先登记新试验；若允许当前候选开展未来实验，需人工批准具体hash，并完成相应部署验收。

`formal_campaign_allowed=false`，未启动Campaign。冻结后的panel按registration ID恢复/校验，不能重跑采集抽样替代恢复。
