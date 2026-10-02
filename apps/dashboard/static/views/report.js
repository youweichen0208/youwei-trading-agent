// batch report view: metadata (version, hash, code versions),
// coverage, metrics and the per-case rows the report stands on.
// Version switching reads saved reports only — nothing recomputes.

import { esc, span, badge, fmtNum, OUTCOME_BADGE } from "../format.js";

export async function renderReport(el, campaignId, batchId, horizon, body) {
  const meta = `
    <div class="grid">
      <div class="card"><div class="k">版本</div><div class="v">v${body.report_version}
        ${body.is_latest_version ? "" : badge("warn", "非最新")}</div></div>
      <div class="card"><div class="k">报告 ID</div><div class="v" style="font-size:12px">${esc(body.report_id.slice(0, 18))}…</div></div>
      <div class="card"><div class="k">scoring code</div><div class="v" style="font-size:12px">${esc(body.scoring_code_version)}</div></div>
      <div class="card"><div class="k">content sha256</div><div class="v" style="font-size:12px">${esc(body.content_sha256.slice(0, 16))}…</div></div>
      <div class="card"><div class="k">生成时间 (UTC)</div><div class="v" style="font-size:13px">${esc(body.created_at.replace("T", " ").slice(0, 19))}</div></div>
    </div>`;

  const c = body.content;
  const cov = c.coverage;
  const m = c.metrics;

  const coverageCards = `
    <div class="grid">
      <div class="card"><div class="k">计划 case</div><div class="v">${cov.planned_cases}</div></div>
      <div class="card"><div class="k">可配对</div><div class="v">${cov.pairable}
        <small>配对分母（其余按协议排除）</small></div></div>
      <div class="card"><div class="k">结果</div><div class="v" style="font-size:14px">
        resolved ${cov.outcome_status.resolved} · unresolved ${cov.outcome_status.unresolved} ·
        unscorable ${cov.outcome_status.unscorable}</div></div>
      <div class="card"><div class="k">来源产生</div><div class="v" style="font-size:14px">
        ${Object.entries(cov.source_status)
          .map(([s, st]) => `${s} ${st.produced}${st.fallback ? "⚠" + st.fallback : ""}${st.unavailable ? " ✕" + st.unavailable : ""}`)
          .join(" · ")}</div></div>
    </div>`;

  const metricRows = Object.entries(m)
    .map(
      ([k, v]) =>
        `<tr><td>${esc(k)}</td><td class="num">${v === null ? span("NA", "none") : fmtNum(v, 6)}</td></tr>`
    )
    .join("");

  const caseRows = c.cases
    .map((row) => {
      const out = row.outcome;
      const outCell = !out
        ? span("—", "none")
        : badge((OUTCOME_BADGE[out.status] ?? ["dim", ""])[0], `${out.status} r${out.revision}`);
      return `<tr>
        <td>${esc(row.case_id.slice(0, 8))}…</td>
        <td>${row.commit_id ? esc(row.commit_timeliness ?? "") : span("no_commit", "none")}</td>
        <td class="num">${row.sources?.baseline?.p_outperform !== undefined && row.sources?.baseline?.p_outperform !== null
          ? fmtNum(row.sources.baseline.p_outperform) : "—"}</td>
        <td class="num">${row.sources?.quant_model?.p_outperform !== undefined && row.sources?.quant_model?.p_outperform !== null
          ? fmtNum(row.sources.quant_model.p_outperform) : "—"}</td>
        <td class="num">${row.sources?.llm_adjusted?.p_outperform ?? "—"}</td>
        <td>${outCell}</td>
        <td class="num">${row.y === null ? span("—", "none") : fmtNum(row.y)}</td>
        <td class="num">${row.d_i === null ? span("—", "none") : fmtNum(row.d_i)}</td>
        <td>${row.pairable ? badge("ok", "入配对") : badge("dim", row.exclusions.join(",") || "排除")}</td>
      </tr>`;
    })
    .join("");

  el.innerHTML = `
    ${meta}
    ${coverageCards}
    <div class="section">指标</div>
    <table style="max-width:520px"><tbody>${metricRows}</tbody></table>
    <div class="section">case 行（结果列为报告采用版本）</div>
    <table>
      <thead><tr>
        <th>case</th><th>提交</th><th class="num">baseline p</th><th class="num">quant p</th>
        <th class="num">llm p</th><th>结果</th><th class="num">y</th><th class="num">d_i</th><th>配对</th>
      </tr></thead>
      <tbody>${caseRows}</tbody>
    </table>`;
}
