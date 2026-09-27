# S01 数据账号与预算建议

核实日期：2026-09-27。状态：官方资料初筛，未购买账号、调用付费数据或完成 S01 验收。预算为建议，尚未经用户批准。现有付费账号情况待用户补充；AKShare 不纳入当前美股方案。

## 建议

当前是固定 20 证券 + SPY、每周预测、日线输入与固定窗口结果回填。先验收一个主数据源，采用月付；个人阶段预留 **$100–150/月**，如需第二来源交叉检查，可把 **$200/月**作为待批准上限。该预算不包含 ECS、LLM，也不保证覆盖 GICS 专项授权或额外留存权。

- **日线主源候选：Tiingo $30/月。** 原始 OHLC 与公司行为适合先验证收益管线；成为正式快照源前须解决留存权限。
- **低成本综合候选：Sharadar Direct Prices 5Y $9/月；Bundle 5Y $29/月。** 包含股票/ETF、行为与成员数据；需要财务时选 Bundle。历史深度由训练 manifest 决定，不因便宜自动认定五年足够。10Y Bundle 为 $49/月。
- **广覆盖备选：EODHD All-in-One $99.99/月。** 可以减少后续多类数据接入，但不能据套餐名认定所有 Marketplace 产品、正式 GICS、严格 PIT 和并购终值都包含。
- **分钟线等需求出现后再评估 Massive。** 当前无需为实时行情升级高价套餐。Consensus、IV 暂缓采购。

价格为 USD，来自 [Tiingo](https://www.tiingo.com/about/pricing)、[Sharadar](https://sharadar.com/subscribe)、[EODHD](https://eodhd.com/pricing)、[Massive](https://massive.com/pricing)。以上个人套餐不能直接套用于团队内部服务。

## 套餐与覆盖边界

| 候选 | 已核实公开价格 | 本项目相关能力 | 主要缺口/验收项 |
| --- | --- | --- | --- |
| Tiingo | 个人 $30/月或 $300/年；内部商业 $50/月或 $499/年 | 原始/复权 OHLC、分红、拆分 | 未证明满足 S&P 成员/GICS 和完整并购退市终值；API 财务为附加产品；用户数、云部署和留存范围按许可确认 |
| Sharadar Direct | Prices 5Y $9/月；Bundle 5Y $29/月、10Y $49/月、Full $69/月 | 股票、ETF、成员、公司行为；Bundle 加 as-reported 财务 | GICS 未证实；原始开盘价需按其复权字段规则还原并验证；个人许可不覆盖组织/专业使用 |
| EODHD | EOD $19.99/月；Fundamentals $59.99/月；All-in-One $99.99/月 | 日线、拆分分红、财务、成员历史、退市行情 | 正式 GICS 来源、历史修订版本、完整终值和常规时段开收盘定义需验证 |
| Massive，原 Polygon | 个人 Starter $29/月，5 年；Developer $79/月，10 年；Advanced $199/月，20+ 年 | 行情、证券元数据、拆分分红 | bars 默认仅拆分复权；不等于含分红总收益；需核实自动分析和长期存储许可 |

技术依据：[Tiingo EOD](https://www.tiingo.com/documentation/end-of-day)、[Sharadar 价格字段](https://sharadar.com/prices)、[Sharadar FAQ](https://sharadar.com/docs/faqs)、[EODHD Fundamentals](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds)、[EODHD 日线](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)、[Massive 复权说明](https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends)。这些是供应商文档声明，尚非本项目实测结论。

EODHD 价格页页脚对报价来源、指示性价格有宽泛声明；应要求供应商明确本项目所用 EOD 产品的具体来源及 open/close 定义，不能直接把它当作交易所官方开收盘价。参见其[定价页说明](https://eodhd.com/pricing)。

FMP 暂作为后续基本面补充候选。其 FAQ 说明 sector/industry 参考 GICS 后使用内部分类标准，不等于官方 GICS；历史成分支持在 FAQ 与接口文档中表述不一致，需实际验收。价格页面与搜索缓存存在变化，本记录不采用缓存旧价。[FMP FAQ](https://site.financialmodelingprep.com/de/faqs?code=statements)、[历史成员 API](https://site.financialmodelingprep.com/developer/docs/stable/historical-sp-500)

Norgate、Sharadar 的进一步依据见 [历史数据初筛](us-data-history-vendors.md)。Norgate 的 Windows Updater 要求不适合直接接入当前 Linux ECS，暂不列首购。

## S00 五类需求如何落地

1. **登记日 S&P 500 成员**：当前 campaign 需要实际登记时点的完整、有效、可审计成员快照，并固定使用；不必先购买数十年历史成员。未来使用历史总体训练/回测时另行验收。供应商历史成员接口不自动证明公告时间或历史修订版本完整。
2. **GICS Sector**：必须确认确为约定层级的 GICS、来源允许本项目使用、登记日生效，并保存版本。普通 `sector` 字段不能自动替代。可核实授权供应商或 [S&P DJI 数据许可](https://www.spglobal.com/spdji/en/about-us/data-index-licensing/)；价格未取得。无法满足时须显式修订协议，不能悄悄改成自有行业分类。
3. **股票/SPY 总收益与行为**：保存原始 open/close、拆分、现金分红及版本，按 TargetSpec 算术核对。复权 close 比率不能直接替代开盘至收盘的收益，更不能跳过除息日权益与再投资规则。
4. **退市/对价**：退市历史行情不等于最终收益。单独抽样现金收购、换股、混合对价、分拆及未知终值；有缺失按既定 unresolved 政策保留分母。Sharadar 文档列收购对价字段，也不代表所有例外都覆盖。
5. **Consensus/IV**：当前 Phase 1A 暂不采购、不做依赖；后续需额外确认历史可用时间和修订版本，不能用今天的估计值回填过去。

## 留存权与使用方式必须匹配

- **Tiingo**：现行条款禁止 Starter/Trial 持久保存数据；合资格付费计划可在授权范围内保存，但停订/降级后要求删除原始数据、备份和归档。不同留存安排需单独书面约定。[Terms §1.6](https://api.tiingo.com/tos/)
- **Sharadar Direct**：仅个人非专业使用；终止后 30 日内删除原始数据及可重建数据集。团队/机构通过 Nasdaq 或其他适当协议采购。[个人许可](https://sharadar.com/terms)
- **EODHD**：个人存储/分析与团队使用分开；Internal 商业档公开价为 $399/月。云服务器下载、第三方模型处理和停订后快照保留仍需确认。[商业价格](https://eodhd.com/commercial-pricing)、[条款](https://eodhd.com/financial-apis/terms-conditions)
- **Massive**：个人与商业许可分开，市场数据条款另涉及非展示用途及终止后删除。[个人许可](https://massive.com/legal/individuals-terms-of-service)、[市场数据许可](https://massive.com/legal/market-data-terms-of-service)

本项目应把“允许在新加坡 ECS/OSS 保存研究输入、持续复现、备份恢复，以及订阅终止后的处理方式”写进供应商验收记录。仅保留数据 hash 无法复现输入；把原始数据放入 WORM 也不会自动获得保留权。Phase 1B 再明确第三方 LLM 处理的数据范围。

本记录不改变 S00 协议、正式 Campaign 就绪状态或 S01 勾选状态；实际样本验证和供应商许可仍待完成。
