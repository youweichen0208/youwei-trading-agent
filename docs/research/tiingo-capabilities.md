# Tiingo 能力核实

核实日期：2026-09-27。方法：API 路由探测（无 token，经代理直连 api.tiingo.com）。Tiingo 官网文档/定价/条款页为 JS 渲染 SPA，CLI 抓取不到正文（20263 字节空壳）；字段级验证需注册账号取得 token。需求对照 [target-spec.v1](../protocols/target-spec.v1.md) 与 [campaign-policy.v1](../protocols/campaign-policy.v1.md) 第 2 节。

## 已确认（API 路由探测）

| 端点 | 状态 |
| --- | --- |
| `/tiingo/daily/{ticker}/prices` | 存在（AAPL、SPY 路径均返回认证提示） |
| `/tiingo/corporate-actions/{ticker}/distributions` | **存在**（独立分红端点） |
| `/tiingo/corporate-actions/{ticker}/splits` | **存在**（独立拆分端点） |
| `/tiingo/fundamentals/{ticker}/daily` | 存在（市值、PE 等日频指标） |
| `/tiingo/fundamentals/meta?ticker={t}` | 存在（行业字段所在端点） |
| `/tiingo/fundamentals/{ticker}/statements` | 存在 |
| 认证模型 | `token` 参数；无 token → `Please supply a token`；无效 → `Invalid token.` |

方法论记录：父路径（如 `/tiingo/corporate-actions`）404 **不能**证明子资源不存在——首版曾据此误判公司行为无独立端点，已按官方文档纠正。

## 需求对照与缺口

| S00 需求 | Tiingo 能力 | 状态 |
| --- | --- | --- |
| 日线原始 OHLC | dailyPrices | 端点存在，字段待验证 |
| 公司行为（除息日/金额/拆分） | 独立 distributions / splits 端点（官方存在） | Beta 状态、套餐权限、实际字段待账号验证；**不能假设个人 $30 套餐已包含**（[分红文档](https://www.tiingo.com/documentation/corporate-actions)、[拆分文档](https://www.tiingo.com/documentation/splits)） |
| S&P 500 成员 PIT 历史 | 无此端点 | **缺口，需补源（见下）** |
| GICS Sector 分类 | fundamentals/meta 的 sector/industry 官方定义**派生自 SIC**，非 GICS | **直接判定：不满足 GICS 要求**，无需购买后验证；GICS 必须独立来源（[官方字段定义](https://www.tiingo.com/documentation/fundamentals)） |
| 退市/并购对价 | 未知 | 待 token 验证 |
| SPY 总收益同口径 | 与股票同端点 | 路径存在，内容待验证 |

总收益口径：目标是用原始价格 + 公司行为按 target-spec §3.1–3.3 自行计算（除息日收盘再投资、仅计有权份额），不依赖供应商 TR 序列；Tiingo 的 adjusted 字段只作交叉核对。

## 补充源：S&P 500 成分历史

- **EODHD Indices Historical Constituents Data API**：官方页面确认覆盖 S&P 500 / 400 / 600 / 100 及 20 个行业指数，提供指数列表、成分明细、历史变更，JSON 格式；页面标价 $29.99/月。**候选之一**（非“最小”选择）。来源：[eodhd.com](https://eodhd.com/financial-apis/sp-and-dow-jones-indices-historical-constituents-data-api)
- **Sharadar Direct Prices（$9/月，5Y）**：已入库供应商初筛确认包含 sp500 成员数据，价格低于 EODHD；需按相同权限、PIT 质量与留存要求比较后定（[Sharadar 套餐](https://sharadar.com/subscribe)，详见 [us-data-vendor-selection](us-data-vendor-selection.md)）
- Wikipedia [List of S&P 500 companies](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies)：现行成分表 + 变更记录段，仅作交叉核对；PIT 严谨性不足，不作为正式采样源
- S00 首要需求是**登记日的成分快照**（含版本/hash），补源比较以此为第一验收项

## 待账号验证（已于 2026-09-27 实测，结果见 [tiingo-token-verification](tiingo-token-verification.md)）

1. ~~dailyPrices 响应字段与调整口径~~ → 字段全集在；拆分精确，分红复权 ~1e-5 偏差（内部股息 0.2697 vs 展示 0.27），adjClose 仅作容差交叉核对
2. ~~corporate-actions distributions / splits 套餐权限与字段~~ → 当前（evaluation）套餐 403；日线内嵌 divCash/splitFactor 替代
3. ~~fundamentals/meta 的 sector/industry 取值~~ → `tickers=` 复数参数下可用（AAPL: Technology/SIC 3571）；SIC 派生确认；inactive ticker 不可用
4. ~~退市 ticker 覆盖~~ → 三形态：干净保留（ATVI/TWTR）、幽灵行（SGEN：冻结价 volume=0、endDate 误导）、完全缺失（SIVB 404）；isActive 是可靠标志
5. ~~EOD 发布延迟~~ → 初步证据支持（周五数据在；dailyLastUpdated 周五 22:15 ET）；待下次周五晚→周六 06:00 ET 实盘观测定案
6. ~~定价 tier、限额、ticker 数上限~~ → evaluation 确认；无 rate-limit 头；精确配额需网页控制台
7. ~~Terms of Use 授权约束~~ → SPA 不可程序化读取；付费套餐购买时浏览器确认（用户任务）
8. ~~SPY / ETF 配额~~ → 同端点正常返回

## 结论

Tiingo 可作主行情源（原始 OHLC + 独立公司行为端点自行计算总收益，符合协议口径）；**S&P 500 成分 PIT 快照是缺口**（EODHD $29.99/月与 Sharadar $9/月为候选，按相同权限与质量要求比较）；**GICS 分类直接判定不满足**（Tiingo 行业字段为 SIC 派生），需独立来源。正式采用前需完成字段级验证（**已干 2026-09-27，见上**），并确认授权条款覆盖“数据经 LLM Gateway 发送给模型供应商”的使用方式（架构 §10 要求）——后者待付费套餐购买时浏览器确认。
