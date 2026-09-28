# EODHD S&P 500 成分数据实测（unicornbay/spglobal Marketplace 产品）

核实日期：2026-09-28。方法：真实 token 实测（用户已购买 Indices Historical Constituents Data 产品）。token 存于本地 `.env`（gitignore，不入库、不入文档）。对照 [campaign-policy.v1](../protocols/campaign-policy.v1.md) 第 2 节与 [S01 数据账号建议](us-data-vendor-selection.md)。

## 端点与接入

- 真实端点在 **Marketplace 命名空间**，不是标准 `/api/fundamentals/`：

```text
https://eodhd.com/api/mp/unicornbay/spglobal/comp/{INDEX}?api_token=TOKEN&fmt=json
```

- 指数代码格式 `GSPC.INDX`（后缀 `.INDX`）。已实测返回真实 S&P 500 成分数据。
- 认证：`api_token` 参数；`fmt=json` 生效。
- **账号档位陷阱**：`/api/user` 的 `subscriptionType` 仍显示 `free`、`dailyRateLimit=20`——但 Marketplace 产品访问是**独立于该字段**的，标准 `/api/fundamentals/GSPC.INDX` 返回 "Only EOD data allowed for free users"，而 Marketplace 路径正常。判断"是否买到"要以**实际端点返回**为准，不能只看 `/api/user`。精确限额未在响应头出现，需网页控制台确认（用户任务）。

## 响应结构（`comp/GSPC.INDX`）

```text
General                      指数元数据（Code/Name/Exchange/MarketCap/货币/国家/OpenFigi）
Components                   当前成分，dict 键 0..N-1 连续；每项 {Code, Exchange, Name, Sector, Industry, Weight}
HistoricalTickerComponents   历史成员，dict 键 0..N-1；每项 {Code, Name, StartDate, EndDate, IsActiveNow, IsDelisted}
```

- 当前成分 **503** 家（S&P 500 当前实际 503，非恰好 500——抽样总体不得假定恰好 500，与协议一致）。
- 历史成员 **822** 条（含已退出者）；其中 `IsDelisted=1` **176** 条、带 `EndDate` **319** 条（非退市的调出，如再平衡/板块变更，也有 EndDate 但 IsDelisted=0）。
- 历史覆盖约 12 年（EndDate 分布 2008–2026），与产品页"up to 12 years"一致。

## 关键字段语义

- **PIT 成员**：`StartDate` / `EndDate` 给出成员区间；`IsActiveNow` 表示当前是否在册。正式查询据此重建任一时点成员快照。
- **退市标志**：`IsDelisted=1` 表示从交易所退市（多数为并购收购），`EndDate` 无 `IsDelisted` 为仍在交易但调出指数——两者区分有用。
- **板块/行业**：11 个板块 `Financial Services / Consumer Defensive / Industrials / Technology / Utilities / Real Estate / Healthcare / Consumer Cyclical / Basic Materials / Energy / Communication Services`，数量分布 20–86。**这是 GICS 结构（11 个板块一一对应）但命名非官方 GICS**（"Financial Services" vs GICS "Financials"、"Technology" vs "Information Technology"、"Consumer Defensive" vs "Consumer Staples"）。**GICS 决策的关键输入**：结构等价、标签不同——若协议要求"官方 GICS 命名"则不满足；若接受"GICS 结构的板块分层（声明来源命名）"则需显式协议修订。

## 退市真相交叉验证（对照 Tiingo 三形态）

| ticker | 事件 | Tiingo（行情源） | EODHD（指数成员） |
| --- | --- | --- | --- |
| SIVB | 2023-03 破产退市 | 完全 404 无数据 | `EndDate 2023-03-15, IsDelisted=1` ✓ |
| ATVI | 2023-10 微软收购 | 干净保留 | `EndDate 2023-10-18, IsDelisted=1` ✓ |
| TWTR | 2022-10 退市 | 干净保留 | `EndDate 2022-11-01, IsDelisted=1` ✓ |
| SGEN | 2023-12 辉瑞收购 | 幽灵行（冻结价 volume=0） | 历史列表中缺失 |

结论：EODHD 指数成员数据与 Tiingo 行情**互相独立**，可充当退市真相源（打破"行情源自己当真相"的循环），但不绝对完整（SGEN 型缺失）——协议既定的 unresolved+依据路径继续兜底。

## 未决项

1. **快照 as-of**：`comp` 响应无日期字段；"当前成分"的时点以抓取时刻为 frame_as_of 记录，供应商数据内部时点待网页控制台/文档确认。
2. **历史变更事件列表**：`changes/hist/history` 子路径均返回 "Not found"；当前只能从 `HistoricalTickerComponents` 的 StartDate/EndDate 区间重建，无独立"增删事件带日期"端点（如需精确变更公告时点需另寻）。
3. **GICS 命名**：见上，待用户决策（协议修订 vs 接受非官方标签）。
4. **限额**：Marketplace 产品的精确每日限额待确认。
