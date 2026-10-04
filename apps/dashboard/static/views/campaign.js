// campaign overview: the frozen plan is the denominator; the batch
// table shows lifecycle phases. Everything not yet produced is a
// pending state, never a failure.

import { esc, span, badge, fmtDateTime, PHASE_BADGE } from "../format.js";

export async function renderCampaign(el, campaignId, status) {
  const plan = status.plan;
  const pb = plan.batches;
  const enabled = status.enabled_sources.map(esc).join("、");
  const llmNote = status.enabled_sources.includes("llm_adjusted")
    ? ""
    : `<span class="note">llm_adjusted 未启用（Phase 1A）——来源以 not_enabled 封存，如实计数。</span>`;

  const planCards = `
    <div class="grid">
      <div class="card"><div class="k">冻结计划 · 批次</div><div class="v">${plan.planned_batches}</div></div>
      <div class="card"><div class="k">冻结计划 · Case</div><div class="v">${plan.planned_cases}
        <small>${plan.planned_batches} 批 × ${plan.panel_size} 证券 × ${plan.horizons.join("/")}D</small></div></div>
      <div class="card"><div class="k">已预注册批次</div><div class="v">${pb.registered}
        <small>补登 ${pb.backfilled}</small></div></div>
      <div class="card"><div class="k">待预注册</div><div class="v">${pb.pending_registration}
        <small>未来 cutoff，正常等待</small></div></div>
      <div class="card"><div class="k">过期未注册</div><div class="v">${pb.overdue_unregistered === 0
        ? span(String(pb.overdue_unregistered), "")
        : span(String(pb.overdue_unregistered), "badge bad")}
        <small>调度器应补登</small></div></div>
    </div>`;

  const t = status.totals;
  const totalsCards = `
    <div class="grid">
      <div class="card"><div class="k">已建批次 Case</div><div class="v">${t.planned_cases}</div></div>
      <div class="card"><div class="k">已提交</div><div class="v">${t.with_commit}
        <small>on_time ${t.on_time} / late ${t.late} / 未确认 ${t.unconfirmed}</small></div></div>
      <div class="card"><div class="k">未提交</div><div class="v">${t.no_commit}</div></div>
      <div class="card"><div class="k">结果状态</div><div class="v" style="font-size:14px">
        resolved ${t.outcome_resolved} · unresolved ${t.outcome_unresolved} ·
        unscorable ${t.outcome_unscorable} · ${span("待成熟 " + t.outcome_pending, "none")}</div></div>
    </div>
    <div class="note">启用来源：${enabled} · campaign ${esc(status.campaign_key)}（${esc(status.status)}）· release ${esc(status.release_id)}</div>
    ${llmNote}`;

  const rows = status.batches
    .map((b) => {
      const [cls, label] = PHASE_BADGE[b.phase] ?? ["dim", b.phase];
      return `<tr>
        <td>${badge(b.backfilled_plan ? "warn" : cls, b.backfilled_plan ? "补登记录" : label)}</td>
        <td>${fmtDateTime(b.decision_cutoff_utc)}</td>
        <td>${fmtDateTime(b.prediction_deadline_utc)}</td>
        <td class="num">${b.with_commit}/${b.planned_cases}</td>
        <td class="num">${b.on_time}</td>
        <td class="num">${b.late}</td>
        <td class="num">${b.outcome_resolved}/${b.outcome_unresolved}/${b.outcome_unscorable}/${b.outcome_pending}</td>
        <td>${Object.entries(b.reports)
          .map(([h, v]) => `<a href="#/${campaignId}/report/${b.batch_id}/${h}">D${h}·v${v}</a>`)
          .join(" ") || span("待产生", "none")}</td>
        <td><a href="#/${campaignId}/cases?batch=${b.batch_id}">cases →</a></td>
      </tr>`;
    })
    .join("");

  el.innerHTML = `
    ${planCards}
    ${totalsCards}
    <div class="section">批次（结果列 = resolved/unresolved/unscorable/待成熟）</div>
    <table>
      <thead><tr>
        <th>阶段</th><th>cutoff (ET)</th><th>deadline (ET)</th>
        <th class="num">提交</th><th class="num">on_time</th><th class="num">late</th>
        <th class="num">结果</th><th>报告</th><th></th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="9">尚无预注册批次</td></tr>'}</tbody>
    </table>`;
}
