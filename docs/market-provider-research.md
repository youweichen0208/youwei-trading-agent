# Market provider qualification：Massive / Finnhub

核验日期：2026-10-27。对应 TASK-039 / TASK-042；这是公开文档研究，不是账户授权、真实 API 联调或两年数据验收证据。尚未选择供应商，未使用密钥、购买套餐或请求付费数据。

## 建议与边界

建议先准备默认关闭的 Massive 候选适配器：其公开接口明确提供未拆股调整的 OHLCV，参数和分页规则较完整。此建议只确定可验证的实现方向，不替用户选择供应商；Finnhub 的 RAW 日线语义仍待官方确认。两者均不能凭文档直接进入 LIVE_RESEARCH。

继续遵守 [GLOSSARY](../GLOSSARY.md) 与架构任务书 §1.2 / §6.2 的 `eod-price-v1`：冻结 RAW close，Alpha 用 Decimal 计算 split-only 收益；不以股息总回报数据替换价格，不补齐缺失交易日，不把当前修订数据伪装为历史已知数据。真实完成情况以 [implementation-status](implementation-status.md) 为准。

## Massive：可直接落实的接口输入

| 项目 | 官方事实 / 候选实现约束 |
| --- | --- |
| 日线 | `GET /v2/aggs/ticker/NVDA/range/1/day/{from}/{to}`，日期 `YYYY-MM-DD`，显式 `adjusted=false&sort=asc`；默认是拆股调整，必须检查响应 `adjusted=false`。 |
| 字段与时间 | `results` 中 `o/h/l/c/v` 是数值；`t` 是窗口起点的 Unix 毫秒，窗口以 ET 定义。用 `America/New_York` 归属交易日，不能把 `t` 当发布时间。 |
| 缺口 / 分页 | 无合格成交时不生成 bar；`next_url` 指向下一页。`limit` 默认 5000、最大 50000，限制底层 aggregates 数，并非完整历史保证。 |

以上来自 [Custom Bars 官方文档](https://massive.com/docs/rest/stocks/aggregates/custom-bars)。实现时保留数值的 Decimal 精度、原始响应和请求范围；对照交易所 session calendar 验证完整性。每日窗口涉及盘前盘后，必须额外核验候选 daily close 与产品要求的收盘价一致。

当前拆股接口是 `GET /stocks/v1/splits`；导航已将旧接口标为 Deprecated。使用 `ticker=NVDA`、`execution_date` 日期条件、`sort=execution_date.asc`；每页默认 100、上限 5000，跟随 `next_url`。事件字段包含 `id`、`execution_date`、`split_from`（旧股数）、`split_to`（新股数）、`adjustment_type`。后者包含 forward_split、reverse_split、stock_dividend；未经领域规则支持的类型必须显式拒绝。`historical_adjustment_factor` 是累计调整系数，不能直接当作单次事件比例，也不能用来覆盖 RAW 冻结价格。部分事件字段在 schema 中可选，缺字段不能默认为 1:1。[Splits 官方文档](https://massive.com/docs/rest/stocks/corporate-actions/splits)

API host 为 `https://api.massive.com`，支持 `Authorization: Bearer <key>` 或 `apiKey` 查询参数；候选实现优先用 header，密钥只在服务端。分页 URL 必须限制到批准的 HTTPS host/path，避免向任意 URL 转发凭证；日志不得包含 token。[REST Quickstart](https://massive.com/docs/rest/quickstart)

官方月付个人套餐页面列出：Basic $0/月、5 次/分钟、两年历史、EOD；Starter $29/月、五年历史、15 分钟延迟；Developer $79/月、十年历史、15 分钟延迟；Advanced $199/月、20+ 年历史、实时；后三者标示 unlimited API calls。页面同时标注 individual use，不能据此推定商业展示、再分发或本项目全部用途已获许可。实际账户权益、使用条款、服务端缓存与展示权限仍需确认；不根据免费套餐描述自动开通。[Stocks Pricing](https://massive.com/pricing?product=stocks)

## Finnhub：保留候选，先补 RAW 语义证据

官方 Python SDK 确认 `GET /stock/candle` 使用 `symbol/resolution/from/to`，`GET /stock/split` 使用 `symbol/from/to`；官方 Go 文档列出相同资源。SDK 存在端点不证明账户有两年权限，也不证明其日线是 RAW。[官方 Python SDK](https://github.com/Finnhub-Stock-API/finnhub-python/blob/master/finnhub/client.py)、[官方 Go API 文档](https://github.com/Finnhub-Stock-API/finnhub-go/blob/master/docs/DefaultApi.md)

本次公开网页读取未取得 [Stock Candles](https://finnhub.io/docs/api/stock-candles)、[Stock Splits](https://finnhub.io/docs/api/stock-splits)、[Rate Limit](https://finnhub.io/docs/api/rate-limit) 的可解析正文。因此 RAW / adjusted 切换、日线时间戳的交易日语义、缺失返回值、窗口/分页上限、认证方式、当前套餐价格、速率及许可均未完成核验，不引用第三方教程补作事实。接入前需官方可归档规格或供应商确认；不得猜测增加 `adjusted=false` 就能得到 RAW，也不得用调整价反推 RAW 后宣称原始价格。

## 进入 LIVE 前的资格门槛

1. **账户与许可**：记录用户选定供应商、实际 entitlement、两年起止边界、用途许可、限流与成本约束；文档中的两年历史不是实际抓取证明。
2. **交易日与数据样本**：真实抓取 NVDA 指定两年窗口，验证每页完成、无重复/越界、所有预期 session、DST/半日市及最新已完成 session。无数据、权限错误、429 和超时必须明确 unavailable；不回退旧收盘或 fixtures。
3. **公司行动覆盖**：完整分页成功且无 split 也不自动证明覆盖。另存可审计的覆盖范围、抓取证据与修订政策后才能形成 `actions_verified_through`；缺覆盖不得结算。两年窗口未包含拆股时，需另选有官方事件证据的窗口验证比例方向。
4. **可用时间与修订**：上述 Massive 响应 schema 未提供历史 `available_at` 或逐条修订版本。没有额外可靠证据时使用实际抓取时间作为保守可用时间，保留内容哈希与不可变快照；后续更正追加版本。`request_id` 是请求标识，不是数据修订版本。回填不得据此声称历史 point-in-time 可得性。
5. **验收范围**：候选适配器的 mock 测试只证明协议处理。真实凭证联调、两年样本、公司行动资格、许可和相关 DoD 未齐备时，TASK-039 / TASK-042 仍不能标记 DONE。

## 2026-10-28 日历例外日期核对

已将 Nasdaq 官方公布的 2026 年 10 个休市日、11 月 27 日与 12 月 24 日的美东 13:00 提前收盘，以及 2025 年 1 月 9 日特殊休市写入独立回归 fixture，保留来源链接。来源为 [Nasdaq Trader 年度日历](https://nasdaqtrader.com/Trader.aspx?id=Calendar)及 [2024-12-30 特殊休市公告](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2024-87)。

`test_calendar_reference.py` 共 14 项通过，包括跨特殊休市的五 session 目标日和提前收盘前后 latest_closed 边界。期望值来自公告，不是从待测 calendar 生成。此证据仅覆盖 fixture 所列日期；不能证明 1990–2035 全区间、盘中停牌或未来临时公告均正确。实现仍使用 XNYS proxy，真实供应商与 Nasdaq 的完整资格验收仍待完成，未改变 acquisition-quality 的 UNVERIFIED 状态。
