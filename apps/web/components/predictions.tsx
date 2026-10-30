"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Forecast } from "@yta/contracts";
import { api, dateTime, percent, type Me } from "../lib/api";
import { ForecastFacts } from "./forecast";

type Scored = {base_close?: string; frozen_base_close?: string; base_price_difference?: string; base_revision?: string; base_revision_snapshot_hash?: string | null; base_revision_provider?: string | null; base_revision_source_id?: string | null; base_revision_as_of?: string | null; status: "SETTLED"; actual_return: string; absolute_error: string; direction_correct: boolean; data_snapshot_hash: string};
type Unscorable = {status: "UNSCORABLE"; reason: string; evidence_hash: string; evidence: {provider: string; source_id: string; statement: string; available_at: string; source_document_sha256: string}};
const reasons: Record<string, string> = {TARGET_HALTED_NO_CLOSE: "目标交易日停牌，无有效收盘价", DELISTED_NO_PRICE: "已退市，无法取得有效目标价格", UNRESOLVED_CORPORATE_ACTION: "公司行动口径无法可靠解释"};
type Prediction = { prediction_id: string; supersedes_prediction_id?: string | null; forecast: Forecast; status: string; created_at: string; outcomes: {settlement_version: number; observed_at: string; result: Scored | Unscorable}[]; events: {type: string; occurred_at: string}[] };
const state: Record<string, string> = {FROZEN: "已冻结 · 待到期", SETTLED: "已结算", WAITING_DATA: "等待目标行情", SETTLEMENT_FAILED: "结算失败", UNSCORABLE: "不可评分"};

export function Predictions({ me, id }: { me: Me; id?: string }) {
  const [items, setItems] = useState<Prediction[]>(), [error, setError] = useState("");
  const [pending, setPending] = useState(false), [filter, setFilter] = useState("ALL");
  const [cursor, setCursor] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    const abort = new AbortController();
    setItems(undefined); setCursor(null); setError(""); setPending(false);
    const path = "/predictions" + (filter === "ALL" ? "" : "?status=" + filter);
    void (id ? api<Prediction>("/predictions/" + id, {signal: abort.signal}).then(item => ({items: [item], next_cursor: null}))
      : api<{items: Prediction[]; next_cursor: string | null}>(path, {signal: abort.signal}))
      .then(page => { if (current === generation.current) { setItems(page.items); setCursor(page.next_cursor); } })
      .catch(e => { if (!abort.signal.aborted && current === generation.current) setError(e.message); });
    return () => abort.abort();
  }, [id, filter]);
  async function older() {
    if (!cursor || pending) return;
    const current = generation.current;
    setPending(true); setError("");
    try {
      const params = new URLSearchParams({cursor});
      if (filter !== "ALL") params.set("status", filter);
      const page = await api<{items: Prediction[]; next_cursor: string | null}>("/predictions?" + params.toString());
      if (current !== generation.current) return;
      setItems(old => [...(old ?? []), ...page.items.filter(item => !old?.some(p => p.prediction_id === item.prediction_id))]);
      setCursor(page.next_cursor);
    } catch (e) { if (current === generation.current) setError((e as Error).message); }
    finally { if (current === generation.current) setPending(false); }
  }
  async function settle() {
    if (!id) return; setPending(true); setError("");
    try { setItems([await api<Prediction>(`/replay/predictions/${id}/settle`, {body: {}, csrf: me.csrf_token, key: crypto.randomUUID()})]); }
    catch (e) { setError((e as Error).message); } finally { setPending(false); }
  }
  const record = id ? items?.[0] : undefined;
  return <main className="records-page"><div className="records-heading"><div><p className="eyebrow">判断留痕 / 到期核验</p><h1>{id ? "预测详情" : "预测记录"}</h1><p className="muted">冻结时的判断永久保留；结算修订以新版本记录。</p></div><Link href={id ? "/predictions" : "/research"}>{id ? "← 返回记录" : "开始研究 →"}</Link></div>
    {error && <p className="alert" role="alert">{error}</p>}{!items && !error && <p role="status">正在读取记录…</p>}
    {record ? <div className="record-detail"><section className="forecast-card"><div className="section-heading"><h2>NVDA · 冻结预测</h2><span className="chip">{state[record.status] ?? record.status}</span></div><ForecastFacts forecast={record.forecast}/><dl className="facts"><dt>冻结时间（美东）</dt><dd>{dateTime(record.created_at)}</dd><dt>记录 ID</dt><dd className="hash">{record.prediction_id}</dd></dl>
      {record.supersedes_prediction_id && <p>此记录更正了 <Link href={"/predictions/" + record.supersedes_prediction_id}>原预测</Link>；两条记录均保留参与统计。</p>}
      {(me.data_mode === "REPLAY" || Date.now() < Date.parse(record.forecast.expires_at)) ? <><Link className="button-link" href={"/research?correct=" + record.prediction_id}>研究并更正此预测 →</Link><p className="small muted">更正需新草稿和明确确认，不覆盖原记录。</p></> : <p className="small muted">原预测已超过更正窗口，记录永久保留。</p>}
      {me.data_mode === "REPLAY" && record.forecast.data_mode === "REPLAY" && !record.outcomes.length && <><button className="primary" disabled={pending} onClick={() => void settle()}>{pending ? "正在核验…" : "模拟到期并结算"}</button><p className="small muted">使用固定合成目标价验证结算流程，不代表真实未来结果。</p></>}
      </section><section className="evidence-card"><h2>结算与证据</h2>{!record.outcomes.length && <p className="muted">尚无结算结果。目标日行情核验后追加结果。</p>}{record.outcomes.map(o => <div className="outcome" key={o.settlement_version}><h3>结算版本 {o.settlement_version}</h3>{o.result.status === "UNSCORABLE" ? <><p className="chip">不可评分 · 不纳入命中率与误差分母</p><p>{reasons[o.result.reason] ?? o.result.reason}</p><p>{o.result.evidence.statement}</p><dl className="facts"><dt>证据来源</dt><dd>{o.result.evidence.provider}</dd><dt>来源标识</dt><dd className="hash">{o.result.evidence.source_id}</dd><dt>可用时间（美东）</dt><dd>{dateTime(o.result.evidence.available_at)}</dd><dt>原文哈希</dt><dd className="hash">{o.result.evidence.source_document_sha256}</dd><dt>证据哈希</dt><dd className="hash">{o.result.evidence_hash}</dd></dl></> : <dl className="facts"><dt>实际价格收益</dt><dd>{percent(o.result.actual_return)}</dd><dt>绝对误差</dt><dd>{(Number(o.result.absolute_error) * 100).toFixed(2)} 个百分点</dd><dt>方向</dt><dd>{o.result.direction_correct ? "一致" : "不一致"}</dd>{o.result.base_revision_snapshot_hash && <><dt>冻结基准价</dt><dd>{o.result.frozen_base_close} USD</dd><dt>修订基准价</dt><dd>{o.result.base_close} USD</dd><dt>与冻结价之差</dt><dd>{o.result.base_price_difference} USD</dd><dt>修订版本</dt><dd>{o.result.base_revision}</dd><dt>修订来源</dt><dd>{o.result.base_revision_provider} · {o.result.base_revision_source_id}</dd><dt>修订截点（美东）</dt><dd>{o.result.base_revision_as_of ? dateTime(o.result.base_revision_as_of) : "—"}</dd><dt>基准修订快照</dt><dd className="hash">{o.result.base_revision_snapshot_hash}</dd></>}<dt>结果快照</dt><dd className="hash">{o.result.data_snapshot_hash}</dd></dl>}</div>)}<h3>冻结依据</h3><dl className="facts"><dt>协议</dt><dd>{record.forecast.protocol_version}</dd><dt>行情快照</dt><dd className="hash">{record.forecast.data_snapshot_hash}</dd><dt>模型哈希</dt><dd className="hash">{record.forecast.model_sha256}</dd><dt>代码版本</dt><dd className="hash">{record.forecast.git_sha}</dd><dt>日历</dt><dd>{record.forecast.calendar_version}</dd></dl><h3>生命周期</h3><ol className="event-timeline">{record.events.map((e,i) => <li key={i}><strong>{state[e.type] ?? e.type}</strong><span>{dateTime(e.occurred_at)} 美东</span></li>)}</ol></section></div> : items && !id && <><div className="records-toolbar"><span className="chip">NVDA · 5 交易日</span><label>状态 <select value={filter} onChange={e => setFilter(e.target.value)}><option value="ALL">全部</option><option value="FROZEN">已冻结</option><option value="SETTLED">已结算</option><option value="UNSCORABLE">不可评分</option><option value="WAITING_DATA">等待行情</option><option value="SETTLEMENT_FAILED">结算失败</option></select></label><Link href="/evaluations">查看基线评测 →</Link><span className="muted">已加载 {items.length} 条记录</span></div>{!items.length ? <div className="empty-state"><h2>{filter === "ALL" ? "留下第一条可验证的判断" : "暂无符合此状态的记录"}</h2><p>在研究工作台检查预测草稿，确认冻结后即可在这里追踪结果。</p><Link className="primary button-link" href="/research">开始研究 →</Link></div> : <div className="table-scroll"><table><thead><tr><th>证券 / 数据性质</th><th>基准日 → 目标日</th><th>预测收益</th><th>状态</th><th>记录</th></tr></thead><tbody>{items.map(p => <tr key={p.prediction_id}><td><strong>{p.forecast.symbol}</strong><small>{p.forecast.data_mode}</small></td><td>{p.forecast.base_session} → {p.forecast.target_session}</td><td className="number">{percent(p.forecast.expected_return)}</td><td>{state[p.status] ?? p.status}</td><td><Link href={`/predictions/${p.prediction_id}`}>查看详情 →</Link></td></tr>)}</tbody></table></div>}{cursor && <button disabled={pending} onClick={() => void older()}>{pending ? "正在加载记录…" : "加载更早记录"}</button>}</>}
  </main>;
}
