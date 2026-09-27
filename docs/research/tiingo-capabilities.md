# Tiingo 能力核实

核实日期：2026-09-27。方法：API 路由探测（无 token，经代理直连 api.tiingo.com）。Tiingo 官网文档/定价/条款页为 JS 渲染 SPA，CLI 抓取不到正文（20263 字节空壳）；字段级验证需注册账号取得 token。需求对照 [target-spec.v1](../protocols/target-spec.v1.md) 与 [campaign-policy.v1](../protocols/campaign-policy.v1.md) 第 2 节。

## 已确认（API 路由探测）

| 端点 | 状态 |
| --- | --- |
| `/tiingo/daily/{ticker}/prices` | 存在（AAPL、SPY 路径均返回认证提示） |
| `/tiingo/fundamentals/{ticker}/daily` | 存在 |
| `/tiingo/daily/{ticker}/dividends` | **不存在**（404） |
| `/tiingo/corporate-actions` | **不存在**（404） |
| `/tiingo/news/{ticker}` | 不存在（news 为独立产品路由） |
| 认证模型 | `token` 参数；无 token → `{"detail":"Please supply a token"}`；无效 → `{"detail":"Invalid token."}` |

推论：公司行为**不在独立端点**，应在 dailyPrices 响应字段内（adjClose / divCash / splitFactor 一类）——待 token 验证字段名与口径。

## 需求对照与缺口

| S00 需求 | Tiingo 能力 | 状态 |
| --- | --- | --- |
| 日线原始 OHLC | dailyPrices | 端点存在，字段待验证 |
| 公司行为（除息日/金额/拆分） | 推测在 dailyPrices 字段 | **待 token 验证** |
| S&P 500 成员 PIT 历史 | 无此端点 | **缺口，需补源（见下）** |
| GICS Sector 分类 | fundamentals（daily） | 待验证来源与是否 GICS |
| 退市/并购对价 | 未知 | 待 token 验证 |
| SPY 总收益同口径 | 与股票同端点 | 路径存在，内容待验证 |

总收益口径：目标是用原始价格 + 公司行为按 target-spec §3.1–3.3 自行计算（除息日收盘再投资、仅计有权份额），不依赖供应商 TR 序列；Tiingo 的 adjusted 字段只作交叉核对。

## 补充源：S&P 500 成分历史

- **EODHD Indices Historical Constituents Data API**：官方页面确认覆盖 S&P 500 / 400 / 600 / 100 及 20 个行业指数，提供指数列表、成分明细、历史变更，JSON 格式；页面标价 $29.99/月。来源：[eodhd.com](https://eodhd.com/financial-apis/sp-and-dow-jones-indices-historical-constituents-data-api)
- Wikipedia [List of S&P 500 companies](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies)：现行成分表 + 变更记录段，可作交叉核对（本次已抓取成功）；PIT 严谨性不足，不作为正式采样源。

## 待账号验证（注册 Tiingo 后）

1. dailyPrices 响应字段与调整口径（raw / adjClose / divCash / splitFactor 的确切定义）
2. fundamentals 的 sector 字段来源、是否 GICS、粒度（Sector/Industry）
3. 退市 ticker 的历史覆盖与末期数据可得性
4. EOD 发布延迟：周五收盘数据在周六 06:00 ET cutoff 前是否稳定可用（协议可行性关键）
5. 定价 tier、请求/频率限额、ticker 数上限（官网 SPA，浏览器确认：[tiingo.com/pricing](https://www.tiingo.com/pricing)）
6. Terms of Use 对缓存、留存、转发给 LLM 供应商的授权约束
7. SPY / ETF 是否计入标准 ticker 配额

## 结论

Tiingo 可作主行情源（原始 OHLC + 公司行为自行计算符合协议口径）；**S&P 500 成分 PIT 快照与 GICS 分类是缺口**，EODHD constituents API（$29.99/月）为当前最小付费候选。正式采用前需注册账号完成上列字段级验证，并确认授权条款覆盖"数据经 LLM Gateway 发送给模型供应商"的使用方式（架构 §10 要求）。
