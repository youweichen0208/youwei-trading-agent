"use client";
import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, ColorType, HistogramSeries, LineSeries, createChart, type Time } from "lightweight-charts";
import { chartDataSchema, type ChartData } from "@yta/contracts";
import { api, dateTime } from "../lib/api";

export function StockChart({ commandId }: { commandId?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<ChartData>(); const [indicators, setIndicators] = useState<ChartData>();
  const [error, setError] = useState(""); const [retry, setRetry] = useState(0);
  const [window, setWindow] = useState(90);
  const [overlays, setOverlays] = useState<string[]>(["SMA20"]);
  useEffect(() => {
    const abort = new AbortController(); setError(""); setData(undefined); setIndicators(undefined);
    void api<unknown>("/stocks/NVDA/indicators", {signal: abort.signal}).then(response => {
      const parsed = chartDataSchema.safeParse(response);
      if (!parsed.success) throw new Error("行情与指标口径不一致，请重新加载。");
      setData(parsed.data); setIndicators(parsed.data);
    }).catch(e => { if (!abort.signal.aborted) setError(e.message); });
    return () => abort.abort();
  }, [retry]);
  useEffect(() => {
    if (!container.current || !data || !indicators) return;
    const chart = createChart(container.current, {
      autoSize: true, layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#657589", fontFamily: "SFMono-Regular, Consolas, monospace", fontSize: 10, attributionLogo: true },
      grid: { vertLines: { color: "#f1f4f7" }, horzLines: { color: "#edf1f5" } },
      rightPriceScale: { borderVisible: false }, timeScale: { borderColor: "#e4eaf0", timeVisible: false },
    });
    const candles = chart.addSeries(CandlestickSeries, { upColor: "#18796e", downColor: "#c76657", wickUpColor: "#18796e", wickDownColor: "#c76657", borderVisible: false });
    candles.setData(data.bars.map(b => ({ time: b.session as Time, open: Number(b.open), high: Number(b.high), low: Number(b.low), close: Number(b.close) })));
    const volumes = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, color: "#d5e4e6" }, 1);
    volumes.setData(data.bars.map(b => ({ time: b.session as Time, value: Number(b.volume) })));
    const line = (name: string, pane: number, color: string) => {
      const series = chart.addSeries(LineSeries, { color, lineWidth: 1, title: name, priceLineVisible: false, lastValueVisible: false }, pane);
      series.setData(indicators.sessions.flatMap((date, i) => { const v = indicators.series[name]?.[i]; return v == null ? [{ time: date as Time }] : [{ time: date as Time, value: Number(v) }]; }));
    };
    if (overlays.includes("SMA20")) line("SMA20", 0, "#bc8a45");
    if (overlays.includes("BOLL")) { line("BOLL_UPPER", 0, "#8aa5bd"); line("BOLL_MIDDLE", 0, "#657589"); line("BOLL_LOWER", 0, "#8aa5bd"); }
    line("RSI14", 2, "#7b6ba5"); line("MACD", 3, "#2c739d"); line("MACD_SIGNAL", 3, "#bc8a45");
    const panes = chart.panes(); panes[0]?.setHeight(220); panes[1]?.setHeight(60); panes[2]?.setHeight(80); panes[3]?.setHeight(80);
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.bars.length - window), to: data.bars.length + 2 });
    return () => chart.remove();
  }, [data, indicators, window, overlays]);
  const last = data?.bars.at(-1);
  return <section className="chart-card" aria-label="NVDA 价格与指标">
    <div className="chart-top"><div><span className="eyebrow">NASDAQ / 美元</span><h2>NVDA <span>NVIDIA</span></h2></div><div className="last-price">{last ? Number(last.close).toFixed(2) : "—"}<span>{data?.data_mode === "REPLAY" ? "合成收盘价" : "日线收盘价"}</span></div></div>
    <div className="chart-controls"><span className="chip">日线</span><div aria-label="图表范围">{[30, 90, 500].map(n => <button key={n} className={window === n ? "selected" : ""} onClick={() => setWindow(n)}>{n} 日</button>)}</div><span className="muted indicator-legend">K 线 · 成交量 · RSI · MACD</span></div>
    <div className="chart-controls" aria-label="价格指标">{["SMA20", "BOLL"].map(name => <button key={name} aria-pressed={overlays.includes(name)} className={overlays.includes(name) ? "selected" : ""} onClick={() => setOverlays(old => old.includes(name) ? old.filter(n => n !== name) : [...old, name])}>{name}</button>)}</div>
    {error ? <div className="chart-empty" role="alert"><strong>数据暂不可用</strong><p>{error}</p><button onClick={() => setRetry(v => v + 1)}>重新加载</button></div> : <div className="chart-host" ref={container} role="img" aria-label="NVDA 日线、成交量、RSI14 与 MACD 图表">{!data && <span className="chart-loading" role="status">正在读取行情与指标…</span>}</div>}
    <div className="chart-source"><span className="badge">{data?.data_mode === "REPLAY" ? "REPLAY" : data ? "EOD" : "UNAVAILABLE"}</span><span>{data?.provider ?? "等待数据"}{data && ` · ${dateTime(data.as_of)} 美东 · 拆股可比价格与成交量`}</span>{commandId && <span className="tool-opened">由研究工具打开</span>}</div>
    <a className="chart-credit" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a>
  </section>;
}
