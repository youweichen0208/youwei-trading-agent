# Tiingo 字段级验证记录（账号 token 实测）

日期：2026-09-27。方法：真实 token 实测（evaluation 套餐，注册当日）；token 存于本地 `.env`（gitignore，不入库、不入文档）。对照 [tiingo-capabilities](tiingo-capabilities.md) 的「待账号验证」清单逐项执行；需求依据 [target-spec.v1](../protocols/target-spec.v1.md)（总收益自算：原始 OHLC + 公司行为，不依赖供应商 TR 序列）。

## 结论一览

| # | 待验证项 | 结果 |
| --- | --- | --- |
| 1 | dailyPrices 字段与调整口径 | ✅ 字段全集在（raw + adj + divCash + splitFactor）；拆分精确、分红复权有 ~1e-5 级偏差（见 §2） |
| 2 | corporate-actions 端点 | ❌ 当前套餐 403（distributions/splits 均不含）；**日线行内嵌 divCash/splitFactor 可替代**（协议口径本就自算） |
| 3 | fundamentals/meta sector 取值 | ✅ `tickers=`（复数）过滤下可用：AAPL sector=Technology / industry=Consumer Electronics / sicCode=3571——**SIC 派生确认**；inactive ticker 的 sector 字段不可用 |
| 4 | 退市 ticker 覆盖 | ⚠️ 三种形态：干净保留（ATVI/TWTR）、**幽灵行**（SGEN）、完全缺失（SIVB 404）——见 §4 |
| 5 | EOD 发布延迟 | ◐ 初步证据支持：周五（2026-09-25）收盘数据周日已在；fundamentals `dailyLastUpdated` = 周五 22:15 ET；**待一次周五晚→周六 06:00 ET 实盘观测定案**（§5） |
| 6 | 定价 tier / 限额 | ◐ API 内容标记为 free/evaluation；响应无 rate-limit 头；精确配额需网页端控制台确认（用户任务） |
| 7 | Terms of Use（缓存/留存/LLM 转发） | ❌ 页面为 SPA，CLI 不可读；**付费套餐购买时浏览器确认**（此前研究已确认 Starter/Trial 禁止持久保存，[us-data-vendor-selection](us-data-vendor-selection.md)） |
| 8 | SPY / ETF 同端点 | ✅ `/tiingo/daily/SPY/prices` 正常返回，无特殊处理 |

## 1. dailyPrices 字段（AAPL，2026-09-21 ~ 09-25）

```json
{"date":"2026-09-25T00:00:00.000Z","close":341.07,"high":341.67,"low":334.53,
 "open":336.04,"volume":30002507,"adjClose":341.07,"adjHigh":341.67,
 "adjLow":334.53,"adjOpen":336.04,"adjVolume":30002507,"divCash":0.0,"splitFactor":1.0}
```

- 最新数据日期 = 最近交易日（周五 09-25）；`/tiingo/daily/{ticker}` 元数据含 `startDate`/`endDate`/`exchangeCode`，**无 updated-at 时间戳**（源时间唯一证据在 fundamentals 侧，见 §5）。
- 除息/拆分事件直接内嵌在对应交易日的行上（`divCash`、`splitFactor`），最近一次除息日之后 adj==raw。

## 2. 调整口径数学验证

**拆分（精确）**：AAPL 4:1（2020-08-31）：`splitFactor=4.0`，原始收盘 499.23（08-28）→ 129.04（08-31）；499.23/4=124.81，当日实际 +3.4% 市场波动，一致；adjClose 序列跨拆分正确回除（499.23/4 × 后续分红因子 ≈ 120.97 = 实测 adjClose 08-28）。

**分红（约 1e-5 偏差）**：2026-08-10 除息 div=0.27：

```text
实际因子 adjClose(08-07)/close(08-07)      = 0.999124882507
标准口径 1 − div/prevClose(313.33)         = 0.999138288705   ✗
1 − div/exClose(308.26)                    = 0.999124116006   （最接近）
反推内部股息（以 exClose 为分母）           ≈ 0.2697           （字段显示 0.27）
```

2026-05-11 除息同样模式（反推 ≈0.2697）。**判定：Tiingo 内部复权所用股息/分母与展示值存在舍入差异，adjClose 不可作为精确基准**。协议本就规定用原始 OHLC + divCash/splitFactor 自算总收益（target-spec §3.1–3.3），adjClose 仅作容差交叉核对（建议 ±1e-4 相对容差）。

## 3. fundamentals / corporate-actions 端点可用性

| 端点 | 状态 | 说明 |
| --- | --- | --- |
| `/tiingo/fundamentals/{t}/daily` | ✅ 200 | marketCap、peRatio、pbRatio、trailingPEG1Y 等日频 |
| `/tiingo/fundamentals/meta?tickers=AAPL` | ✅ 200（562B） | **`tickers=` 复数参数才过滤**；单数参数返回全量 ~14.5MB 且字段标记受限 |
| `/tiingo/fundamentals/meta`（无过滤） | ⚠️ 200 但 ~14.5MB | 字段值 "Field not available for free/evaluation"（bulk 模式受限） |
| `/tiingo/corporate-actions/{t}/distributions` | ❌ 403 空体 | 套餐不含（连测 4 次一致） |
| `/tiingo/corporate-actions/{t}/splits` | ❌ 403 空体 | 同上 |

fundamentals/meta（按 ticker）关键字段：`permaTicker`（如 US000000000038）、`isActive`、`sector/industry/sicCode/sicSector`（active ticker 可用）、`dailyLastUpdated`、`statementLastUpdated`。

## 4. 退市覆盖：三种形态（S04 证券主数据的直接输入）

| Ticker | 形态 | 证据 |
| --- | --- | --- |
| ATVI（2023-10 被 Microsoft 收购） | 干净保留 | meta `endDate=2023-10-13`、`isActive=false`，完整历史可查 |
| TWTR（2022-10 退市） | 干净保留 | `endDate=2022-10-28`、`isActive=false` |
| SGEN（2023-12 被 Pfizer 收购） | **幽灵行** | `endDate=2026-09-25`（看似活跃！）；收购价 228.74 冻结、**volume=0**、仅交易日生成、周末不生成；`isActive=false` 是唯一可靠标志 |
| SIVB（2023 破产退市） | **完全缺失** | daily 与 fundamentals 均 404，无任何数据 |

工程结论：
1. **不能用 `endDate` 新鲜度判断存活**；`isActive`（fundamentals meta）可用但 SIVB 型连元数据都没有——退市/存续真相需要独立主数据源，Tiingo 只作行情覆盖。
2. **幽灵行必须检测**（连续 volume=0 + 价格冻结），否则停牌/退市证券会被当作正常交易进入 TargetSpec 的收益计算。S00 known-answer-cases 的停牌/退市条款应引用本发现。
3. SIVB 型缺口登记为供应商覆盖限制；对 2026 登记日的 S&P 500 panel 影响待 S04 采样时逐一核对。

## 5. EOD 时效（协议可行性）

- 现状证据：周五 2026-09-25 收盘 OHLCV 在周日查询时已完整在库；fundamentals `dailyLastUpdated = 2026-09-26T02:15:41Z`（= 周五 22:15 ET）表明日频管线周五晚完成。
- 定案所需的实盘观测（下次周五 2026-10-02 执行）：

```bash
# 周五 18:00 ET 起每 30 分钟轮询，记录endDate首次达到周五的时刻：
curl -sS "https://api.tiingo.com/tiingo/daily/SPY?token=$YOUWEI_TIINGO_TOKEN" | jq .endDate
# 验收线：周六 06:00 ET 前 endDate == 周五日期（协议 cutoff）
```

S04 首次真实采集（周五晚定时任务）本身即完成该验证；在此之前不据此承诺延迟。

## 6. 套餐与许可（未闭环项）

- 当前 token 为 **free/evaluation**（响应内容标记）；无 rate-limit 响应头，精确配额在网页控制台（用户确认）。
- corporate-actions 端点不在当前套餐；**日线内嵌 divCash/splitFactor 已满足协议总收益计算**，批量回补便利性非必需。
- 生产采用前（S04 正式快照）：升级付费套餐时浏览器确认 ToS 的留存、缓存与「数据经 LLM Gateway 转发」授权（[us-data-vendor-selection](us-data-vendor-selection.md) 已记录 Starter/Trial 禁止持久保存、付费计划停订后要求删除等要点）。

## 7. 对架构/计划的更新

- 「Tiingo 可作主行情源」判定维持：原始 OHLC + 行内公司行为字段满足 target-spec 自算总收益口径；adjClose 降级为容差交叉核对。
- GICS 与 S&P 500 成分 PIT 快照缺口结论不变（sector 为 SIC 派生；成分源另配）。
- S04 数据服务新增验收点：幽灵行检测（volume=0 连续性）、退市状态不依赖 Tiingo endDate、SIVB 型缺口登记。
