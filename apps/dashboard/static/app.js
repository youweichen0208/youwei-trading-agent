// hash router + view mounting. Views render into #view; the crumb
// shows where you are. No framework: hash -> view function -> HTML.

import { getConfig, getJSON } from "./api.js";
import { renderCampaign } from "./views/campaign.js";
import { renderCases } from "./views/cases.js";
import { renderReport } from "./views/report.js";
import { renderMonthly } from "./views/monthly.js";

const view = () => document.getElementById("view");
const crumb = (html) => {
  document.getElementById("crumb").innerHTML = html;
};

async function route() {
  const hash = window.location.hash || "#/";
  const [pathPart, queryPart] = hash.slice(1).split("?");
  const params = new URLSearchParams(queryPart || "");
  const segments = pathPart.split("/").filter(Boolean);

  try {
    if (segments.length === 0) {
      const cfg = await getConfig();
      location.hash = `#/${cfg.campaign_id}`;
      return;
    }
    const campaignId = segments[0];
    if (segments.length === 1) {
      crumb("campaign 总览");
      const status = await getJSON(`/v1/campaigns/${campaignId}/status`);
      await renderCampaign(view(), campaignId, status);
    } else if (segments[1] === "cases") {
      crumb('<a href="#/' + campaignId + '">campaign</a> / cases');
      const body = await getJSON(`/v1/campaigns/${campaignId}/cases`, {
        batch_id: params.get("batch"),
        horizon_td: params.get("horizon"),
        page: params.get("page") || 1,
        page_size: params.get("size") || 50,
      });
      await renderCases(view(), campaignId, body, params);
    } else if (segments[1] === "report") {
      // #/{cid}/report/{batchId}/{horizon}?version=
      const [batchId, horizon] = [segments[2], segments[3]];
      crumb(
        `<a href="#/${campaignId}">campaign</a> / 报告 D${horizon}` +
        (params.get("version") ? ` v${params.get("version")}` : "")
      );
      const body = await getJSON(
        `/v1/campaigns/${campaignId}/batches/${batchId}/reports/${horizon}`,
        { version: params.get("version") }
      );
      await renderReport(view(), campaignId, batchId, Number(horizon), body);
    } else if (segments[1] === "monthly") {
      const month = segments[2];
      crumb(`<a href="#/${campaignId}">campaign</a> / 月报 ${month}`);
      const body = await getJSON(
        `/v1/campaigns/${campaignId}/monthly-reports/${month}`,
        { version: params.get("version") }
      );
      await renderMonthly(view(), campaignId, month, body);
    } else {
      view().innerHTML = `<div class="error">未知路由：${hash}</div>`;
    }
  } catch (err) {
    view().innerHTML = `<div class="error">${err.message}</div>`;
  }
}

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
