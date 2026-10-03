// exploratory research views (S12b): the task list and the report
// detail — status, summary, quant, evidence, counter-evidence,
// limitations, versions and the references resolved against the frozen
// snapshot. Read-only: everything comes from the Core APIs.

import { esc, span, badge, fmtNum, fmtDateTime } from "../format.js";

export const RESEARCH_BADGE = {
  pending: ["dim", "待执行"],
  running: ["warn", "执行中"],
  succeeded: ["ok", "完成"],
  failed: ["bad", "失败"],
  cancelled: ["dim", "已取消"],
};

const _statusBadge = (status) =>
  badge((RESEARCH_BADGE[status] ?? ["dim", ""])[0], (RESEARCH_BADGE[status] ?? ["dim", status])[1]);

export async function renderResearchList(el, body) {
  const rows = (body.research ?? [])
    .map(
      (r) => `<tr>
        <td>${fmtDateTime(r.created_at)}</td>
        <td>${esc(r.ticker)} <small class="none">vs ${esc(r.benchmark_ticker)}</small></td>
        <td class="num">D${r.horizon_td}</td>
        <td>${_statusBadge(r.status)}</td>
        <td>${span(r.job_status ?? "—", r.job_status ? "" : "none")}</td>
        <td class="num">${
          r.latest_report_version
            ? `v${r.latest_report_version}${r.report_versions > 1 ? `（共 ${r.report_versions} 版）` : ""}`
            : span("—", "none")
        }</td>
        <td><a href="#/research/${r.research_id}">详情</a></td>
      </tr>`
    )
    .join("");
  el.innerHTML = `
    <div class="section">探索性研究（独立于正式预测 Ledger；不参与前向评分）</div>
    <table>
      <thead><tr>
        <th>提交时间</th><th>标的</th><th class="num">窗口</th><th>状态</th>
        <th>job</th><th class="num">报告</th><th></th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="7">尚无研究任务</td></tr>'}</tbody>
    </table>
    <div class="note">研究由聊天入口发起（S12c 接入前可经 Core API 提交）；报告固定冻结证据与配置版本。</div>`;
}

export async function renderResearch(el, researchId, task, report) {
  const q = report?.content?.question ?? {};
  const cards = `
    <div class="grid">
      <div class="card"><div class="k">状态</div><div class="v">${_statusBadge(task.status)}</div></div>
      <div class="card"><div class="k">标的</div><div class="v">${esc(task.ticker ?? "")}
        <small>vs ${esc(task.benchmark_ticker ?? "")} · D${task.horizon_td}</small></div></div>
      <div class="card"><div class="k">提交 (UTC)</div><div class="v" style="font-size:13px">${esc(task.created_at.replace("T", " ").slice(0, 19))}</div></div>
      <div class="card"><div class="k">窗口 (UTC)</div><div class="v" style="font-size:12px">
        ${esc((task.entry_at_utc ?? "").slice(0, 10))} → ${esc((task.exit_at_utc ?? "").slice(0, 10))}</div></div>
      <div class="card"><div class="k">job</div><div class="v" style="font-size:14px">${esc(task.job_status ?? "—")}</div></div>
    </div>`;

  let reportHtml = `<div class="section">报告</div>
    <div class="note">尚无报告 —— 任务${
      task.status === "failed" ? "失败" : task.status === "running" ? "执行中" : "未完成"
    }；完成后此处展示研究结果。</div>`;

  if (report) {
    const c = report.content;
    const summary = c.summary ?? {};
    const quant = c.quant ?? {};
    const evidence = c.evidence ?? {};
    const ce = c.counter_evidence ?? {};
    const versions = c.versions ?? {};

    const versionSwitch =
      report.report_version > 1
        ? Array.from({ length: report.report_version }, (_, i) => i + 1)
            .map(
              (v) =>
                `<a href="#/research/${researchId}?version=${v}"${v === report.report_version ? ' class="none"' : ""}>v${v}</a>`
            )
            .join(" · ")
        : "";

    const refRows = (report.references_resolved ?? [])
      .map(
        (r) => `<tr>
          <td style="font-size:12px">${esc(r.locator)}${r.note ? ` <small class="none">${esc(r.note)}</small>` : ""}</td>
          <td style="font-size:12px;white-space:normal">${esc(JSON.stringify(r.row))}</td>
        </tr>`
      )
      .join("");

    reportHtml = `
      <div class="section">报告 ${report.is_latest ? "" : badge("warn", "非最新版本")}</div>
      <div class="grid">
        <div class="card"><div class="k">研究结论</div><div class="v" style="font-size:14px">
          ${summary.source_status === "produced"
            ? `p=${fmtNum(summary.p_outperform)} · 超额 ${fmtNum(summary.expected_excess_return)}`
            : span("unavailable", "none")}
          ${summary.quant_relation ? badge(summary.quant_relation === "kept" ? "ok" : "warn", summary.quant_relation) : ""}
        </div></div>
        <div class="card"><div class="k">量化输入</div><div class="v" style="font-size:14px">
          ${quant.quant_model?.source_status === "produced"
            ? `p=${fmtNum(quant.quant_model.p_outperform)}（${esc(quant.quant_model.model_version ?? "")}）`
            : span("unavailable", "none")}<br>
          <small class="none">baseline p=${quant.baseline?.p_outperform != null ? fmtNum(quant.baseline.p_outperform) : "—"}
          （${esc(quant.baseline?.model_version ?? "")}）</small></div></div>
        <div class="card"><div class="k">报告版本</div><div class="v">v${report.report_version}
          <small>${versionSwitch}</small></div></div>
        <div class="card"><div class="k">证据快照</div><div class="v" style="font-size:12px">
          ${esc((evidence.snapshot_id ?? "").slice(0, 13))}…<br>
          <small class="none">${Object.entries(evidence.row_counts ?? {})
            .map(([sec, n]) => `${sec.slice(0, 8)}…:${n}`)
            .join(" · ")}</small></div></div>
      </div>
      <div class="section">研究依据与反证</div>
      <div class="kv">
        <div class="k">依据</div><div>${esc(ce.quantitative_basis ?? "—")}</div>
        <div class="k">警告</div><div>${(ce.warnings ?? []).length
          ? (ce.warnings ?? []).map((w) => `${esc(w.kind)}：${esc(w.detail)}`).join("；")
          : span("—", "none")}</div>
        <div class="k">未能落实</div><div>${(ce.missing ?? []).length ? esc((ce.missing ?? []).join("、")) : span("—", "none")}</div>
      </div>
      <div class="section">限制</div>
      <div class="kv">${(c.limitations ?? [])
        .map((l) => `<div class="k">·</div><div>${esc(l)}</div>`)
        .join("")}</div>
      <div class="section">引用（对冻结快照解析）</div>
      <table><tbody>${refRows || '<tr><td>无引用</td></tr>'}</tbody></table>
      <div class="section">版本信息</div>
      <div class="kv">
        <div class="k">报告格式</div><div>${esc(versions.report_format ?? "—")}</div>
        <div class="k">代码版本</div><div>${esc(versions.code_version ?? "—")}</div>
        <div class="k">量化模型</div><div>${esc(versions.quant_model_version ?? "—")}</div>
        <div class="k">研究模型</div><div>${versions.research_model
          ? `${esc(versions.research_model.model_version ?? "")}（${esc(versions.research_model.provider ?? "")}）`
          : span("—", "none")}</div>
        <div class="k">content sha256</div><div style="font-size:12px">${esc((report.content_sha256 ?? "").slice(0, 16))}…</div>
        <div class="k">生成时间 (UTC)</div><div>${esc((report.created_at ?? "").replace("T", " ").slice(0, 19))}</div>
      </div>`;
  }

  el.innerHTML = `
    ${cards}
    <div class="note">
      <a href="#/research">← 研究列表</a>
      · 目标协议 ${esc(task.target_spec_id ?? "")}
      · config ${esc((task.config_sha256 ?? "").slice(0, 12))}…
    </div>
    ${reportHtml}`;
}
