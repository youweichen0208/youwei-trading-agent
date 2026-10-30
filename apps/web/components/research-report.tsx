import type { ResearchReport } from "@yta/contracts";
import { dateTime } from "../lib/api";
import { DraftEvidence } from "./draft-evidence";

export function ResearchReportDetails({ report }: { report: ResearchReport }) {
  const mode = { REPLAY: "合成回放", DEMO: "演示数据", LIVE_RESEARCH: "真实研究" }[report.data_mode];
  return <details className="research-report">
    <summary><span>研究依据与风险</span><span className="report-count">{report.evidence.length} 项来源 · {mode}</span></summary>
    <div className="report-content">
      <p className="report-cutoff">信息截点 <time dateTime={report.as_of} title={report.as_of}>{dateTime(report.as_of)}</time>（纽约时间）</p>
      {report.status === "UNAVAILABLE" && <p className="alert">数据尚未就绪，本次没有生成预测草稿。</p>}
      <h3>风险与限制</h3>
      <ul className="report-risks">{report.risks.map((risk, index) => <li key={index}>{risk}</li>)}</ul>
      <h3>引用来源</h3>
      {report.evidence.length === 0 ? <p className="muted">本次没有可引用的已核验数据。</p> : <ul className="report-sources">{report.evidence.map((item, index) => <li key={item.tool_call_id}>
        <div className="report-source-heading"><span className="report-reference">[{index + 1}]</span><strong>{item.tool === "market_snapshot" ? "行情快照" : "技术指标"}</strong></div>
        <p className="report-source-id">{item.source_id}</p>
        <p className="muted small">数据可用时间 <time dateTime={item.available_at} title={item.available_at}>{dateTime(item.available_at)}</time>（纽约时间）</p>
        <details className="report-fingerprint"><summary>核对数据快照</summary><code>{item.data_snapshot_hash}</code></details>
      </li>)}</ul>}
      {report.status === "DRAFT_READY" && <><DraftEvidence key={report.draft_id} report={report}/><p className="report-footnote">这些依据对应当前预测草稿。冻结仍需你单独检查并确认。</p></>}
    </div>
  </details>;
}
