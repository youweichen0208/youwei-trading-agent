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

- 本次响应包含当前成分 **503** 个证券条目；不等于503家不同发行人，也不能仅凭条目数量证明该响应完整反映登记时点的官方成员资格。
- 历史成员 **822** 条（含已退出者）；其中 `IsDelisted=1` **176** 条、带 `EndDate` **319** 条（非退市的调出，如再平衡/板块变更，也有 EndDate 但 IsDelisted=0）。
- 历史覆盖约 12 年（EndDate 分布 2008–2026），与产品页"up to 12 years"一致。

## 关键字段语义

- **成员历史**：`StartDate` / `EndDate` 给出供应商记录的成员区间；`IsActiveNow` 表示供应商当前在册状态。区间边界、覆盖完整性、变更生效时刻与源版本尚不足以支持“任一历史时点正式 PIT 已验收”的结论；当前采集代码没有执行历史成员重建，也没有取得历史板块分类。
- **退市标志**：`IsDelisted=1` 表示从交易所退市（多数为并购收购），`EndDate` 无 `IsDelisted` 为仍在交易但调出指数——两者区分有用。
- **板块/行业**：本次观察到11个板块 `Financial Services / Consumer Defensive / Industrials / Technology / Utilities / Real Estate / Healthcare / Consumer Cyclical / Basic Materials / Energy / Communication Services`，数量分布20–86。分类规则、逐证券归属及历史变化未与官方 GICS 核对，**不能从11个名称推导结构或归属等价**。建议按原始 `Sector` 值登记 `eodhd_sector`；这属于分类依据变更，需要协议新版本，不能仅改显示名称。GICS 本身包含企业活动的统一分类规则，见 [MSCI 官方说明](https://www.msci.com/indexes/index-resources/gics)。

## 退市真相交叉验证（对照 Tiingo 三形态）

| ticker | 事件 | Tiingo（行情源） | EODHD（指数成员） |
| --- | --- | --- | --- |
| SIVB | 2023-03 破产退市 | 完全 404 无数据 | `EndDate 2023-03-15, IsDelisted=1` ✓ |
| ATVI | 2023-10 微软收购 | 干净保留 | `EndDate 2023-10-18, IsDelisted=1` ✓ |
| TWTR | 2022-10 退市 | 干净保留 | `EndDate 2022-11-01, IsDelisted=1` ✓ |
| SGEN | 2023-12 辉瑞收购 | 幽灵行（冻结价 volume=0） | 历史列表中缺失 |

结论：EODHD 提供了 Tiingo 行情之外的交叉核对线索，且已发现覆盖缺口（SGEN）。两个 API 不足以证明底层来源独立；`EndDate` 是指数移除日期，不能替代退市生效日、并购对价或终值证据。现有样例不构成完整退市真相源的验收，协议的 unresolved+依据路径继续适用。

## 未决项

1. **快照时间**：本次 `comp` 响应未提供可用作分类生效证据的时间字段。分别保存真实观察/入库/可用时刻；来源生效时间未知则保留未知。`frame_as_of` 日期不能替代精确观察时刻或证明当日官方成员资格。
2. **历史变更事件列表**：`changes/hist/history` 子路径均返回 "Not found"；当前只能从 `HistoricalTickerComponents` 的 StartDate/EndDate 区间重建，无独立"增删事件带日期"端点（如需精确变更公告时点需另寻）。
3. **分类方案**：推荐采用供应商分类，候选修订见 [campaign-policy.v2](../protocols/campaign-policy.v2.md)。v1 保留；未获得实际 release 批准，不能把候选文档当作正式登记。
4. **限额**：Marketplace 产品的精确每日限额待确认。

## 2026-09-28 协议选择复核

官方 [Marketplace 产品页](https://eodhd.com/lp/spglobal)描述当前成员与历史增删记录；这不证明当前 `Sector` 值具有历史版本，也不授予客户任意留存或转发权限。本次只查阅官方公开资料与仓库代码，未再次调用付费端点。

代码层仍需补齐正式登记验收：`data/panel.py` 为新证券生成随机 ID，并无依据写入1990年有效期；当前复算测试只覆盖同库复用。需冻结并恢复原 ID 映射与规范化 frame。`build_panel_manifest` 尚未核对源对象、精确时间、frame/sample/selected 的一致性，`register_campaign` 仅校验 manifest 类型；这些是工程待办，不是预算或人工批准能够代替的检查。一次性测试库中的短 hash 是冒烟记录，不代替完整可恢复的正式登记包。
