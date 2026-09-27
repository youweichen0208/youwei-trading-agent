# 美股历史数据：Norgate 与 Sharadar 初筛

核实日期：2026-09-27。范围：S01 的 20 只股票 + SPY 日频研究。只核实官方公开资料，没有购买、登录付费账户或验证付费数据。报价为 USD，个人许可与机构许可不可混用；以下是候选建议，不代表 S01 验收通过。

## 价格与初步选择

| 候选 | 当前公开价格 | 对本项目的判断 |
| --- | --- | --- |
| Norgate US Platinum | $346.50 / 6 个月；$630 / 年，年付折合 $52.50 / 月，**没有月付** | 含 1990 年以来退市证券、历史指数成员；更适合已有 Windows 本地回测环境的个人，不优先作为 Linux ECS 主源。 |
| Norgate US Diamond | $433.13 / 6 个月；$787.50 / 年 | 更深历史回到 1950；当前阶段无需为额外历史付费。 |
| Sharadar Direct Prices | 5 年 $9 / 月或 $99 / 年；10 年 $19 / 月或 $199 / 年；完整历史 $39 / 月或 $299 / 年 | 包含 stocks、funds、actions、sp500、tickers，可作为个人 Phase 1A 价格与公司行为候选。 |
| Sharadar Direct Bundle | 5 年 $29 / 月或 $299 / 年；10 年 $49 / 月或 $399 / 年；完整历史 $69 / 月或 $499 / 年 | 加财务等表，适合基本面阶段试用；不必一开始年付。 |
| Nasdaq Data Link Sharadar | 本次未取得登录后的实际报价 | 机构、团队或商业用途应走该渠道或供应商的适当协议，不能套用 Direct 个人价。 |

价格来源：[Norgate 套餐](https://norgatedata.com/stockmarketpackages.php)、[Sharadar Direct 订阅页](https://sharadar.com/subscribe)、[Nasdaq SF1 页面](https://data.nasdaq.com/databases/SF1/documentation?anchor=see-also)。Sharadar Direct 是 [2026 年 7 月新推出的渠道](https://blog.sharadar.com/2026/07/sharadar-launches-direct.html?m=1)，不能沿用旧的 Nasdaq/Quandl 报价。5 年 Bundle 的 $29 是月付价格，完整历史是 $69 / 月。

## Norgate 的适配边界

- Platinum/Diamond 提供按交易日的指数成员资格；S&P 500 成员历史表列起点为 1957 年，但实际历史深度仍受套餐约束。[内容表](https://norgatedata.com/data-content-tables.php#ushics)
- 行业分类只有最近值；不提供历史分类、历史指数调整公告时间，且忽略某些临时成员。提供退市和 OTC 延续行情，但不直接提供完整并购条款、退市原因或退市收益。其建议的“最后交易价平仓”不符合本项目固定窗口终值政策。[数据 FAQ](https://norgatedata.com/data-package-faq.php)
- OHLC 可取 raw 或多种复权；open 指常规时段综合行情带的首笔合资格成交，不能直接称为主上市交易所开盘竞价价。分红复权示例采用除息前收盘扣息因子，不应未经检验就当作协议的“除息日收盘再投资”。供应商历史数据会无版本覆盖更新，因此本地冻结快照仍必要。[数据 FAQ](https://norgatedata.com/data-package-faq.php)
- 官方 Python 包要求 Windows + 正在运行的 Norgate Data Updater；Linux/macOS 需 Windows VM，不能当成原生 Linux REST 服务。[官方 Python 包](https://pypi.org/project/norgatedata/)
- 当前 EULA 是单一自然人个人使用；可用两台个人电脑，禁止其他商业用途/再分发；订阅到期须删除相应原始内容，可保留符合定义的衍生成果。团队使用和长期原始快照保留需另行解决。[EULA，2026-08-25](https://norgatedata.com/subscribe/eula.php)

## Sharadar 能满足什么

| 需求 | 官方确认与限制 |
| --- | --- |
| S&P 500 成员 | `sp500` 有当前成员、增删生效日和历史季度快照，历史到 1998。可以重建成员，但本次未验证完整性及来源许可；季度快照本身不代表任意日 PIT。[文档](https://sharadar.com/docs/sp500) |
| GICS Sector | `tickers` 提供 SIC、Fama Industry、sector/industry，未见此文档承诺 sector 是官方 GICS；**不能自行等同 GICS**。元数据表是当前快照。[证券元数据](https://sharadar.com/docs/tickers) |
| 股票与 SPY | `stocks` 对应 SEP，`funds` 对应 SFP；Prices 包含两者，覆盖 ETF。open/close 默认 split-adjusted，另有 closeunadj、closeadj；raw open 可按官方公式推导，须用快照保存其输入。[价格字段](https://sharadar.com/prices) |
| 公司行为与退市 | `actions` 含拆分、现金分红、分拆、退市原因、收购关联证券等。[Actions](https://sharadar.com/docs/actions) |
| 并购对价 | FAQ 明确有 `acquisitioncash` / `acquisitionstock`，可相加；选择现金或换股时另有 elect 字段。供应商自述覆盖约 99%，不含 CVR；不可据此保证每个终值都能自动解析。[FAQ](https://sharadar.com/docs/faqs) |
| PIT 财务 | ARQ/ARY/ART 排除后来重述，以 SEC Form 10 提交日索引；MRQ/MRY/MRT 会随重述更新。AR 是有用候选，但不是供应商所有历史修正版本档案，也不是所有历史发布时刻的完整复制。[财务维度](https://sharadar.com/docs/fundamentals#reporting-dimensions) |

官方 FAQ 还提供历史 SIC 变更，以及 raw OHLC 的推导规则。这不证明有 GICS 历史，也不证明 adjusted close 的分红再投资时点与 S00 相同。[FAQ](https://sharadar.com/docs/faqs)

Direct 提供 HTTPS REST 和 bulk CSV，适合 Linux Data Service；这是接口适配判断，不代表新加坡 ECS 链路已经实测。[查询文档](https://sharadar.com/docs/getting-started)

## 采购前真正影响方案的条件

Sharadar Direct 个人许可明确不覆盖组织、公司、团队或专业用途；禁止共享数据/账号。终止后 30 日内须删除原始数据、缓存和可重建表的数据集，只可保留不可重建原表的研究成果。**因此“受控内部使用”不自动满足个人套餐，停订后保留永久研究快照也不自动获准。** [个人许可](https://sharadar.com/terms)

建议：若明确只有个人自用，Sharadar Prices 5Y 的 $9 月付可列为低成本首轮验证候选；需要基本面再比较 $29 Bundle。若团队使用或必须停订后永久保留原始快照，先拿到对应授权和报价。Norgate 因 Windows 运维与公司行为详情缺口，暂不作为本项目首购。

任一候选进入正式 Campaign 前，还需验证：登记日全体 S&P 成员及 GICS Sector 的合法来源、raw open 口径、分红再投资算术、现金/股票收购、退市未知终值、数据修订、周六 cutoff 前到达率和快照留存权限。不能用另一分类体系静默替换 S00 已冻结的 GICS 分层。
