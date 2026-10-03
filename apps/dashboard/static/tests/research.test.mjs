// research view rendering tests (node --test). Shapes mirror the S12a
// API: the task view, the report content blocks (summary / evidence /
// quant / counter_evidence / limitations / versions) and the references
// resolved against the frozen snapshot. The S10a lesson: rendering
// behavior gets an automated gate, not just syntax checks.

import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.document = {
  createElement() {
    const el = { _text: "" };
    Object.defineProperty(el, "textContent", {
      set(v) { this._text = String(v); },
    });
    Object.defineProperty(el, "innerHTML", { get() { return this._text; } });
    return el;
  },
};

const { renderResearchList, renderResearch } = await import("../views/research.js");

function listBody() {
  return {
    research: [
      {
        research_id: "11111111-2222-3333-4444-555555555555",
        ticker: "S0", benchmark_ticker: "SPY", horizon_td: 20,
        status: "succeeded", job_status: "succeeded",
        created_at: "2026-10-03T02:00:00+00:00",
        latest_report_version: 2, report_versions: 2,
      },
      {
        research_id: "99999999-8888-7777-6666-555555555555",
        ticker: "S1", benchmark_ticker: "SPY", horizon_td: 1,
        status: "pending", job_status: "queued",
        created_at: "2026-10-03T03:00:00+00:00",
        latest_report_version: null, report_versions: 0,
      },
    ],
  };
}

function taskBody() {
  return {
    research_id: "11111111-2222-3333-4444-555555555555",
    ticker: "S0", benchmark_ticker: "SPY", horizon_td: 20,
    status: "succeeded", job_status: "succeeded",
    created_at: "2026-10-03T02:00:00+00:00",
    decision_cutoff_utc: "2026-10-03T02:00:00+00:00",
    entry_at_utc: "2026-10-05T13:30:00+00:00",
    exit_at_utc: "2026-11-02T20:00:00+00:00",
    target_spec_id: "excess-tr-d20-v1",
    config_sha256: "a".repeat(64),
  };
}

function reportBody() {
  return {
    research_id: "11111111-2222-3333-4444-555555555555",
    report_version: 2,
    is_latest: true,
    content_sha256: "b".repeat(64),
    created_at: "2026-10-03T02:05:00+00:00",
    content: {
      summary: {
        source_status: "produced", p_outperform: 0.55,
        expected_excess_return: 0.02, quant_relation: "adjusted",
        basis: "momentum overstates the trend",
      },
      evidence: {
        snapshot_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        as_of: "2026-10-03T02:00:00+00:00", mode: "forward",
        content_sha256: "c".repeat(64),
        row_counts: { "sec-1": 30, "sec-2": 30 },
      },
      quant: {
        baseline: { source_status: "produced", p_outperform: 0.5, expected_excess_return: 0.0, model_version: "baseline-constant-v0" },
        quant_model: { source_status: "produced", p_outperform: 0.61, expected_excess_return: 0.01, model_version: "quant-momentum-v0" },
      },
      counter_evidence: {
        warnings: [{ kind: "low_confidence", detail: "thin holiday volume" }],
        missing: ["sector context"],
        quantitative_basis: "momentum overstates the trend",
      },
      limitations: [
        "exploratory research: not a sealed prediction; excluded from the formal ledger and from forward evaluation",
        "research warning: low_confidence: thin holiday volume",
      ],
      versions: {
        report_format: "exploratory-report-v1",
        code_version: "exploratory-research-v1",
        quant_model_version: "quant-momentum-v0",
        research_model: { model_version: "mock-llm", provider: "mock" },
      },
    },
    references_resolved: [
      {
        locator: "snapshot:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/rows/3",
        note: "the breakout bar",
        row: { security_id: "sec-1", trade_date: "2026-10-01", close: 101.0 },
      },
    ],
  };
}

async function render(fn, ...args) {
  const el = { innerHTML: "" };
  await fn(el, ...args);
  return el.innerHTML;
}

test("list renders ticker, status, report versions and detail links", async () => {
  const html = await render(renderResearchList, listBody());
  assert.ok(!html.includes("[object Object]"));
  assert.match(html, /S0/);
  assert.match(html, /D20/);
  assert.match(html, /v2/);
  assert.match(html, /#\/research\/11111111-2222-3333-4444-555555555555/);
  // pending without a report shows a dash, never a fake zero
  assert.ok(html.includes("—"));
});

test("detail renders the six report blocks without leaking raw objects", async () => {
  const html = await render(renderResearch, "11111111-2222-3333-4444-555555555555", taskBody(), reportBody());
  assert.ok(!html.includes("[object Object]"));
  assert.match(html, /0\.5500|0\.55/);          // summary probability
  assert.match(html, /adjusted/);                // quant relation
  assert.match(html, /baseline-constant-v0/);    // quant block
  assert.match(html, /quant-momentum-v0/);
  assert.match(html, /aaaaaaaa-bbbb/);           // snapshot id
  assert.match(html, /low_confidence/);          // counter-evidence
  assert.match(html, /exploratory research/);    // limitations
  assert.match(html, /exploratory-research-v1/); // versions
  assert.match(html, /rows\/3/);                 // resolved reference locator
  assert.match(html, /101/);                     // the cited row's close
});

test("detail with no report yet shows an honest absence note", async () => {
  const html = await render(renderResearch, "99999999-8888-7777-6666-555555555555", {
    ...taskBody(), status: "running", job_status: "running",
  }, null);
  assert.ok(!html.includes("[object Object]"));
  assert.match(html, /running/);
  assert.ok(html.includes("暂无报告") || html.includes("尚无报告"));
});

test("multi-version reports offer version switching", async () => {
  const html = await render(renderResearch, "11111111-2222-3333-4444-555555555555", taskBody(), reportBody());
  assert.match(html, /version=1/);
  assert.match(html, /v2/);
});
