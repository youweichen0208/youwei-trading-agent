// case listing: per-source positions, confirmation state, the
// CURRENT outcome head next to the revision the latest report
// scored against. Nulls are pending states, rendered as 待/—.

import { esc, span, badge, fmtDateTime, fmtPct, fmtNum, OUTCOME_BADGE } from "../format.js";

function sourceCell(sources, name) {
  const s = sources?.[name];
  if (!s) return span("—", "none");
  if (s.source_status === "unavailable") {
    return badge("dim", `unavailable·${s.reason ?? ""}`);
  }
  const p = s.p_outperform === null || s.p_outperform === undefined
    ? "—" : (s.p_outperform * 100).toFixed(1) + "%";
  const mark = s.source_status === "fallback" ? "⚠fallback " : "";
  return `${mark}${p}`;
}

function outcomeCell(head) {
  if (!head) return span("待成熟", "none");
  const [cls, label] = OUTCOME_BADGE[head.status] ?? ["dim", head.status];
  const rev = head.revision > 1 ? ` (r${head.revision})` : "";
  const val = head.excess_return === null ? "" : ` ${fmtNum(head.excess_return)}`;
  return `${badge(cls, label + rev)}${val}`;
}

export async function renderCases(el, campaignId, body, params) {
  const page = body.page;
  const pages = body.pages;
  const horizon = params.get("horizon") ?? "";
  const batch = params.get("batch") ?? "";

  const nav = `
    <div class="pager">
      <button ${page <= 1 ? "disabled" : ""}
        onclick="location.hash='#/${campaignId}/cases?batch=${batch}&horizon=${horizon}&page=${page - 1}&size=${body.page_size}'">← 上一页</button>
      <span>第 ${page} / ${pages} 页 · 共 ${body.total} case</span>
      <button ${page >= pages ? "disabled" : ""}
        onclick="location.hash='#/${campaignId}/cases?batch=${batch}&horizon=${horizon}&page=${page + 1}&size=${body.page_size}'">下一页 →</button>
      <span style="margin-left:auto">horizon
        <select onchange="location.hash='#/${campaignId}/cases?batch=${batch}&horizon=' + this.value + '&page=1&size=${body.page_size}'">
          <option value="">全部</option>
          ${[1, 20, 60].map((h) => `<option value="${h}" ${String(h) === horizon ? "selected" : ""}>D${h}</option>`).join("")}
        </select></span>
    </div>`;

  const rows = body.items
    .map((c) => {
      const commit = c.commit;
      const timeliness = !commit
        ? span("待产生", "none")
        : badge(commit.timeliness === "on_time" ? "ok"
            : commit.timeliness === "late" ? "warn" : "dim", commit.timeliness);
      const head = c.outcome_head;
      const scoredDiff = head && c.scored_against_revision !== null
        && head.revision !== c.scored_against_revision
        ? ` ${badge("warn", "报告仍用 r" + c.scored_against_revision)}`
        : "";
      return `<tr>
        <td>${esc(c.security_symbol ?? c.security_id.slice(0, 8))}</td>
        <td>D${c.horizon_td}</td>
        <td>${fmtDateTime(c.decision_cutoff_utc)}</td>
        <td>${sourceCell(commit?.sources, "baseline")}</td>
        <td>${sourceCell(commit?.sources, "quant_model")}</td>
        <td>${sourceCell(commit?.sources, "llm_adjusted")}</td>
        <td>${timeliness}</td>
        <td>${outcomeCell(head)}${scoredDiff}</td>
        <td>${c.scored_against_revision === null ? span("—", "none") : "r" + c.scored_against_revision}</td>
        <td>${c.label_mature ? "" : badge("dim", "未成熟")}</td>
      </tr>`;
    })
    .join("");

  el.innerHTML = `
    ${nav}
    <table>
      <thead><tr>
        <th>证券</th><th>窗口</th><th>cutoff (ET)</th>
        <th class="num">baseline p</th><th class="num">quant p</th><th class="num">llm</th>
        <th>提交</th><th>当前结果</th><th>报告采用</th><th></th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="10">无 case（该过滤条件下为空）</td></tr>'}</tbody>
    </table>
    <div class="note">报告采用 = 最新批次报告评分所用的 outcome 版本；与当前结果版本不同时，说明修正后尚未重新生成报告。</div>`;
}
