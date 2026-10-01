# 数据供应商许可状态（S06 准备材料）

日期：2026-10-01  
状态：**Phase 1A 数据许可已满足**（项目所有者确认：两家均购买付费套餐、个人档、自用/受控内部研究、本地/SG 留存）。LLM 转发授权归 Phase 1B，非 Phase 1A 前置。

依据：[us-data-vendor-selection](../research/us-data-vendor-selection.md)、[tiingo-token-verification](../research/tiingo-token-verification.md)、[eodhd-constituents-verification](../research/eodhd-constituents-verification.md)。

## 1. 阶段拆分原则

| 阶段 | 涉及许可 | 现状 |
| --- | --- | --- |
| **Phase 1A**（不调 LLM） | 内部计算、留存、备份、退订后处理、套餐限额 | **已满足**（见下） |
| **Phase 1B**（首次转发 LLM） | 向模型发送供应商数据的授权 | 未取得，Phase 1B 首次转发前再落实，不阻断 Phase 1A |

## 2. Tiingo（行情源：OHLC、分红、拆分）

- 套餐：**Personal $30/月（付费计划）**，项目所有者 2026-10-01 确认。
- 用途：自用/受控内部研究，不向公众展示、不转售，20 证券 + SPY 的小规模日线输入。
- Phase 1A 判定：**已满足**。依据 [Tiingo ToS §1.6](https://api.tiingo.com/tos/)：「现行条款禁止 Starter/Trial 持久保存数据；**合资格付费计划可在授权范围内保存**」。Personal 是合资格付费计划，自用内部研究落在授权范围内，留存与备份允许。
- 已知义务（不阻断，记录备忘）：
  - **停订/降级后须删除**原始数据、备份和归档。
  - Personal 为「个人」档；若日后转为团队/多用户或对外商业服务，需升级 Internal Business（$50/月 或 $499/年）。
  - 精确每日限额不在响应头，按 1 req/s 保守默认，实际配额在控制台可见。

## 3. EODHD（成分/分类源：S&P 500 成员、板块）

- 套餐：Indices Historical Constituents Data 产品，项目所有者 2026-10-01 确认个人档、自用、本地/SG 留存。
- Phase 1A 判定：**已满足**（个人自用、内部留存）。
- 已知义务（不阻断，记录备忘）：
  - Marketplace 产品的精确每日限额不在响应头，保守按限速调用。
  - 若日后转为团队/商业使用或云分发，需按 EODHD 商业档重新确认。

## 4. 汇总

| 阶段 | 项 | 状态 |
| --- | --- | --- |
| Phase 1A | Tiingo Personal 付费计划留存/备份（自用内部研究） | **已满足** |
| Phase 1A | EODHD 成分数据留存（自用内部研究） | **已满足** |
| 备忘 | 停订/降级后删除原始数据、备份、归档 | 已知义务，不阻断 |
| 备忘 | 转团队/商业使用需升级套餐档位 | 已知义务，不阻断 |
| Phase 1B | LLM 转发授权（两家） | 未取得，Phase 1B 前再落实 |

结论：**Phase 1A 的数据许可已满足，不再构成正式 campaign 启动的阻断项**。原先记录的「Tiingo 生产套餐 ToS 未确认」「EODHD 限额/留存待确认」两项，因项目所有者已购买付费套餐并确认个人自用，予以解除。LLM 转发授权保持为 Phase 1B 的独立前置，不影响 Phase 1A。
