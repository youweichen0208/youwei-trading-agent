// monthly report view: metadata plus the per-batch aggregation with
// the batch-report references each point came from.

import { esc, span, badge, fmtNum } from "../format.js";

export async function renderMonthly(el, campaignId, month, body) {
  const c = body.content;
  const meta = `
    <div class="grid">
      <div class="card"><div class="k">月份</div><div class="v">${esc(c.month ?? month)}</div></div>
      <div class="card"><div class="k">版本</div><div class="v">v${body.report_version}
        ${body.is_latest_version ? "" : badge("warn", "非最新")}</div></div>
      <div class="card"><div class="k">monthly code</div><div class="v" style="font-size:12px">${esc(body.monthly_code_version)}</div></div>
      <div class="card"><div class="k">content sha256</div><div class="v" style="font-size:12px">${esc(body.content_sha256.slice(0, 16))}…</div></div>
    </div>`;

  const rows = (c.batches ?? [])
    .map((b) => {
      const ref = b.d20_report;
      const point = ref?.mean_paired_brier_delta;
      return `<tr>
        <td>${fmtDateTimeLocal(b.decision_cutoff_utc)}</td>
        <td>${b.backfilled_plan ? badge("warn", "补登") : ""}</td>
        <td>${ref
          ? `<a href="#/${campaignId}/report/${b.batch_id}/20${ref.report_version > 1 ? "?version=" + ref.report_version : ""}">D20 v${ref.report_version}</a>`
          : span("未产生（NA）", "none")}</td>
        <td class="num">${ref ? `n=${ref.pairable_n}` : "—"}</td>
        <td class="num">${point === null || point === undefined ? span("NA", "none") : fmtNum(point, 6)}</td>
      </tr>`;
    })
    .join("");

  const summary = c.summary
    ? `<div class="section">汇总</div>
       <div class="kv">${Object.entries(c.summary)
         .map(([k, v]) => `<div class="k">${esc(k)}</div><div>${v === null ? span("NA", "none") : esc(String(v))}</div>`)
         .join("")}</div>`
    : "";

  el.innerHTML = `
    ${meta}
    ${summary}
    <div class="section">批次（等权聚合；NA 批次不稀释均值）</div>
    <table>
      <thead><tr><th>cutoff</th><th></th><th>报告引用</th><th class="num">配对</th><th class="num">配对 Brier Δ</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5">无批次</td></tr>'}</tbody>
    </table>`;
}

function fmtDateTimeLocal(iso) {
  if (!iso) return "—";
  return String(iso).replace("T", " ").slice(0, 16) + "Z";
}
