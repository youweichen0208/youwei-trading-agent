"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { experimentalReportComparisonSchema, type ExperimentalReportComparison } from "@yta/contracts";
import { api, HttpError, type Me } from "../lib/api";
import { useReportSelection } from "../lib/report-selection";

export function ModelComparison({me}: {me: Me}) {
  const selection = useReportSelection(me);
  const router = useRouter();
  const active = useRef<AbortController | null>(null);
  const [result, setResult] = useState<ExperimentalReportComparison>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {setResult(undefined); setError("");}, [selection.ids]);
  useEffect(() => () => active.current?.abort(), []);
  async function compare() {
    if (active.current || selection.ids.length < 2 || !me.experimental_models_enabled) return;
    const abort = new AbortController(); active.current = abort;
    const ids = [...selection.ids]; setPending(true); setResult(undefined); setError("");
    try {
      const value = experimentalReportComparisonSchema.parse(await api<unknown>("/experimental-evaluations/compare", {
        body: {evaluation_ids: ids}, csrf: me.csrf_token, signal: abort.signal,
      }));
      if (value.evaluation_ids.length !== ids.length || value.evaluation_ids.some((id, i) => id !== ids[i])
        || value.reports.some(report => report.cohort.data_mode !== me.data_mode)) throw new Error("Invalid comparison");
      if (!abort.signal.aborted) setResult(value);
    } catch (e) {
      if (abort.signal.aborted) return;
      if (e instanceof HttpError && e.status === 401) {router.replace("/login"); return;}
      setError(e instanceof HttpError && e.code === "EVALUATION_COHORT_MISMATCH"
        ? "这些报告使用的样本或信息截点不同。请移除不匹配的报告，选择同一组目标事实与信息截点。"
        : e instanceof HttpError && e.status === 404 ? "所选报告在当前环境中不存在，请移除后重新选择。"
        : "暂时无法比较，请保留选择并重试。不会用其他报告替代。");
    } finally {if (!abort.signal.aborted) {setPending(false); active.current = null;}}
  }
  return <main className="records-page experimental-reports-page">
    <div className="records-heading"><div><p className="eyebrow">相同样本 / 明确版本</p><h1>实验报告比较</h1><p className="muted">选择 2–10 份已登记报告，核对共同样本上的价格预测表现。</p></div><Link href="/models">← 选择模型与报告</Link></div>
    {!me.experimental_models_enabled ? <section className="empty-state"><h2>实验评测尚未启用</h2></section> : <>
      <aside className="model-scope"><span className="badge">未经正式验收</span><div><h2>历史比较，不构成模型晋升</h2><p>报告必须使用相同目标事实、信息截点和跨度。样本可能相关，单次比较不代表预测能力。{me.data_mode === "REPLAY" && "当前为合成 REPLAY。"}</p></div></aside>
      <section className="evidence-card"><div className="section-heading"><h2>已选报告 · {selection.ids.length} / 10</h2><button disabled={pending || !selection.ids.length} onClick={selection.clear}>清空选择</button></div>
        {!selection.ids.length && <p>从<Link href="/models">模型目录</Link>打开评测记录，点击“加入比较”。选择会在当前浏览器标签页保留。</p>}
        <ul className="comparison-selection">{selection.ids.map((id, i) => <li key={id}><div><Link href={"/model-evaluations/" + id}>查看报告 {i + 1} →</Link><p className="hash small muted">{id}</p></div><button disabled={pending} aria-label={`移除报告 ${i + 1}`} onClick={() => selection.toggle(id)}>移除</button></li>)}</ul>
        <button className="primary" disabled={!selection.ready || pending || selection.ids.length < 2} onClick={() => void compare()}>{pending ? "正在核对报告…" : "比较所选报告"}</button>
      </section>
      {(error || selection.error) && <div role="alert" className="alert">{error || selection.error}</div>}
      {pending && <p role="status">正在重放所选报告，请稍候。</p>}
      {result && <section className="evidence-card"><h2>共同样本上的价格指标</h2><p className="muted">{result.reports[0]!.cohort.horizon_sessions} 交易日 · 每份 {result.reports[0]!.manifest.length} 个样本 · {me.data_mode}</p><p className="small muted">信息截点（UTC）：{result.reports[0]!.cohort.cutoff}</p>
        <div className="table-scroll" role="region" aria-label="多报告指标比较" tabIndex={0}><table><thead><tr><th>报告 / 模型</th><th>比较对象</th><th>样本数</th><th>平均绝对误差</th><th>方向命中率</th></tr></thead><tbody>{result.reports.flatMap((report, index) => Object.entries(report.metrics).map(([name, metric]) => <tr key={`${index}-${name}`}><td><Link href={"/model-evaluations/" + result.evaluation_ids[index]}>报告 {index + 1}</Link><p className="small muted">模型 {report.model_id.slice(-12)}</p></td><td>{name === "model" ? "实验模型" : name === "zero-return-v1" ? "零收益基线" : "五日动量基线"}</td><td>{metric?.sample_count ?? "—"}</td><td>{metric?.mae ?? "—"}</td><td>{metric?.direction_accuracy ?? "—"}</td></tr>))}</tbody></table></div>
        <p className="small muted">按选择顺序展示，不按成绩排名。数值保留原始精度；误差 0.01 为 1 个百分点，命中率 0.5 为 50%。五日动量基线仅适用于 5 交易日跨度，其他跨度显示“—”。</p>
        {result.reports.length===2 && <div className="model-pagination">{result.evaluation_ids.map((id,i)=><Link key={id} href={`/models/propose/${id}/${result.evaluation_ids[1-i]}`}>以报告 {i+1} 提出候选 →</Link>)}</div>}
        <details className="model-identity"><summary>核对共同样本与完整模型身份</summary><p className="hash">样本集合：{result.cohort_hash}</p>{result.reports.map((report, i) => <p className="hash" key={result.evaluation_ids[i]}>报告 {i + 1} 模型：{report.model_id}</p>)}</details>
      </section>}
    </>}
  </main>;
}
