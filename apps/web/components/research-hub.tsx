"use client";
import Link from "next/link";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, message, statusName } from "../lib/client";
import { HermesMark } from "./research-welcome";

type Hub = {
  coverage: any[];
  forecasts: any[];
  lessons: any[];
  notifications: any[];
  jobs: any[];
  settings: Record<string, any>;
  capabilities: any[];
  summary: Record<string, any>;
  scan: any | null;
};
const tabs = ["研究池", "预测记录", "经验试验", "研究设置"] as const;
type Tab = (typeof tabs)[number];
const pct = (value: unknown) =>
  value === null ||
  value === undefined ||
  value === "" ||
  !Number.isFinite(Number(value))
    ? "—"
    : `${(Number(value) * 100).toFixed(1)}%`;
const time = (value: unknown) =>
  !value
    ? "尚无记录"
    : new Date(String(value)).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Shanghai",
      });
const category: Record<string, string> = {
  holding: "持仓",
  holdings: "持仓",
  watchlist: "自选",
  temporary: "临时观察",
  candidate: "市场发现",
};
const lessonStatus: Record<string, string> = {
  candidate: "候选经验",
  shadow: "影子试用",
  trial: "影子试用",
  adopted: "采用中",
  active: "采用中",
  paused: "已暂停",
  retired: "已停用",
  disabled: "已停用",
};
const sourceName: Record<string, string> = {
  market_news: "行情与新闻",
  sec: "公司披露",
  web: "网页与公司官网",
  x: "X 讨论",
  macro: "宏观数据",
};

export function ForecastView({
  forecast,
  onSelect,
}: {
  forecast: any;
  onSelect?: () => void;
}) {
  const p = forecast.payload ?? forecast;
  const evaluation = forecast.evaluation?.payload ?? forecast.evaluation;
  const mean = p.meanReturn ?? forecast.mean_return;
  return (
    <article className="forecast-ticket">
      <div className="forecast-ticket-heading">
        <div>
          <strong>{forecast.symbol ?? p.symbol}</strong>
          <span>{forecast.horizon ?? p.horizon} 个交易日</span>
        </div>
        <span
          className={`research-label ${Number(mean) >= 0 ? "positive" : "negative"}`}
        >
          {Number(mean) === 0 ? "横盘" : Number(mean) > 0 ? "看多" : "看空"}
        </span>
      </div>
      <div className="forecast-values">
        <div>
          <small>预期收益</small>
          <strong>{pct(mean)}</strong>
        </div>
        <div>
          <small>上涨概率</small>
          <strong>{pct(p.upProbability ?? forecast.up_probability)}</strong>
        </div>
        <div>
          <small>80% 收益区间</small>
          <strong>
            {pct(p.lowerReturn ?? forecast.lower_return)} ～{" "}
            {pct(p.upperReturn ?? forecast.upper_return)}
          </strong>
        </div>
      </div>
      <p className="forecast-thesis">{p.thesis ?? forecast.thesis}</p>
      <div className="forecast-ticket-footer">
        <span>
          {forecast.status === "evaluated" ||
          forecast.status === "settled" ||
          evaluation?.status === "evaluated" ||
          evaluation?.actualReturn !== undefined
            ? `已验证 · 实际 ${pct(evaluation?.actualReturn ?? evaluation?.actual_return)}`
            : forecast.status === "pending_data"
              ? "待补充结算行情"
              : "等待到期验证"}
        </span>
        <span>
          发布于{" "}
          {time(forecast.published_at ?? p.publishedAt ?? forecast.created_at)}
        </span>
        {onSelect && (
          <button type="button" onClick={onSelect}>
            依据与结果 ↗
          </button>
        )}
      </div>
    </article>
  );
}

function EvidenceList({ evidence }: { evidence: any[] }) {
  return (
    <ul className="research-evidence">
      {evidence.map((item, i) => {
        const p = item.payload ?? item;
        const url =
          typeof p.url === "string" && /^https?:\/\//.test(p.url)
            ? p.url
            : null;
        return (
          <li key={item.id ?? i}>
            <span className="research-label">
              {sourceName[item.source] ?? item.source ?? "研究证据"}
            </span>
            {url ? (
              <a href={url} target="_blank" rel="noreferrer">
                {p.title ?? p.url}
              </a>
            ) : (
              <strong>{p.title ?? p.sourceKey ?? "已保存资料"}</strong>
            )}
            <p>
              {String(p.summary ?? p.text ?? p.content ?? "").slice(0, 600)}
            </p>
            <small>
              公开 {time(item.published_at ?? p.publishedAt)} · 获取{" "}
              {time(item.observed_at ?? p.observedAt)}
            </small>
          </li>
        );
      })}
    </ul>
  );
}

function EvaluationView({ evaluation }: { evaluation: any }) {
  const result = evaluation?.payload ?? evaluation;
  if (!result) return <p>到期并取得完整行情后，验证结果会显示在这里。</p>;
  const metrics = result.metrics ?? {};
  return (
    <div className="research-review">
      <h3>到期验证</h3>
      <div className="forecast-values">
        <div>
          <small>实际收益</small>
          <strong>{pct(result.actualReturn)}</strong>
        </div>
        <div>
          <small>相对 SPY 收益</small>
          <strong>{pct(result.excessReturn)}</strong>
        </div>
        <div>
          <small>收益预测误差</small>
          <strong>{pct(metrics.absoluteError)}</strong>
        </div>
        <div>
          <small>方向判断</small>
          <strong>
            {metrics.directionCorrect == null
              ? "—"
              : metrics.directionCorrect
                ? "正确"
                : "错误"}
          </strong>
        </div>
        <div>
          <small>80% 区间覆盖</small>
          <strong>
            {metrics.intervalCovered == null
              ? "—"
              : metrics.intervalCovered
                ? "落在区间内"
                : "超出区间"}
          </strong>
        </div>
        <div>
          <small>概率误差 · 越小越好</small>
          <strong>
            {metrics.brier == null ? "—" : Number(metrics.brier).toFixed(3)}
          </strong>
        </div>
      </div>
    </div>
  );
}

export function ResearchHub({ embedded = false }: { embedded?: boolean }) {
  const [tab, setTab] = useState<Tab>("研究池");
  const [data, setData] = useState<Hub | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [unauthorized, setUnauthorized] = useState(false);
  const [filter, setFilter] = useState("");
  const [horizon, setHorizon] = useState("all");
  const [stock, setStock] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [settings, setSettings] = useState<Record<string, any>>({});
  const generation = useRef(0);
  const detailGeneration = useRef(0);
  const mounted = useRef(true);
  const refresh = useCallback(async (initial = false) => {
    const run = ++generation.current;
    if (initial) setLoading(true);
    try {
      const next = await api<Hub>("research-hub");
      if (!mounted.current || run !== generation.current) return;
      setData(next);
      if (initial) setSettings(next.settings);
      setError("");
      setUnauthorized(false);
    } catch (err) {
      if (!mounted.current || run !== generation.current) return;
      setError(message(err));
      if (err instanceof Error && err.message === "unauthorized") {
        setUnauthorized(true);
        setData(null);
        setStock(null);
        setDetail(null);
      }
    } finally {
      if (mounted.current && run === generation.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh(true);
    const interval = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 30_000);
    return () => {
      mounted.current = false;
      generation.current++;
      detailGeneration.current++;
      clearInterval(interval);
    };
  }, [refresh]);
  async function updateCoverage(symbol: string, action: string) {
    setSaving(true);
    setNotice("");
    try {
      await api("coverage", "PUT", { symbol, action });
      if (!mounted.current) return;
      setNotice(`${symbol} 的跟踪设置已更新`);
      await refresh();
    } catch (err) {
      if (mounted.current) setError(message(err));
    } finally {
      if (mounted.current) setSaving(false);
    }
  }
  async function selectStock(symbol: string) {
    const run = ++detailGeneration.current;
    setDetailLoading(true);
    setDetail(null);
    setStock(null);
    try {
      const next = await api(`stocks/${encodeURIComponent(symbol)}`);
      if (mounted.current && run === detailGeneration.current) setStock(next);
    } catch (err) {
      if (mounted.current && run === detailGeneration.current)
        setError(message(err));
    } finally {
      if (mounted.current && run === detailGeneration.current)
        setDetailLoading(false);
    }
  }
  async function selectForecast(id: string) {
    const run = ++detailGeneration.current;
    setDetailLoading(true);
    setStock(null);
    setDetail(null);
    try {
      const next = await api(`forecasts/${id}`);
      if (mounted.current && run === detailGeneration.current) setDetail(next);
    } catch (err) {
      if (mounted.current && run === detailGeneration.current)
        setError(message(err));
    } finally {
      if (mounted.current && run === detailGeneration.current)
        setDetailLoading(false);
    }
  }
  const coverage = (data?.coverage ?? []).filter((row) =>
    row.symbol.includes(filter.toUpperCase().trim()),
  );
  const forecasts = (data?.forecasts ?? []).filter(
    (row) =>
      row.symbol.includes(filter.toUpperCase().trim()) &&
      (horizon === "all" || String(row.horizon) === horizon),
  );
  const scores = data?.summary.byHorizon ?? {};
  const closeDetail = () => {
    detailGeneration.current++;
    setStock(null);
    setDetail(null);
    setDetailLoading(false);
  };
  return (
    <div className={embedded ? "research-embedded" : "app research-app"}>
      {!embedded && <header className="research-topbar">
        <Link className="research-brand" href="/">
          <HermesMark /> Hermes <span>ALPHA LAB</span>
        </Link>
        <Link href="/">返回研究对话 ↗</Link>
      </header>}
      <main className="research-hub">
        <div className="research-heading">
          <div>
            <p className="research-kicker">持续研究 · 美股</p>
            <h1>判断留下记录，经验接受检验。</h1>
            <p>持仓优先，发现新机会。每份预测都保留当时的依据和后来的结果。</p>
          </div>
          <button
            type="button"
            disabled={loading}
            onClick={() => void refresh()}
          >
            刷新记录
          </button>
        </div>
        {error && (
          <div className="research-alert" role="alert">
            {error}{" "}
            {unauthorized ? (
              <Link href="/">返回登录</Link>
            ) : (
              <button type="button" onClick={() => void refresh(true)}>
                重试加载
              </button>
            )}
          </div>
        )}
        {notice && (
          <p className="research-notice" role="status">
            {notice}
          </p>
        )}
        {loading && <p role="status">正在读取研究记录…</p>}
        {data && (
          <>
            <div className="research-overview" aria-label="研究概况">
              <div>
                <small>持续覆盖</small>
                <strong>
                  {data.summary.covered ??
                    data.coverage.filter(
                      (r) => r.status !== "paused" && r.status !== "removed",
                    ).length}
                </strong>
                <span>持仓与自选优先</span>
              </div>
              <div>
                <small>等待验证</small>
                <strong>{data.summary.open ?? "—"}</strong>
                <span>5／20 个交易日</span>
              </div>
              <div>
                <small>已完成验证</small>
                <strong>{data.summary.evaluated ?? "—"}</strong>
                <span>仅事前发布记录</span>
              </div>
              <div>
                <small>待研究</small>
                <strong>
                  {data.summary.queued ??
                    data.jobs.filter((j) => j.status === "queued").length}
                </strong>
                <span>
                  {data.summary.deferred
                    ? `${data.summary.deferred} 项等待后续研究名额`
                    : "你的提问优先"}
                </span>
              </div>
            </div>
            <nav className="research-tabs" aria-label="持续研究栏目">
              {tabs.map((name) => (
                <button
                  key={name}
                  type="button"
                  aria-current={tab === name ? "page" : undefined}
                  onClick={() => {
                    setTab(name);
                    closeDetail();
                  }}
                >
                  {name}
                </button>
              ))}
            </nav>
            {(tab === "研究池" || tab === "预测记录") && (
              <div className="research-filters">
                <label>
                  筛选股票
                  <input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="例如 NVDA"
                  />
                </label>
                {tab === "预测记录" && (
                  <label>
                    预测期限
                    <select
                      value={horizon}
                      onChange={(e) => setHorizon(e.target.value)}
                    >
                      <option value="all">全部期限</option>
                      <option value="5">5 个交易日</option>
                      <option value="20">20 个交易日</option>
                    </select>
                  </label>
                )}
              </div>
            )}
            {tab === "研究池" && (
              <>
                <div className="research-section-heading">
                  <h2>我的研究池</h2>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      const form = e.currentTarget;
                      const symbol = String(
                        new FormData(form).get("symbol") ?? "",
                      )
                        .trim()
                        .toUpperCase();
                      void updateCoverage(symbol, "watch").then(() =>
                        form.reset(),
                      );
                    }}
                  >
                    <input
                      name="symbol"
                      required
                      pattern="[A-Za-z][A-Za-z0-9.\-]{0,14}"
                      aria-label="添加跟踪股票代码"
                      placeholder="股票代码"
                    />
                    <button disabled={saving}>加入自选</button>
                  </form>
                </div>
                {coverage.length ? (
                  <div
                    className="research-table-scroll"
                    role="region"
                    aria-label="研究股票"
                    tabIndex={0}
                  >
                    <table className="research-table">
                      <thead>
                        <tr>
                          <th>股票</th>
                          <th>来源</th>
                          <th>跟踪状态</th>
                          <th>最近研究</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {coverage.map((row) => (
                          <tr key={row.symbol}>
                            <th>
                              <button
                                type="button"
                                className="research-symbol"
                                onClick={() => void selectStock(row.symbol)}
                              >
                                {row.symbol} ↗
                              </button>
                            </th>
                            <td>
                              {category[
                                row.kind ?? row.source ?? row.coverage_type
                              ] ??
                                row.kind ??
                                "跟踪"}
                            </td>
                            <td>
                              {row.status === "paused"
                                ? "已暂停"
                                : row.status === "removed"
                                  ? "已移除"
                                  : row.expires_at &&
                                      new Date(row.expires_at) < new Date()
                                    ? "观察到期"
                                    : "持续跟踪"}
                            </td>
                            <td>
                              {time(
                                row.last_researched_at ?? row.last_research_at,
                              )}
                            </td>
                            <td className="research-actions">
                              <button
                                disabled={saving}
                                onClick={() =>
                                  void updateCoverage(
                                    row.symbol,
                                    row.status === "paused"
                                      ? "resume"
                                      : "pause",
                                  )
                                }
                              >
                                {row.status === "paused" ? "恢复" : "暂停"}
                              </button>
                              {!["holding", "holdings"].includes(
                                row.kind ?? row.source ?? row.coverage_type,
                              ) && (
                                <button
                                  disabled={saving}
                                  onClick={() =>
                                    void updateCoverage(row.symbol, "remove")
                                  }
                                >
                                  移除
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="research-empty">
                    <h3>{filter ? "没有匹配的股票" : "从你的股票开始积累"}</h3>
                    <p>
                      {filter
                        ? "换一个股票代码，或清空筛选。"
                        : "添加自选股，或回到工作台保存持仓。首次研究会建立公司档案和预测记录。"}
                    </p>
                  </div>
                )}
                <div className="research-columns">
                  <section>
                    <h2>市场发现</h2>
                    {data.scan ? (
                      <>
                        <p>最近扫描 {time(data.scan.created_at)}</p>
                        <p>
                          {data.scan.coverage?.observed ?? "—"} 只取得有效数据 ·{" "}
                          {data.scan.coverage?.missing?.length ?? "—"}{" "}
                          只待补数据
                        </p>
                        <p className="research-muted">
                          每天优先深研 8 只排名候选，并探索 2
                          只覆盖较少的股票。实际名单与缺数单独保存。
                        </p>
                      </>
                    ) : (
                      <p className="research-muted">
                        首次扫描完成后，显示实际覆盖范围和候选。没有行情连接时不会生成示例结果。
                      </p>
                    )}
                  </section>
                  <section>
                    <h2>最近动态</h2>
                    {data.notifications.length ? (
                      <ul className="research-activity">
                        {data.notifications.slice(0, 6).map((n) => (
                          <li key={n.id}>
                            <strong>
                              {n.payload?.title ?? n.title ?? "研究更新"}
                            </strong>
                            <p>{n.payload?.text ?? n.text}</p>
                            <small>{time(n.created_at)}</small>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="research-muted">
                        预测变化、到期复盘和每日摘要会显示在这里。
                      </p>
                    )}
                  </section>
                </div>
              </>
            )}
            {tab === "预测记录" && (
              <>
                <div className="research-section-heading">
                  <div>
                    <h2>事前判断与后续结果</h2>
                    <p>
                      收益从发布后的第一个正常开盘起算。改判另存新版本，原预测照常验证。
                    </p>
                  </div>
                </div>
                <div className="research-score-strip">
                  {[5, 20]
                    .filter((h) => horizon === "all" || String(h) === horizon)
                    .map((h) => (
                      <span key={h}>
                        {h}日 · 方向正确率{" "}
                        <strong>{pct(scores[h]?.directionAccuracy)}</strong> ·
                        概率误差{" "}
                        <strong>
                          {scores[h]?.brier == null
                            ? "—"
                            : Number(scores[h].brier).toFixed(3)}
                        </strong>{" "}
                        · {scores[h]?.samples ?? 0} 个原始预测期
                      </span>
                    ))}
                  <span>
                    待补行情 <strong>{data.summary.pendingData ?? 0}</strong>
                  </span>
                </div>
                {forecasts.length ? (
                  <div className="forecast-list">
                    {forecasts.map((f) => (
                      <ForecastView
                        key={f.id}
                        forecast={f}
                        onSelect={() => void selectForecast(f.id)}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="research-empty">
                    <h3>等待第一份可验证的判断</h3>
                    <p>
                      在研究对话中询问一只股票未来一周或一个月的走势。记录发布后，系统会按到期日验证。
                    </p>
                    <Link href="/">开始研究 ↗</Link>
                  </div>
                )}
              </>
            )}
            {tab === "经验试验" && (
              <>
                <div className="research-section-heading">
                  <div>
                    <h2>经验也要经得起检验</h2>
                    <p>
                      同一份证据，对比原方法与加入新经验后的判断。历史归纳案例不计入后续验证。
                    </p>
                  </div>
                </div>
                <ol className="lesson-stages">
                  <li>候选经验</li>
                  <li>影子试用</li>
                  <li>通过后采用</li>
                  <li>持续检查</li>
                </ol>
                {data.lessons.length ? (
                  <div className="lesson-list">
                    {data.lessons.map((l) => (
                      <article className="lesson-row" key={l.id}>
                        <div>
                          <span className="research-label">
                            {lessonStatus[l.status] ?? l.status}
                          </span>
                          <h3>
                            {l.title ??
                              l.payload?.title ??
                              l.rule ??
                              "经验假设"}
                          </h3>
                          <p>
                            {l.statement ??
                              l.rule ??
                              l.payload?.rule ??
                              l.description ??
                              l.thesis}
                          </p>
                          <p className="research-muted">
                            适用：{l.scope ?? l.payload?.scope ?? "见经验条件"}{" "}
                            · {l.horizon ?? l.payload?.horizon ?? "—"} 个交易日
                          </p>
                        </div>
                        <div>
                          <strong>
                            {l.completed_trials ??
                              l.last_batch?.sampleCount ??
                              0}
                            <small> / 30 组独立事件</small>
                          </strong>
                          <p>支持与反例均保留</p>
                          <details>
                            <summary>查看条件与验证记录</summary>
                            <p>适用条件：{l.applicability}</p>
                            <p>失效条件：{l.invalidation}</p>
                            <p>事件类型：{l.event_type}</p>
                            <p>
                              已安排试用 {l.trial_count ?? 0} 次。
                              {l.last_batch
                                ? `最近批次：${l.last_batch.passes ? "达到采用门槛" : "继续观察"}`
                                : "尚无完整验证批次。"}
                            </p>
                          </details>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : (
                  <div className="research-empty">
                    <h3>让第一批预测先接受检验</h3>
                    <p>
                      预测到期后会复盘成功与失败，将有依据的规律整理为候选经验，进入后续试用。
                    </p>
                  </div>
                )}
              </>
            )}
            {tab === "研究设置" && (
              <div className="research-settings">
                <section>
                  <h2>研究节奏</h2>
                  <form
                    onSubmit={async (e) => {
                      e.preventDefault();
                      setSaving(true);
                      setNotice("");
                      try {
                        const next = await api(
                          "agent-settings",
                          "PUT",
                          settings,
                        );
                        if (!mounted.current) return;
                        setSettings(next);
                        setNotice("研究设置已保存");
                        await refresh();
                      } catch (err) {
                        if (mounted.current) setError(message(err));
                      } finally {
                        if (mounted.current) setSaving(false);
                      }
                    }}
                  >
                    <label className="research-check">
                      <input
                        type="checkbox"
                        checked={!!settings.enabled}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            enabled: e.target.checked,
                          })
                        }
                      />
                      启用后台持续研究
                    </label>
                    <label>
                      每日后台深研股票上限
                      <input
                        type="number"
                        min="1"
                        max="100"
                        value={settings.dailyResearchLimit ?? 30}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            dailyResearchLimit: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    <label>
                      其中新候选上限
                      <input
                        type="number"
                        min="0"
                        max="30"
                        value={settings.dailyCandidateLimit ?? 10}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            dailyCandidateLimit: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    <label className="research-check">
                      <input
                        type="checkbox"
                        checked={!!settings.feishuNotifications}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            feishuNotifications: e.target.checked,
                          })
                        }
                      />
                      飞书私聊接收摘要与重要变化
                    </label>
                    <p className="research-muted">
                      日报在有新交易日结果时于北京时间 08:00
                      发送。主动提问和预测结算不占后台深研名额。
                    </p>
                    <button className="primary" disabled={saving}>
                      {saving ? "正在保存…" : "保存研究设置"}
                    </button>
                  </form>
                </section>
                <section>
                  <h2>信息渠道</h2>
                  <ul className="source-list">
                    {data.capabilities.map((c) => (
                      <li key={c.source ?? c.name}>
                        <div>
                          <strong>
                            {sourceName[c.source ?? c.name] ??
                              c.label ??
                              c.name ??
                              c.source}
                          </strong>
                          <p>
                            {c.reason ??
                              c.detail ??
                              (c.available
                                ? "已配置；实际获取情况记录在每轮研究中。"
                                : "尚未配置此渠道，研究时会明确标记缺失。")}
                          </p>
                        </div>
                        <span
                          className={`research-label ${c.available ? "positive" : ""}`}
                        >
                          {c.available ? "已配置" : "待连接"}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="research-muted">
                    连接状态不代表资料完整。每次回答保留实际获取的来源和时间。
                  </p>
                </section>
              </div>
            )}
            {(detailLoading || stock || detail) && (
              <section className="research-detail" aria-label="研究详情">
                <div className="research-section-heading">
                  <h2>
                    {stock
                      ? `${stock.symbol ?? stock.coverage?.symbol ?? "股票"} · 研究档案`
                      : "预测依据与结果"}
                  </h2>
                  <button type="button" onClick={closeDetail}>
                    关闭详情
                  </button>
                </div>
                {detailLoading && <p role="status">正在加载详情…</p>}
                {stock && (
                  <>
                    <p>
                      本股历史预测{" "}
                      {stock.forecastCount ?? stock.forecasts?.length ?? 0} 条 ·
                      相关经验 {stock.lessons?.length ?? 0} 条
                    </p>
                    {stock.dossier && (
                      <p className="forecast-thesis">
                        {stock.dossier.summary ??
                          stock.dossier.payload?.summary ??
                          ""}
                      </p>
                    )}
                    <EvidenceList evidence={stock.evidence ?? []} />
                    {(stock.forecasts ?? []).map((f: any) => (
                      <ForecastView
                        key={f.id}
                        forecast={f}
                        onSelect={() => void selectForecast(f.id)}
                      />
                    ))}
                  </>
                )}
                {detail && (
                  <>
                    <ForecastView forecast={detail.forecast ?? detail} />
                    <p>
                      起算日：
                      {detail.forecast?.start_session ??
                        detail.start_session ??
                        "见预测记录"}{" "}
                      · 到期日：
                      {detail.forecast?.target_session ??
                        detail.target_session ??
                        "见预测记录"}
                    </p>
                    <EvidenceList evidence={detail.evidence ?? []} />
                    {(detail.reviews ?? []).map((r: any) => (
                      <div className="research-review" key={r.id}>
                        <h3>到期复盘</h3>
                        <p>{r.text ?? r.payload?.text}</p>
                      </div>
                    ))}
                    <EvaluationView
                      evaluation={
                        detail.evaluation ?? detail.forecast?.evaluation
                      }
                    />
                  </>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
