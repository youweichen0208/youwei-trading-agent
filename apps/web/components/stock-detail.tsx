"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/client";

export function StockDetail({
  symbol,
  onAnalyze,
}: {
  symbol: string;
  onAnalyze: (symbol: string) => void;
}) {
  const [data, setData] = useState<any>(null),
    [research, setResearch] = useState<any>(null),
    [error, setError] = useState(""),
    [attempt, retry] = useState(0);
  const [translation,setTranslation]=useState<any>(null);
  const [period, setPeriod] = useState(100);
  useEffect(() => {
    let active = true;
    setData(null);
    setTranslation(null);
    void api(`stocks/${encodeURIComponent(symbol)}/news-zh`).then(value=>{if(active)setTranslation(value);}).catch(()=>{if(active)setTranslation({unavailable:true,items:[]});});
    setResearch(null);
    setError("");
    void Promise.allSettled([
      api(`stocks/${encodeURIComponent(symbol)}/overview`),
      api(`stocks/${encodeURIComponent(symbol)}`),
    ]).then(([market, saved]) => {
      if (!active) return;
      if (market.status === "fulfilled") setData(market.value);
      else setError("行情与新闻加载失败，请重试。");
      setResearch(saved.status === "fulfilled" ? saved.value : { error: true });
    });
    return () => {
      active = false;
    };
  }, [symbol, attempt]);
  const allBars = (data?.market?.bars ?? []).filter(
    (b: any) => Number.isFinite(Number(b.close)) && Number(b.close) > 0,
  );
  const cutoff = new Date(
    (allBars.at(-1)?.session ?? new Date().toISOString().slice(0, 10)) +
      "T00:00:00Z",
  );
  cutoff.setUTCDate(cutoff.getUTCDate() - period);
  const bars = allBars.filter(
    (b: any) => new Date(b.session + "T00:00:00Z") >= cutoff,
  );
  const prices = bars.map((b: any) => Number(b.close));
  const min = Math.min(...prices),
    max = Math.max(...prices);
  const points = prices
    .map(
      (v: number, i: number) =>
        `${40 + (i / Math.max(prices.length - 1, 1)) * 720},${220 - ((v - min) / (max - min || 1)) * 180}`,
    )
    .join(" ");
  const change =
    prices.length > 1 ? (prices.at(-1) / prices[0] - 1) * 100 : null;
  const news = data?.news?.documents ?? [];
  return (
    <div className="stock-detail">
      <header className="stock-detail-header">
        <div>
          <p className="stock-breadcrumb">
            自选股 <span>/</span> 研究概览
          </p>
          <div className="stock-identity">
            <span className="stock-monogram" aria-hidden="true">
              {symbol.slice(0, 1)}
            </span>
            <div>
              <h1>{symbol}</h1>
              <p>美股 · 行情与研究</p>
            </div>
          </div>
        </div>
        <button className="primary" onClick={() => onAnalyze(symbol)}>
          让 Agent 分析
        </button>
      </header>
      {error && (
        <p role="alert">
          {error} <button onClick={() => retry((v) => v + 1)}>重试</button>
        </p>
      )}
      {!data && !error && <p role="status">正在获取走势与新闻…</p>}
      {data && (
        <>
          <div className="stock-overview-grid">
            <section className="stock-trend">
              <div className="stock-section-title">
                <h2>
                  价格走势 <small>日线</small>
                </h2>
                <div className="stock-periods" aria-label="走势时间范围">
                  {[
                    [30, "1 个月"],
                    [90, "3 个月"],
                    [100, "全部"],
                  ].map(([days, label]) => (
                    <button
                      key={days}
                      aria-pressed={period === days}
                      onClick={() => setPeriod(Number(days))}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {prices.length > 1 ? (
                <>
                  <div className="stock-price">
                    <strong>
                      {prices.at(-1).toFixed(2)} <small>USD</small>
                    </strong>
                    <span
                      className={
                        change! >= 0
                          ? "stock-change positive"
                          : "stock-change negative"
                      }
                    >
                      {change! >= 0 ? "+" : ""}
                      {change!.toFixed(2)}% <small>区间涨跌</small>
                    </span>
                  </div>
                  <p>最近日线：{bars.at(-1).session} · 拆股复权</p>
                  <svg
                    viewBox="0 0 800 270"
                    role="img"
                    aria-label={`${symbol} 日线收盘走势，区间涨跌 ${change!.toFixed(2)}%`}
                  >
                    <line x1="40" x2="760" y1="220" y2="220" stroke="#e4e8e5" />
                    <line x1="40" x2="760" y1="40" y2="40" stroke="#e4e8e5" />
                    <polygon
                      points={`40,220 ${points} 760,220`}
                      fill="url(#price-fill)"
                    />
                    <defs>
                      <linearGradient
                        id="price-fill"
                        x1="0"
                        y1="0"
                        x2="0"
                        y2="1"
                      >
                        <stop
                          offset="0%"
                          stopColor="#00875a"
                          stopOpacity=".14"
                        />
                        <stop
                          offset="100%"
                          stopColor="#00875a"
                          stopOpacity="0"
                        />
                      </linearGradient>
                    </defs>
                    <polyline
                      points={points}
                      fill="none"
                      stroke="#00875a"
                      strokeWidth="2.5"
                    />
                    <text x="40" y="258">
                      {bars[0].session}
                    </text>
                    <text x="760" y="258" textAnchor="end">
                      {bars.at(-1).session}
                    </text>
                    <text x="40" y="28">
                      {max.toFixed(2)} USD
                    </text>
                  </svg>
                  <details>
                    <summary>查看每日价格数据</summary>
                    <div className="stock-data-table">
                      <table>
                        <thead>
                          <tr>
                            <th>日期</th>
                            <th>收盘（USD）</th>
                          </tr>
                        </thead>
                        <tbody>
                          {bars.map((b: any) => (
                            <tr key={b.session}>
                              <td>{b.session}</td>
                              <td>{b.close}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                </>
              ) : (
                <p>
                  {data.market.status === "unavailable"
                    ? "行情暂不可用，请稍后刷新。"
                    : "暂未取得足够的日线数据。"}
                </p>
              )}
              <p className="stock-source">
                来源：{data.market.source ?? "Alpaca"} ·{" "}
                {data.market.feed?.toUpperCase() ?? "行情不可用"}
                {data.market.feed === "iex" ? "（仅 IEX 市场覆盖）" : ""} ·
                日线不代表实时成交价
              </p>
            </section>{" "}
            <section className="stock-analysis">
              <div className="stock-agent-mark">
                H <span>HERMES AGENT</span>
              </div>
              <h2>把信息变成研究</h2>
              <p className="stock-agent-intro">
                从价格变化出发，连接新闻与公司基本面。
              </p>
              {research?.error ? (
                <p>研究档案加载失败，请刷新重试。</p>
              ) : research?.dossier?.summary ? (
                <p className="stock-summary">{research.dossier.summary}</p>
              ) : (
                <p>
                  暂无已保存的分析。让 Agent
                  结合走势与新闻进行研究，结果会保留在对话中。
                </p>
              )}
              <div className="stock-agent-topics">
                <span>
                  01 <b>走势与关键变化</b>
                </span>
                <span>
                  02 <b>新闻与潜在影响</b>
                </span>
                <span>
                  03 <b>证据与待验证问题</b>
                </span>
              </div>
              <button className="primary" onClick={() => onAnalyze(symbol)}>
                结合新闻与走势提问 →
              </button>
              <p className="stock-source">
                已有分析可能早于当前行情，请核对原研究中的数据日期。
              </p>
            </section>
          </div>
          <div className="stock-reading-grid">
            <section>
              <div className="stock-section-title">
                <div>
                  <h2>
                    市场动态 <small>近两周 · 相关新闻</small>
                  </h2>
                </div>
                <button onClick={() => retry((v) => v + 1)}>刷新数据 ↻</button>
              </div>
              <div className="stock-news-grid">
                {!news.length && (
                  <p>
                    {data.news.status === "unavailable"
                      ? "新闻暂不可用，请稍后刷新。"
                      : "该时间范围暂无相关新闻。"}
                  </p>
                )}
                {news.slice(0, 12).map((n: any) => {
                  const d = n.payload ?? {};
                  const chinese=translation?.items?.find((item:any)=>item.id===n.sourceKey);
                  const safe = /^https?:\/\//.test(d.url ?? "");
                  return (
                    <article
                      className="stock-news"
                      key={n.sourceKey ?? d.url ?? d.title}
                    >
                      <p>
                        {d.publisher ?? "Alpaca News"} ·{" "}
                        {n.publishedAt
                          ? new Date(n.publishedAt).toLocaleDateString("zh-CN")
                          : "发布时间未核实"}
                      </p>
                      <h3>{chinese?.title ?? (translation ? "中文翻译暂不可用" : "正在翻译新闻…")}</h3>
                      <p>{chinese?.summary ?? (translation ? "请稍后刷新重试，或展开查看原文。" : "中文标题和摘要将在站内显示，无需访问外网。")}</p>
                      <details className="news-original"><summary>查看英文原文</summary><h3>{d.title}</h3><p>{d.summary}</p></details>
                      <footer className="news-source-link">来源：{d.publisher ?? "Alpaca News"} {safe && <a href={d.url} target="_blank" rel="noreferrer">阅读渠道原文 ↗</a>}<span>{chinese ? "机器翻译 · 以原文为准" : ""}</span></footer>
                    </article>
                  );
                })}
              </div>
              {data.news.status === "partial" && (
                <p>仅展示数据源返回的部分新闻。</p>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
