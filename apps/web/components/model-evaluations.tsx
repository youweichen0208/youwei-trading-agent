"use client";
import Link from "next/link";
import { useReportSelection } from "../lib/report-selection";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { experimentalEvaluationPageSchema, experimentalPriceReportSchema,
  experimentalEvaluationIdSchema, experimentalModelIdSchema,
  type ExperimentalEvaluationPage, type ExperimentalPriceReport } from "@yta/contracts";
import { api, dateTime, HttpError, type Me } from "../lib/api";

export function ModelEvaluations({me, modelId, reportId}: {me: Me; modelId?: string; reportId?: string}) {
  const router = useRouter();
  const selection = useReportSelection(me);
  const [page, setPage] = useState<ExperimentalEvaluationPage>();
  const [report, setReport] = useState<ExperimentalPriceReport>();
  const [trail, setTrail] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const cursor = trail.at(-1);
  useEffect(() => {
    if (!me.experimental_models_enabled) return;
    const abort = new AbortController();
    setPage(undefined); setReport(undefined); setError(""); setPending(true);
    async function load() {
      if (reportId) {
        const id = experimentalEvaluationIdSchema.parse(reportId);
        const result = experimentalPriceReportSchema.parse(await api<unknown>("/experimental-evaluations/" + id, {signal: abort.signal}));
        if (result.cohort.data_mode !== me.data_mode || id !== "model-evaluation-experimental-" + result.content_hash) throw new Error("Invalid report");
        if (!abort.signal.aborted) setReport(result);
      } else {
        const id = experimentalModelIdSchema.parse(modelId);
        const query = new URLSearchParams({limit: "10"});
        if (cursor) query.set("cursor", cursor);
        const result = experimentalEvaluationPageSchema.parse(await api<unknown>(`/experimental-models/${id}/evaluations?${query}`, {signal: abort.signal}));
        if (result.items.length > 10 || result.items.some(row => row.model_id !== id || row.data_mode !== me.data_mode || row.id === cursor)
          || (cursor && result.next_cursor === cursor)) throw new Error("Invalid report list");
        if (!abort.signal.aborted) setPage(result);
      }
    }
    void load().catch(e => {
      if (abort.signal.aborted) return;
      if (e instanceof HttpError && e.status === 401) {router.replace("/login"); return;}
      setError(e instanceof HttpError && e.status === 404 ? "当前环境中找不到这份评测或模型。请返回模型目录。"
        : "实验评测暂时无法读取，请重新加载。不会使用其他报告替代。");
    }).finally(() => {if (!abort.signal.aborted) setPending(false);});
    return () => abort.abort();
  }, [modelId, reportId, cursor, reload, me.data_mode, me.experimental_models_enabled, router]);
  const back = report ? `/models/${report.model_id}/evaluations` : "/models";
  return <main className="records-page experimental-reports-page">
    <div className="records-heading"><div><p className="eyebrow">独立窗口 / 固定样本</p><h1>{reportId ? "实验评测详情" : "模型评测记录"}</h1><p className="muted">比较同一组历史样本的价格预测误差，保留报告使用的证据版本。</p></div><Link href={back}>{report ? "← 该模型的评测" : "← 模型目录"}</Link></div>
    {!me.experimental_models_enabled ? <section className="empty-state"><h2>实验评测尚未启用</h2><Link href="/predictions">查看预测记录 →</Link></section> : <>
      <aside className="model-scope"><span className="badge">未经正式验收</span><div><h2>历史实验，不代表前瞻成绩或实际收益</h2><p>评测窗口独立于校准样本，但样本可能相关，也可能被多次用于模型选择。{me.data_mode === "REPLAY" && "当前为合成 REPLAY 数据。"}本报告不代表模型已获批准或成为冠军。</p></div></aside>
      <div className="model-pagination"><Link href="/models/compare">比较已选报告（{selection.ids.length}） →</Link><span className="small muted">最多选择 10 份，可跨模型选择。</span></div>
      {selection.error && <div role="alert" className="alert">{selection.error}</div>}
      {error && <div className="alert" role="alert">{error}<button onClick={() => setReload(n => n + 1)}>重新加载</button></div>}
      {pending && <p role="status">正在核对实验评测…</p>}
      {page && <section className="evidence-card"><h2>已登记报告</h2><p className="small muted hash">模型：{modelId}</p>{page.items.length ? <div className="table-scroll" role="region" aria-label="实验评测列表" tabIndex={0}><table><thead><tr><th>登记时间（美东）</th><th>信息截点（美东）</th><th>跨度 / 样本</th><th>报告</th></tr></thead><tbody>{page.items.map(row => <tr key={row.id}><td>{dateTime(row.registered_at)}</td><td>{dateTime(row.cutoff)}</td><td>{row.horizon_sessions} 交易日 / {row.sample_count}</td><td><Link href={"/model-evaluations/" + row.id}>查看评测 {row.content_hash.slice(0, 8)} →</Link><button className="report-select" aria-pressed={selection.ids.includes(row.id)} disabled={!selection.ready || (!selection.ids.includes(row.id) && selection.ids.length >= 10)} onClick={() => selection.toggle(row.id)}>{selection.ids.includes(row.id) ? "移出比较" : "加入比较"}</button></td></tr>)}</tbody></table></div> : <p className="muted">这个模型还没有已登记的独立窗口评测。目录中的保留窗口误差不替代评测报告。</p>}</section>}
      {!reportId && <div className="model-pagination"><button disabled={pending || !trail.length} onClick={() => setTrail(old => old.slice(0, -1))}>上一页</button><span>第 {trail.length + 1} 页</span><button disabled={pending || !page?.next_cursor} onClick={() => {if (page?.next_cursor) setTrail(old => [...old, page.next_cursor!]);}}>下一页</button></div>}
      {report && <>
        <p><Link href={`/models/quality/${reportId}/baseline`}>核验历史质量 →</Link></p>
        <p><Link href={`/models/propose/${reportId}/baseline`}>以此报告提出候选模型 →</Link></p>
        <section className="evidence-card"><div className="section-heading"><h2>模型与基线 · 同样本比较</h2><span className="chip">{report.cohort.horizon_sessions} 交易日 / {report.cohort.data_mode}</span></div><p className="muted">{report.manifest[0]!.base_session} — {report.manifest.at(-1)!.base_session} · {report.manifest.length} 个基准交易日</p><p className="small muted">信息截点（UTC）：{report.cohort.cutoff}</p>
          <div className="table-scroll" role="region" aria-label="同样本指标比较" tabIndex={0}><table><thead><tr><th>模型</th><th>样本数</th><th>平均绝对误差</th><th>方向命中率</th></tr></thead><tbody>{Object.entries(report.metrics).map(([name, metric]) => <tr key={name}><td>{name === "model" ? "当前实验模型" : name === "zero-return-v1" ? "零收益基线" : "五日动量基线"}</td><td>{metric?.sample_count ?? "—"}</td><td>{metric?.mae ?? "—"}</td><td>{metric?.direction_accuracy ?? "—"}</td></tr>)}</tbody></table></div><p className="small muted">数值保持原始精度。误差 0.01 为 1 个百分点；命中率 0.5 为 50%。不按单份报告自动排名或晋升。</p>{report.momentum_unavailable_reason && <p>五日动量基线仅适用于 5 交易日跨度，本报告不提供该对照。</p>}
        </section>
        <section className="evidence-card"><h2>逐样本结果</h2><p className="small muted">固定报告中的目标事实版本；后续行情修订不会覆盖此报告。收益为拆股口径的价格收益。</p><div className="table-scroll" role="region" aria-label="逐样本评测结果" tabIndex={0}><table><thead><tr><th>基准交易日</th><th>目标交易日</th><th>模型预测</th><th>实际收益</th><th>五日动量预测</th><th>零收益预测</th></tr></thead><tbody>{report.manifest.map(row => <tr key={row.label_id}><td>{row.base_session}</td><td>{row.target_session}</td><td>{row.model_expected_return}</td><td>{row.actual_return}</td><td>{row.momentum_expected_return ?? "—"}</td><td>{row.zero_expected_return}</td></tr>)}</tbody></table></div></section>
        <section className="evidence-card"><h2>复现依据</h2><dl className="facts"><dt>报告 ID</dt><dd className="hash">{reportId}</dd><dt>模型 ID</dt><dd className="hash">{report.model_id}</dd><dt>校准 ID</dt><dd className="hash"><Link href={"/model-calibrations/"+report.calibration_id}>查看固定概率校准报告 →</Link><br/>{report.calibration_id}</dd><dt>报告内容哈希</dt><dd className="hash">{report.content_hash}</dd><dt>样本集合哈希</dt><dd className="hash">{report.cohort_hash}</dd><dt>计算版本</dt><dd>{report.schema_version}</dd><dt>协议</dt><dd>{report.protocol_version}</dd></dl><details className="model-identity"><summary>核对逐样本证据与计算源码</summary><p className="small muted">这些身份固定当前报告的输入与结果，不包含模型权重。</p><pre className="report-manifest">{JSON.stringify({code_sha256: report.code_sha256, manifest: report.manifest}, null, 2)}</pre></details></section>
      </>}
    </>}
  </main>;
}
