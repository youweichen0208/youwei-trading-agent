import { useEffect, useRef, useState } from "react";
import { marketEvidenceSchema, type ResearchReport } from "@yta/contracts";
import { api, dateTime } from "../lib/api";

type Evidence = ReturnType<typeof marketEvidenceSchema.parse>;

export function DraftEvidence({ report }: { report: Extract<ResearchReport, { status: "DRAFT_READY" }> }) {
  const [evidence, setEvidence] = useState<Evidence>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  async function load() {
    if (pending.current) return;
    const controller = new AbortController(); pending.current = controller;
    setLoading(true); setError(""); setEvidence(undefined);
    try {
      const result = marketEvidenceSchema.parse(await api<unknown>(
        `/prediction-drafts/${report.draft_id}/evidence`, {signal: controller.signal}));
      if (result.snapshot.data_mode !== report.data_mode || report.evidence.some(
        item => item.data_snapshot_hash !== result.data_snapshot_hash)) throw new Error("Mismatch");
      if (!controller.signal.aborted) { setEvidence(result); setPage(0); }
    } catch {
      if (!controller.signal.aborted) setError("历史行情证据暂时无法读取或与报告不一致，请稍后重试。");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
      if (pending.current === controller) pending.current = null;
    }
  }
  const snapshot = evidence?.snapshot;
  const end = Math.max(0, (snapshot?.bars.length ?? 0) - page * 20);
  const rows = snapshot?.bars.slice(Math.max(0, end - 20), end).toReversed();
  return <section className="draft-evidence" aria-label="草稿历史行情证据">
    {!evidence && <button disabled={loading} onClick={load}>{loading ? "正在读取历史行情…" : "查看草稿历史行情"}</button>}
    {error && <p role="alert" className="alert">{error}</p>}
    {snapshot && <details open>
      <summary>当时保存的行情 · {snapshot.bars.length} 个交易日</summary>
      <p className="muted small">这是生成草稿时保存的原始日线，不是最新行情。价格按原始精度展示，未做拆股调整。</p>
      <p>来源：{snapshot.source_id}<br/>提供方：{snapshot.provider}<br/>信息截点：{dateTime(snapshot.as_of)}（纽约时间）</p>
      <div className="evidence-table" role="region" aria-label="历史日线数据" tabIndex={0}>
        <table><caption>日线原始值（由近到远）</caption><thead><tr>{["交易日", "开", "高", "低", "收", "成交量", "版本", "可用时间（纽约）", "采集时间（纽约）"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{rows?.map(bar => <tr key={bar.session}><th scope="row">{bar.session}</th><td>{bar.open}</td><td>{bar.high}</td><td>{bar.low}</td><td>{bar.close}</td><td>{bar.volume}</td><td>{bar.revision}</td><td title={bar.available_at}>{dateTime(bar.available_at)}</td><td title={bar.retrieved_at}>{dateTime(bar.retrieved_at)}</td></tr>)}</tbody>
        </table>
      </div>
      <div className="evidence-pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>较近日线</button><span>第 {page + 1} / {Math.ceil(snapshot.bars.length / 20)} 页</span><button disabled={end <= 20} onClick={() => setPage(page + 1)}>更早日线</button></div>
      <p className="small">公司行动覆盖至 {snapshot.actions_verified_through}；已保存 {snapshot.splits.length} 条拆股记录。</p>
      {snapshot.splits.length > 0 && <ul>{snapshot.splits.map(split => <li key={split.effective_session}>{split.effective_session}：每旧股对应 {split.new_shares_per_old} 新股；来源 {split.source_id}；可用时间 {dateTime(split.available_at)}（纽约时间）</li>)}</ul>}
    </details>}
  </section>;
}
