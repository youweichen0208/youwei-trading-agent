"use client";
import Link from "next/link";
import { evaluationReportSchema, evaluationPageSchema, type EvaluationReport, type EvaluationSummary } from "@yta/contracts";
import { useRouter } from "next/navigation";
import { Fragment, useEffect, useState, type FormEvent } from "react";
import { api, dateTime, percent, type Me } from "../lib/api";

type Report = EvaluationReport | EvaluationSummary;
function readReport(value: unknown, mode: Me["data_mode"]): EvaluationReport {
  const parsed = evaluationReportSchema.safeParse(value);
  if (!parsed.success || parsed.data.data_mode !== mode) throw new Error("评测数据格式或统计口径不一致，请重新加载。");
  return parsed.data;
}
function readPage(value: unknown, mode: Me["data_mode"]) {
  const parsed = evaluationPageSchema.safeParse(value);
  if (!parsed.success || parsed.data.items.some(report => report.data_mode !== mode)) throw new Error("评测列表格式或数据模式不一致，请重新加载。");
  return parsed.data;
}
const statuses: Record<string, string> = {SETTLED: "已评分", UNSCORABLE: "不可评分", WAITING_TARGET: "尚未到期", WAITING_DATA: "等待行情", SETTLEMENT_FAILED: "结算失败"};
const metric = (value: string | null, suffix = "%") => value === null ? "—" : (Number(value) * 100).toFixed(2) + suffix;

export function Evaluations({me, id}: {me: Me; id?: string}) {
  const router = useRouter();
  const [items, setItems] = useState<Report[]>();
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController(); setItems(undefined); setError(""); setCursor(null);
    void (id ? api<unknown>("/evaluation-reports/" + id, {signal: abort.signal}).then(report => ({items: [readReport(report, me.data_mode)], next_cursor: null}))
      : api<unknown>("/evaluation-reports", {signal: abort.signal}).then(page => readPage(page, me.data_mode)))
      .then(page => {if (!abort.signal.aborted) {setItems(page.items); setCursor(page.next_cursor);}})
      .catch(e => {if (!abort.signal.aborted) setError(e.message);});
    return () => abort.abort();
  }, [id, reload, me.data_mode]);
  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (pending) return;
    const form = new FormData(event.currentTarget);
    const base_from = String(form.get("base_from")), base_to = String(form.get("base_to"));
    if (base_from > base_to) {setError("基准日结束日期不能早于开始日期。"); return;}
    setPending(true); setError("");
    try {
      const cutoff = new Date(String(form.get("cutoff")) + "Z").toISOString();
      const report = readReport(await api<unknown>("/evaluation-reports", {body: {base_from, base_to, cutoff}, csrf: me.csrf_token}), me.data_mode);
      router.push("/evaluations/" + report.report_id);
    } catch (e) {setError((e as Error).message);} finally {setPending(false);}
  }
  async function older() {
    if (!cursor || pending) return;
    setPending(true); setError("");
    try {
      const page = readPage(await api<unknown>("/evaluation-reports?cursor=" + encodeURIComponent(cursor)), me.data_mode);
      setItems(old => [...(old ?? []), ...page.items.filter(item => !old?.some(r => r.report_id === item.report_id))]);
      setCursor(page.next_cursor);
    } catch (e) {setError((e as Error).message);} finally {setPending(false);}
  }
  const first = items?.[0];
  const report = id && first && "manifest" in first ? first : undefined;
  return <main className="records-page evaluations-page">
    <div className="records-heading"><div><p className="eyebrow">固定样本 / 版本留痕</p><h1>{id ? "基线评测详情" : "基线评测"}</h1><p className="muted">统计价格预测误差，不代表策略回测或实际交易收益。</p></div><Link href={id ? "/evaluations" : "/predictions"}>{id ? "← 返回评测列表" : "← 返回预测记录"}</Link></div>
    {error && <div className="alert" role="alert">{error}{!items && <button onClick={() => setReload(n => n + 1)}>重新加载</button>}</div>}
    {!id && <section className="evidence-card"><h2>保存一次评测</h2><p className="muted">纳入所选基准日区间内、截止时间之前的全部匹配预测；原预测与更正均保留。生成后不会随结算修订自动改变。</p><form className="evaluation-form" onSubmit={generate}><label>基准日开始<input type="date" name="base_from" required disabled={pending}/></label><label>基准日结束<input type="date" name="base_to" required disabled={pending}/></label><label>事实截止时间（UTC）<input type="datetime-local" name="cutoff" required disabled={pending}/></label><button className="primary" disabled={pending}>{pending ? "正在保存…" : "生成并保存评测"}</button></form><p className="small muted">{me.data_mode === "REPLAY" ? "当前仅评估合成 REPLAY 记录。回放样例基准日为 2024-11-22，目标日为 2024-12-02。" : "仅评估当前环境的前瞻研究记录。"} 若提交失败，可保留表单参数重试。</p></section>}
    {!items && !error && <p role="status">正在读取评测…</p>}
    {report ? <><section className="evidence-card"><div className="section-heading"><h2>{report.base_from} — {report.base_to}</h2><span className="badge">{report.data_mode}</span></div><p>事实截止：{dateTime(report.cutoff)} 美东</p><p className="small muted">UTC：{report.cutoff}</p><dl className="facts"><dt>全部样本</dt><dd>{report.sample_count}</dd><dt>已评分样本</dt><dd>{report.scored_count}</dd><dt>更正记录</dt><dd>{report.correction_count ?? "旧版报告未披露"}</dd><dt>方向命中率</dt><dd>{metric(report.direction_accuracy)}</dd><dt>平均绝对误差</dt><dd>{metric(report.mae, " 个百分点")}</dd><dt>零收益对照误差</dt><dd>{metric(report.zero_return_mae, " 个百分点")}</dd></dl>{report.scored_count === 0 && <p className="muted">暂无已评分样本，指标显示“—”，不按零分处理。</p>}<div className="records-toolbar">{Object.entries(report.counts).map(([status, count]) => <span className="chip" key={status}>{statuses[status] ?? status}：{count}</span>)}</div></section>
      <section className="evidence-card"><h2>样本与结算版本</h2><p className="muted">下表固定报告使用的结算版本。预测详情可能已有新版本；同一基准日的多个判断不应视为独立证据。</p><dl className="facts">{Object.entries(report.base_session_counts ?? {}).map(([date, count]) => <Fragment key={date}><dt>{date}</dt><dd>{count} 条样本</dd></Fragment>)}</dl><div className="table-scroll"><table><thead><tr><th>预测</th><th>基准日</th><th>报告中状态</th><th>结算版本</th><th>更正引用</th></tr></thead><tbody>{report.manifest?.map(row => <tr key={row.prediction_id}><td><Link href={"/predictions/" + row.prediction_id}>{row.prediction_id.slice(0, 8)} →</Link></td><td>{row.base_session}</td><td>{statuses[row.status] ?? row.status}</td><td>{row.settlement_version ?? "—"}</td><td>{row.supersedes_prediction_id ? <Link href={"/predictions/" + row.supersedes_prediction_id}>原预测 →</Link> : "—"}</td></tr>)}</tbody></table></div></section>
      <section className="evidence-card"><h2>复现依据</h2><dl className="facts"><dt>报告版本</dt><dd>{report.evaluation_version}</dd><dt>模型</dt><dd>{report.model_version}</dd><dt>协议</dt><dd>{report.protocol_version}</dd><dt>报告哈希</dt><dd className="hash">{report.content_hash}</dd><dt>代码版本</dt><dd className="hash">{report.git_sha}</dd></dl></section></> : !id && items && <section className="evidence-card"><h2>已保存评测</h2>{!items.length ? <p className="muted">还没有评测报告。选择基准日区间与截止时间，保存第一份评测。</p> : <div className="table-scroll"><table><thead><tr><th>基准日区间</th><th>截止时间（美东）</th><th>已评分 / 全部</th><th>方向命中率</th><th>报告</th></tr></thead><tbody>{items.map(item => <tr key={item.report_id}><td>{item.base_from} — {item.base_to}</td><td>{dateTime(item.cutoff)}</td><td>{item.scored_count} / {item.sample_count}</td><td>{item.direction_accuracy === null ? "—" : percent(item.direction_accuracy)}</td><td><Link href={"/evaluations/" + item.report_id}>查看评测 →</Link></td></tr>)}</tbody></table></div>}{cursor && <button disabled={pending} onClick={() => void older()}>加载更早评测</button>}</section>}
  </main>;
}
