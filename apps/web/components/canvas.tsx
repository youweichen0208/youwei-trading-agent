"use client";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  createChart,
  CandlestickSeries,
  AreaSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
  type Time,
} from "lightweight-charts";
import { useCanvas, type Artifact } from "../lib/canvas";
import { api, message } from "../lib/client";
import { ForecastView } from "./research-hub";
const number = (value: unknown) =>
  typeof value === "string" &&
  value.trim() !== "" &&
  Number.isFinite(Number(value))
    ? Number(value)
    : null;
const percent = (value: unknown) => {
  const parsed = number(value);
  return parsed === null ? "—" : `${(parsed * 100).toPrecision(4)}%`;
};
function ChartData({ rows, kind }: { rows: any[]; kind: string }) {
  const [open, setOpen] = useState(false);
  const stock = kind === "stock_chart";
  const columns = stock
    ? [
        ["open", "开盘"],
        ["high", "最高"],
        ["low", "最低"],
        ["close", "收盘"],
        ["volume", "成交量（股）"],
      ]
    : [
        ["ma20", "MA20"],
        ["rsi14", "RSI14"],
      ];
  return (
    <details
      className="chart-data"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        查看{stock ? "行情" : "指标"}数据表 · {rows.length} 个数据日
      </summary>
      {open && (
        <div
          className="chart-data-scroll"
          role="region"
          aria-label={stock ? "行情数据表" : "指标数据表"}
          tabIndex={0}
        >
          <table className="data-table">
            <caption>
              {stock ? "当前显示区间的历史行情" : "历史指标"}
              ，缺失值显示为“缺失”
            </caption>
            <thead>
              <tr>
                <th scope="col">交易日</th>
                {columns.map(([key, label]) => (
                  <th scope="col" key={key}>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.session}>
                  <th scope="row">{row.session}</th>
                  {columns.map(([key]) => (
                    <td key={key}>
                      {number(row[key!]) === null ? "缺失" : row[key!]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
function SeriesChart({
  rows,
  kind,
  props,
  mode = "candles",
}: {
  rows: any[];
  kind: string;
  props?: Artifact["props"];
  mode?: "line" | "candles";
}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const chart = createChart(container.current, {
      autoSize: true,
      height: kind === "stock_chart" ? 380 : 260,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#6c665c",
        attributionLogo: true,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: "#ece9e2" },
      },
      rightPriceScale: { borderColor: "#e7e3db" },
      timeScale: { borderColor: "#e7e3db" },
    });
    if (kind === "stock_chart") {
      if (mode === "line") {
        const line = chart.addSeries(AreaSeries, {
          lineColor: "#2e5bff",
          topColor: "#2e5bff26",
          bottomColor: "#2e5bff00",
          lineWidth: 2,
          priceLineVisible: false,
        });
        line.setData(
          rows.map((r) => {
            const value = number(r.close);
            return value === null
              ? { time: r.session as Time }
              : { time: r.session as Time, value };
          }),
        );
      } else {
        const bars = chart.addSeries(CandlestickSeries, {
          priceLineColor: "#6c665c",
          upColor: props?.color ?? "#009c9b",
          downColor: "#d95868",
          borderVisible: false,
          wickUpColor: "#009c9b",
          wickDownColor: "#d95868",
        });
        bars.setData(
          rows
            .map((r) => ({
              time: r.session as Time,
              open: number(r.open),
              high: number(r.high),
              low: number(r.low),
              close: number(r.close),
            }))
            .filter(
              (
                r,
              ): r is {
                time: Time;
                open: number;
                high: number;
                low: number;
                close: number;
              } =>
                r.open !== null &&
                r.high !== null &&
                r.low !== null &&
                r.close !== null,
            ),
        );
      }
      if (props?.showVolume) {
        const volume = chart.addSeries(
          HistogramSeries,
          {
            priceFormat: { type: "volume" },
            color: props.color ?? "#bedad7",
            priceLineColor: "#6c665c",
          },
          1,
        );
        volume.setData(
          rows
            .map((r) => ({ time: r.session as Time, value: number(r.volume) }))
            .filter(
              (r): r is { time: Time; value: number } => r.value !== null,
            ),
        );
      }
    } else {
      for (const [key, color] of [
        ["ma20", "#007b7a"],
        ["rsi14", "#966516"],
      ]) {
        const line = chart.addSeries(
          LineSeries,
          {
            color: props?.color ?? color,
            priceLineColor: color,
            title: key,
            lineWidth: 2,
          },
          key === "rsi14" ? 1 : 0,
        );
        line.setData(
          rows.map((r) => {
            const value = number(r[key!]);
            return value === null
              ? { time: r.session as Time }
              : { time: r.session as Time, value };
          }),
        );
      }
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rows, kind, props, mode]);
  return (
    <div
      ref={container}
      className="chart"
      role="img"
      aria-label={
        kind === "stock_chart"
          ? mode === "line"
            ? "历史收盘价走势"
            : "历史价格 K 线"
          : "MA20 和 RSI14 指标"
      }
    />
  );
}
function StockResult({
  rows,
  data,
  artifact,
}: {
  rows: any[];
  data: any;
  artifact: Artifact;
}) {
  const [range, setRange] = useState("ALL");
  const [mode, setMode] = useState<"line" | "candles">("line");
  const visible = useMemo(() => {
    if (range === "ALL" || !rows.length) return rows;
    const end = new Date(`${rows.at(-1).session}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() - (range === "1M" ? 30 : 90));
    const start = end.toISOString().slice(0, 10);
    return rows.filter((r) => r.session >= start);
  }, [rows, range]);
  const firstVisible = number(visible[0]?.close);
  const lastVisible = number(visible.at(-1)?.close);
  const rangeChange =
    firstVisible !== null && firstVisible > 0 && lastVisible !== null
      ? (lastVisible / firstVisible - 1) * 100
      : null;
  const last = rows.at(-1);
  const indicatorRefs = useCanvas((s) => s.artifacts)
    .filter((a) => a.component === "indicator_panel" && a.resultRef)
    .map((a) => a.resultRef)
    .join("|");
  const [indicators, setIndicators] = useState<{
    datasetRef: string;
    refs: string;
    session: string;
    row: any;
  } | null>(null);
  useEffect(() => {
    let active = true;
    setIndicators(null);
    if (artifact.datasetRef && indicatorRefs) {
      const refs = indicatorRefs.split("|");
      void Promise.allSettled(refs.map((ref) => api(`results/${ref}`))).then(
        (results) => {
          for (const result of results) {
            if (
              result.status !== "fulfilled" ||
              !result.value.lineage?.datasetRefs?.includes(artifact.datasetRef)
            )
              continue;
            const payload = result.value.payload;
            const points = Array.isArray(payload) ? payload : payload?.rows;
            const point = Array.isArray(points)
              ? points.find((r) => r.session === last?.session)
              : undefined;
            if (active && point) {
              setIndicators({
                datasetRef: artifact.datasetRef!,
                refs: indicatorRefs,
                session: last.session,
                row: point,
              });
              break;
            }
          }
        },
      );
    }
    return () => {
      active = false;
    };
  }, [artifact.datasetRef, indicatorRefs, last?.session]);
  const indicator =
    indicators?.datasetRef === artifact.datasetRef &&
    indicators?.refs === indicatorRefs &&
    indicators?.session === last?.session
      ? indicators.row
      : null;
  const close = number(last?.close),
    previous = number(rows.at(-2)?.close);
  const change =
    close !== null && previous !== null && previous > 0
      ? (close / previous - 1) * 100
      : null;
  const price = (v: unknown) => {
    const parsed = number(v);
    return parsed === null
      ? "—"
      : parsed.toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        });
  };
  return (
    <>
      <div className="quote-heading">
        <div>
          <strong className="quote-symbol">
            {data.metadata?.symbol ?? "历史行情"}
          </strong>
          <p>{data.metadata?.feed?.toUpperCase() ?? "日线"} · 已保存行情</p>
        </div>
        <div className="quote-price">
          <strong>
            {close === null
              ? "—"
              : `${data.metadata?.currency === "USD" ? "$" : ""}${price(last.close)}`}
          </strong>
          <span
            className={
              change === null ? "" : change >= 0 ? "positive" : "negative"
            }
          >
            {change === null
              ? "暂无可比收盘价"
              : `${change >= 0 ? "+" : ""}${change.toFixed(2)}% 较前一数据日`}
          </span>
        </div>
      </div>
      <div className="quote-facts">
        <span>开盘 {price(last?.open)}</span>
        <span>最高 {price(last?.high)}</span>
        <span>最低 {price(last?.low)}</span>
        <span>
          成交量 {number(last?.volume)?.toLocaleString("en-US") ?? "—"} 股
        </span>
      </div>
      <div className="quote-context">
        <span>
          截至 {last?.session} ·{" "}
          {data.metadata?.adjustment === "split" ? "拆股调整" : "未调整"}
        </span>
        <span>非实时行情</span>
      </div>
      <SeriesChart
        rows={visible}
        kind="stock_chart"
        props={artifact.props}
        mode={mode}
      />
      <div className="chart-toolbar">
        <div role="group" aria-label="历史行情显示区间">
          {["1M", "3M", "ALL"].map((value) => (
            <button
              key={value}
              aria-pressed={range === value}
              onClick={() => setRange(value)}
            >
              {value}
            </button>
          ))}
        </div>
        <div role="group" aria-label="图表类型">
          <button
            aria-pressed={mode === "line"}
            onClick={() => setMode("line")}
          >
            走势
          </button>
          <button
            aria-pressed={mode === "candles"}
            onClick={() => setMode("candles")}
          >
            K 线
          </button>
        </div>
      </div>
      <div className="quote-metrics">
        <div>
          <small>收盘价</small>
          <strong>
            {close === null
              ? "—"
              : `${data.metadata?.currency === "USD" ? "$" : ""}${price(last.close)}`}
          </strong>
          <span
            className={change !== null && change >= 0 ? "positive" : "negative"}
          >
            {change === null
              ? "暂无可比数据"
              : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}
          </span>
        </div>
        <div>
          <small>MA20</small>
          <strong>{price(indicator?.ma20)}</strong>
          <span>
            {indicator ? "同一交易日 · 已保存指标" : "暂无匹配的指标结果"}
          </span>
        </div>
        <div>
          <small>RSI14</small>
          <strong>{price(indicator?.rsi14)}</strong>
          <span>
            {indicator ? "同一交易日 · 已保存指标" : "暂无匹配的指标结果"}
          </span>
        </div>
        <div>
          <small>区间涨跌</small>
          <strong
            className={
              rangeChange !== null && rangeChange >= 0 ? "positive" : "negative"
            }
          >
            {rangeChange === null
              ? "—"
              : `${rangeChange >= 0 ? "+" : ""}${rangeChange.toFixed(2)}%`}
          </strong>
          <span>{visible.length} 个数据日</span>
        </div>
      </div>
      <ChartData rows={visible} kind="stock_chart" />
      <p className="chart-range">
        显示 {visible[0]?.session} — {visible.at(-1)?.session} ·{" "}
        {visible.length} 个数据日
      </p>
    </>
  );
}
function ResultContent({ artifact, data }: { artifact: Artifact; data: any }) {
  const p = data.payload;
  if (artifact.component === "forecast_result")
    return (
      <>
        <ForecastView forecast={p} />
        <p className="caption">
          起算 {p.startSession} · 到期 {p.targetSession} ·
          模型判断，原预测会持续保留。
        </p>
        <a href="/lab">查看预测记录与后续验证 ↗</a>
      </>
    );
  if (artifact.component === "forecast_evaluation")
    return (
      <>
        <span className="tag">事前预测 · 到期验证</span>
        <div className="metric-grid">
          <div>
            <small>实际收益</small>
            <strong>{percent(p.actualReturn)}</strong>
          </div>
          <div>
            <small>相对 SPY</small>
            <strong>{percent(p.excessReturn)}</strong>
          </div>
        </div>
        <table className="data-table">
          <tbody>
            <tr>
              <th>方向</th>
              <td>{p.metrics?.directionCorrect ? "正确" : "错误"}</td>
            </tr>
            <tr>
              <th>概率误差（Brier）</th>
              <td>{p.metrics?.brier ?? "—"}</td>
            </tr>
            <tr>
              <th>收益幅度误差</th>
              <td>{percent(p.metrics?.absoluteError)}</td>
            </tr>
            <tr>
              <th>80%区间覆盖</th>
              <td>{p.metrics?.intervalCovered ? "覆盖" : "未覆盖"}</td>
            </tr>
          </tbody>
        </table>
      </>
    );
  if (artifact.component === "review")
    return (
      <>
        <span className="tag">
          到期复盘 ·{" "}
          {p.attribution === "supported" ? "归因有证据支持" : "归因待验证"}
        </span>
        <p className="forecast-thesis">{p.text}</p>
        {p.lesson && (
          <>
            <h3>{p.lesson.title}</h3>
            <p>{p.lesson.statement}</p>
            <p className="caption">适用条件：{p.lesson.applicability}</p>
            <p className="caption">反例与失效条件：{p.lesson.invalidation}</p>
          </>
        )}
      </>
    );
  if (
    artifact.component === "stock_chart" ||
    artifact.component === "indicator_panel"
  ) {
    const sourceRows = Array.isArray(p) ? p : p?.rows;
    const rows =
      Array.isArray(sourceRows) && data.metadata?.displayRange
        ? sourceRows.filter(
            (r) =>
              r.session >= data.metadata.displayRange.start &&
              r.session <= data.metadata.displayRange.end,
          )
        : sourceRows;
    if (
      artifact.component === "stock_chart" &&
      Array.isArray(rows) &&
      !rows.some((r) => r.close !== null)
    )
      return <p className="notice">这段区间没有有效行情，未生成价格图。</p>;
    if (
      artifact.component === "stock_chart" &&
      Array.isArray(rows) &&
      rows.length
    )
      return <StockResult rows={rows} data={data} artifact={artifact} />;
    return Array.isArray(rows) && rows.length ? (
      <>
        <SeriesChart
          rows={rows}
          kind={artifact.component}
          props={artifact.props}
        />
        <ChartData rows={rows} kind={artifact.component} />
      </>
    ) : (
      <p>该结果没有可绘制的数据。</p>
    );
  }
  if (artifact.component === "prediction_result")
    return (
      <>
        <span className="tag">探索性统计基线 · 回溯评估</span>
        {p.status === "unavailable" ? (
          <p>连续有效样本不足，暂不生成预测。</p>
        ) : (
          <>
            <div className="metric-grid">
              <div>
                <small>下一交易日收益预测</small>
                <strong>
                  {p.prediction == null
                    ? "—"
                    : `${(Number(p.prediction) * 100).toFixed(3)}%`}
                </strong>
              </div>
              <div>
                <small>预测目标日</small>
                <strong>{p.target}</strong>
              </div>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>评估指标</th>
                  <th>20 日均收益</th>
                  <th>零收益基线</th>
                </tr>
              </thead>
              <tbody>
                {["mae", "rmse"].map((k) => (
                  <tr key={k}>
                    <th>{k.toUpperCase()}</th>
                    <td>{percent(p.metrics?.[k])}</td>
                    <td>{percent(p.zeroBaseline?.[k])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="caption">
              输入截至 {p.origin}；历史效果不代表未来收益。
            </p>
          </>
        )}
      </>
    );
  if (artifact.component === "portfolio_summary")
    return (
      <>
        <span className="tag">模拟持仓 · {p.asOf}</span>
        <div className="metric-grid">
          <div>
            <small>总市值 / USD</small>
            <strong>{p.total ?? "数据不完整"}</strong>
          </div>
          <div>
            <small>集中度</small>
            <strong>
              {p.concentration == null
                ? "—"
                : Number(p.concentration).toFixed(3)}
            </strong>
          </div>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>股票</th>
              <th>市值</th>
              <th>权重</th>
            </tr>
          </thead>
          <tbody>
            {p.positions?.map((r: any) => (
              <tr key={r.symbol}>
                <th>{r.symbol}</th>
                <td>{r.value ?? "缺少价格"}</td>
                <td>
                  {r.weight == null
                    ? "—"
                    : `${(Number(r.weight) * 100).toFixed(1)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {p.missing?.length > 0 && (
          <p className="notice">
            部分持仓没有同一交易日的有效价格，总估值暂不可用。
          </p>
        )}
      </>
    );
  if (artifact.component === "research_summary")
    return (
      <p className="report-text">
        {typeof p.text === "string" ? p.text : "该摘要暂不可读取。"}
      </p>
    );
  return <p>不支持的结果类型。</p>;
}
function ArtifactView({ artifact, refreshKey = 0 }: { artifact: Artifact; refreshKey?: number }) {
  const [data, setData] = useState<any>(),
    [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setData(undefined);
    setError("");
    const ref = artifact.datasetRef ?? artifact.resultRef;
    if (!ref) {
      setLoading(false);
      setError(
        "这项研究结果缺少数据引用。请重新打开研究；若仍失败，请发起新研究。",
      );
      return;
    }
    api(`${artifact.datasetRef ? "datasets" : "results"}/${ref}`)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled)
          setError(
            e instanceof Error && e.message === "not_found"
              ? "这项研究结果不存在或暂时无权读取。请重新读取；若仍失败，请重新打开研究或发起新研究。"
              : `这项研究结果暂不可读取。${message(e)} 可重新读取；若仍失败，请重新打开研究。`,
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [artifact.datasetRef, artifact.resultRef, attempt, refreshKey]);
  return (
    <article className="artifact" data-component={artifact.component}>
      <h3>{artifact.title}</h3>
      {error ? (
        <p role="alert">{error}</p>
      ) : !data ? (
        <p role="status">读取研究结果…</p>
      ) : (
        <>
          <ResultContent artifact={artifact} data={data} />
          {data.metadata?.missingSessions?.length > 0 && (
            <p className="notice">
              存在 {data.metadata.missingSessions.length}{" "}
              个缺失交易日，价格未补齐。
            </p>
          )}
          <div className="source-line">
            {data.metadata?.symbol && <strong>{data.metadata.symbol}</strong>}
            <span>
              来源：
              {data.metadata?.source ?? data.lineage?.source ?? "计算结果"}
            </span>
            <span>{data.metadata?.asOf ?? data.created_at}</span>
            {data.metadata && (
              <span>
                {data.metadata.currency} · {data.metadata.exchangeTimezone} ·{" "}
                {data.metadata.adjustment === "split" ? "拆股调整" : "未调整"} ·{" "}
                {data.metadata.feed}
              </span>
            )}
            {data.lineage?.inputs?.map((input: any) => (
              <span key={input.datasetRef}>
                {input.metadata?.symbol} · {input.metadata?.source}/
                {input.metadata?.feed} · {input.metadata?.adjustment} ·{" "}
                {input.metadata?.asOf}
              </span>
            ))}
            {data.lineage?.codeVersion && (
              <span>版本 {data.lineage.codeVersion}</span>
            )}
          </div>
        </>
      )}
      {!data && (error || attempt > 0) && (
        <button
          type="button"
          aria-label={`重新读取结果：${artifact.title}`}
          disabled={loading}
          onClick={() => setAttempt((value) => value + 1)}
        >
          重新读取结果
        </button>
      )}
    </article>
  );
}
export function Canvas({
  onClose,
  fullscreen,
  onFullscreen,
}: {
  onClose?: () => void;
  fullscreen?: boolean;
  onFullscreen?: () => void;
}) {
  const artifacts = useCanvas((s) => s.artifacts);
  const contextKey = useCanvas(s => `${s.workspaceId}:${s.historyTaskId}`);
  const [selection, setSelection] = useState<{context:string; id:string}|null>(null);
  const activeId = selection?.context === contextKey && artifacts.some(a => a.artifactId === selection.id) ? selection.id : artifacts[0]?.artifactId;
  const [zoom, setZoom] = useState(1);
  const [panMode, setPanMode] = useState<"select" | "hand">("select");
  const stage = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const setZoomClamped = (value: number) =>
    setZoom(Math.min(2, Math.max(0.5, Number(value.toFixed(2)))));
  function startPan(event: ReactPointerEvent<HTMLDivElement>) {
    if (panMode !== "hand" || !stage.current) return;
    if (
      (event.target as HTMLElement).closest(
        "button, a, input, select, textarea, summary",
      )
    )
      return;
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      left: stage.current.scrollLeft,
      top: stage.current.scrollTop,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function movePan(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drag.current || !stage.current) return;
    stage.current.scrollLeft = drag.current.left - (event.clientX - drag.current.x);
    stage.current.scrollTop = drag.current.top - (event.clientY - drag.current.y);
  }
  function endPan() {
    drag.current = null;
  }
  useEffect(() => {
    setZoom(1);
    setPanMode("select");
  }, [contextKey, activeId]);
  useEffect(() => {
    const node = stage.current;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      if (!useCanvas.getState().artifacts.length) return;
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((value) => {
        const next = Number((value - event.deltaY * 0.0015).toFixed(2));
        return Math.min(2, Math.max(0.5, next));
      });
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, []);

  const [refreshKey,setRefreshKey] = useState(0);
  const [downloading,setDownloading] = useState(false);
  const [toolbarError,setToolbarError] = useState("");
  const currentArtifact = artifacts.find(a => a.artifactId === activeId);
  async function download() {
    if (!currentArtifact) return;
    setDownloading(true);setToolbarError("");
    try {
      const ref = currentArtifact.datasetRef ?? currentArtifact.resultRef;
      if (!ref) throw new Error("产物缺少数据引用");
      const data = await api(`${currentArtifact.datasetRef ? "datasets" : "results"}/${ref}`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
      const link = document.createElement("a");link.href=url;link.download=`research-${currentArtifact.artifactId}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(e) {setToolbarError(message(e));} finally {setDownloading(false);}
  }
  const results = useRef<HTMLDivElement>(null);
  const resultLabels: Record<string, string> = {
    stock_chart: "行情",
    indicator_panel: "指标",
    prediction_result: "预测",
    portfolio_summary: "组合",
    research_summary: "摘要",
  };
  function jump(artifactId: string) {
    const container = results.current;
    const item = Array.from(container?.children ?? []).find(
      (node) => (node as HTMLElement).dataset.artifactId === artifactId,
    ) as HTMLElement | undefined;
    item?.scrollIntoView({
      block: "start",
      inline: "nearest",
      behavior: "auto",
    });
  }
  const historyTaskId = useCanvas((s) => s.historyTaskId),
    workspaceId = useCanvas((s) => s.workspaceId),
    load = useCanvas((s) => s.load);
  const [historyError, setHistoryError] = useState("");
  const [savingLayout, setSavingLayout] = useState(false);
  async function move(index: number, offset: number) {
    if (savingLayout || historyTaskId) return;
    const state = useCanvas.getState(),
      order = state.artifacts.map((a) => a.artifactId);
    const target = index + offset;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target]!, order[index]!];
    setSavingLayout(true);
    try {
      load(
        await api(`workspaces/${state.workspaceId}`, "PUT", {
          expectedVersion: state.version,
          order,
        }),
      );
      setHistoryError("");
    } catch (e) {
      setHistoryError(message(e));
      try {
        load(await api(`workspaces/${state.workspaceId}`));
      } catch {
        /* The original error remains visible. */
      }
    } finally {
      setSavingLayout(false);
    }
  }
  return (
    <section className="canvas-panel" aria-label="研究画布">
      <div className="canvas-header">
        <span className="canvas-header-icon" aria-hidden="true">
          <svg
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="4" width="14" height="12" rx="2" />
            <path d="M8 4v12M11 8h3M11 11h3" />
          </svg>
        </span>
        <h2>Canvas</h2>
        <span className="canvas-header-count">{artifacts.length} 个内容</span>
        <div className="canvas-header-actions">
          {onFullscreen && (
            <button
              type="button"
              onClick={onFullscreen}
              aria-label={fullscreen ? "退出全屏" : "全屏"}
            >
              <svg
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                {fullscreen ? (
                  <path d="M7 3v4H3M13 3v4h4M7 17v-4H3M13 17v-4h4" />
                ) : (
                  <path d="M3 7V3h4M17 7V3h-4M3 13v4h4M17 13v4h-4" />
                )}
              </svg>
              {fullscreen ? "退出全屏" : "全屏"}
            </button>
          )}
          {onClose && (
            <button
              type="button"
              className="canvas-close"
              aria-label="收起画布"
              onClick={onClose}
            >
              <svg
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="m5 5 10 10M15 5 5 15" />
              </svg>
            </button>
          )}
        </div>
      </div>
      {artifacts.length > 0 && (
        <div className="canvas-toolbar">
          <select
            className="canvas-select"
            aria-label="选择研究产物"
            value={activeId ?? ""}
            onChange={(e) => setSelection({ context: contextKey, id: e.target.value })}
          >
            {artifacts.map((a) => (
              <option key={a.artifactId} value={a.artifactId}>
                {a.title}
              </option>
            ))}
          </select>
          {historyTaskId && <span className="canvas-toolbar-state">历史快照</span>}
          <div className="canvas-window-actions">
            <div className="preview-nav">
              <button aria-label="上一个产物" disabled={!activeId || artifacts.findIndex(a=>a.artifactId===activeId)===0} onClick={()=>{const i=artifacts.findIndex(a=>a.artifactId===activeId);if(i>0)setSelection({context:contextKey,id:artifacts[i-1]!.artifactId});}}>
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4 6 10l6 6" /></svg>
              </button>
              <button aria-label="下一个产物" disabled={!activeId || artifacts.findIndex(a=>a.artifactId===activeId)>=artifacts.length-1} onClick={()=>{const i=artifacts.findIndex(a=>a.artifactId===activeId);if(i>=0 && i<artifacts.length-1)setSelection({context:contextKey,id:artifacts[i+1]!.artifactId});}}>
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m8 4 6 6-6 6" /></svg>
              </button>
              <button aria-label="刷新当前产物" disabled={!currentArtifact} onClick={()=>setRefreshKey(v=>v+1)}>
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M16 10a6 6 0 1 1-1.8-4.3M16 4v4h-4" /></svg>
              </button>
            </div>
            <button className="canvas-download" disabled={!currentArtifact || downloading} onClick={()=>void download()}>
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 3v9m0 0 3.5-3.5M10 12 6.5 8.5M4 15.5h12" /></svg>
              <span className="canvas-download-label">{downloading ? "下载中…" : "下载产物"}</span>
            </button>
          </div>
        </div>
      )}
      {toolbarError && <p role="alert" className="notice">{toolbarError}</p>}
      {historyTaskId && (
        <div className="notice">
          正在查看历史产物{" "}
          <button
            onClick={async () => {
              try {
                load(await api(`workspaces/${workspaceId}`));
                setHistoryError("");
              } catch (e) {
                setHistoryError(message(e));
              }
            }}
          >
            返回当前画布
          </button>
        </div>
      )}
      {historyError && <p role="alert">{historyError}</p>}
      <div
        ref={stage}
        className="canvas-stage"
        data-pan={panMode}
        onPointerDown={startPan}
        onPointerMove={movePan}
        onPointerUp={endPan}
        onPointerCancel={endPan}
      >
        <div
          className="canvas-zoom-layer"
          style={artifacts.length ? { zoom } : undefined}
        >
          <div
            ref={results}
            className={artifacts.length ? "artifacts" : "canvas-empty-wrap"}
          >
        {artifacts.length ? (
          artifacts.map((a, index) => (
            <div key={a.artifactId} data-artifact-id={a.artifactId} hidden={a.artifactId !== activeId}>
              {!historyTaskId && (
                <div className="artifact-actions">
                  <button
                    aria-label={`上移 ${a.title}`}
                    disabled={savingLayout || index === 0}
                    onClick={() => move(index, -1)}
                  >
                    ↑
                  </button>
                  <button
                    aria-label={`下移 ${a.title}`}
                    disabled={savingLayout || index === artifacts.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    ↓
                  </button>
                </div>
              )}
              <ArtifactView artifact={a} refreshKey={a.artifactId === activeId ? refreshKey : 0} />
            </div>
          ))
        ) : (
          <div className="canvas-idle">
            <div className="canvas-empty">
              <span className="empty-art" aria-hidden="true">
                <svg
                  className="empty-chart-icon"
                  viewBox="0 0 48 48"
                >
                  <path
                    d="M8 8v32h32M14 30l8-8 7 5 12-14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <circle cx="41" cy="13" r="2.5" fill="currentColor" />
                </svg>
              </span>
              <span className="empty-eyebrow">RESEARCH CANVAS</span>
              <h3>内容将在这里生成</h3>
              <p>
                继续左侧对话，表格、图表、图像等内容会自动呈现在画布中。
              </p>
              <div className="empty-types" aria-hidden="true">
                <span>表格</span>
                <span>图表</span>
                <span>图像</span>
                <span>文本</span>
              </div>
            </div>
          </div>
        )}
          </div>
        </div>
      </div>
      <div className="canvas-controls">
        <div className="canvas-pan-toggle" role="group" aria-label="画布操作">
          <button
            type="button"
            aria-pressed={panMode === "select"}
            aria-label="选择"
            onClick={() => setPanMode("select")}
          >
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M5 3.5 14.5 10 10 11l-2 4.5Z" />
            </svg>
          </button>
          <button
            type="button"
            aria-pressed={panMode === "hand"}
            aria-label="拖动平移"
            onClick={() => setPanMode("hand")}
          >
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M7 9V5.5a1.3 1.3 0 0 1 2.6 0V9m0-.5V4.3a1.3 1.3 0 0 1 2.6 0v4.4m0-.2V5.8a1.3 1.3 0 0 1 2.6 0v5.4c0 3-1.8 5.3-4.9 5.3-2.2 0-3.2-.9-4.3-2.5L3.8 11a1.3 1.3 0 0 1 2-1.5L7 11" />
            </svg>
          </button>
        </div>
        <span className="canvas-controls-hint">
          拖动平移 · Ctrl + 滚轮缩放
        </span>
        <div className="canvas-zoom">
          <button
            type="button"
            aria-label="缩小"
            onClick={() => setZoomClamped(zoom - 0.1)}
          >
            −
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button
            type="button"
            aria-label="放大"
            onClick={() => setZoomClamped(zoom + 0.1)}
          >
            +
          </button>
          <button
            type="button"
            aria-label="适应窗口"
            onClick={() => setZoom(1)}
          >
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M7 3H3v4M13 3h4v4M7 17H3v-4M13 17h4v-4" />
            </svg>
          </button>
        </div>
      </div>
      <footer className="canvas-foot">
        <span>数据来源与计算版本随结果保存</span>
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          Charts by TradingView
        </a>
      </footer>
    </section>
  );
}
