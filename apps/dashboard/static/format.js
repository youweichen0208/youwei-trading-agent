// formatting helpers: pending states are dashes/待 badges, never
// zeros — the denominator and every judgment come from Core.

export function fmtDateTime(iso) {
  if (!iso) return span("—", "none");
  const d = new Date(iso);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  }).format(d);
}

export function fmtDate(iso) {
  if (!iso) return span("—", "none");
  return iso.slice(0, 10);
}

export function fmtNum(x, digits = 4) {
  if (x === null || x === undefined) return span("—", "none");
  if (typeof x !== "number") return String(x);
  return x.toFixed(digits);
}

export function fmtPct(p) {
  if (p === null || p === undefined) return span("—", "none");
  return `${(p * 100).toFixed(1)}%`;
}

export function esc(s) {
  const div = document.createElement("div");
  div.textContent = String(s ?? "");
  return div.innerHTML;
}

export function span(text, cls = "") {
  return `<span class="${cls}">${esc(text)}</span>`;
}

// badge(kind, text): ok / warn / bad / dim; pending states use dim
export function badge(kind, text) {
  return `<span class="badge ${kind}">${esc(text)}</span>`;
}

export const PHASE_BADGE = {
  awaiting_cutoff: ["dim", "待 cutoff"],
  in_window: ["warn", "窗口内"],
  sealed: ["ok", "已提交"],
  missed: ["bad", "漏跑"],
};

export const OUTCOME_BADGE = {
  resolved: ["ok", "resolved"],
  unresolved: ["warn", "unresolved"],
  unscorable: ["bad", "unscorable"],
};
