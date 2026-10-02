// report view rendering tests (node --test, no build chain — S10a philosophy).
//
// The report content's metrics object mixes scalar keys (mean_paired_brier_delta,
// paired_n) with per-source grouped keys (brier, brier_n, mse_expected_excess,
// rmse_expected_excess, mse_n) whose values are {source: value} objects
// (youwei_core/ledger/evaluation.py). The view must render the grouped keys as a
// source-keyed matrix, never as [object Object] — the defect this suite pins.

import { test } from "node:test";
import assert from "node:assert/strict";

// minimal DOM shim: format.js's esc() only needs createElement + textContent
// assignment to be readable back through innerHTML.
globalThis.document = {
  createElement() {
    const el = { _text: "" };
    Object.defineProperty(el, "textContent", {
      set(v) {
        this._text = String(v);
      },
    });
    Object.defineProperty(el, "innerHTML", { get() { return this._text; } });
    return el;
  },
};

const { renderReport } = await import("../views/report.js");

// Shapes mirror youwei_core/ledger/evaluation.py: decimal metrics are
// decimal_str strings, counts are JSON numbers, absent computations are null.
function makeBody() {
  return {
    report_id: "0123456789abcdef0123456789abcdef",
    report_version: 1,
    is_latest_version: true,
    scoring_code_version: "scoring-v1",
    content_sha256: "fedcba9876543210fedcba9876543210",
    created_at: "2026-10-02T12:00:00+00:00",
    content: {
      coverage: {
        planned_cases: 3,
        pairable: 2,
        outcome_status: { resolved: 2, unresolved: 1, unscorable: 0 },
        source_status: {
          baseline: { produced: 2, fallback: 0, unavailable: 1, no_position: 0 },
          quant_model: { produced: 2, fallback: 0, unavailable: 1, no_position: 0 },
          llm_adjusted: { produced: 0, fallback: 0, unavailable: 3, no_position: 0 },
        },
      },
      metrics: {
        mean_paired_brier_delta: "0.0123",
        paired_n: 2,
        brier: { baseline: "0.2500", quant_model: "0.2377" },
        brier_n: { baseline: 2, quant_model: 2 },
        mse_expected_excess: { baseline: "0.0004", quant_model: null },
        rmse_expected_excess: { baseline: "0.0200", quant_model: null },
        mse_n: { baseline: 2, quant_model: 0 },
      },
      cases: [
        {
          case_id: "11111111-2222-3333-4444-555555555555",
          commit_id: "aaaaaaaa-0000-0000-0000-000000000000",
          commit_timeliness: "on_time",
          sources: {
            baseline: { p_outperform: "0.5000" },
            quant_model: { p_outperform: "0.6100" },
            llm_adjusted: { p_outperform: null },
          },
          outcome: { status: "resolved", revision: 1 },
          y: 1,
          d_i: "0.0123",
          exclusions: [],
          pairable: true,
        },
      ],
    },
  };
}

async function render(body = makeBody()) {
  const el = { innerHTML: "" };
  await renderReport(el, "c1", "b1", "D20", body);
  return el.innerHTML;
}

test("grouped per-source metrics render as a source matrix, not [object Object]", async () => {
  const html = await render();
  assert.ok(!html.includes("[object Object]"), `raw object leaked into HTML:\n${html}`);
  // one column per source, values in the right cells
  assert.match(html, /<th class="num">baseline<\/th>/);
  assert.match(html, /<th class="num">quant_model<\/th>/);
  assert.match(html, /<td> ?brier ?<\/td>/);
  assert.match(html, /<td class="num">0\.2500<\/td>/);
  assert.match(html, /<td class="num">0\.2377<\/td>/);
  // counts render as integers, not 6-decimal floats
  assert.match(html, /<td class="num">2<\/td>/);
  assert.doesNotMatch(html, /2\.000000/);
});

test("null per-source values render as an explicit dash, never a blank or 0", async () => {
  const html = await render();
  // mse_expected_excess.quant_model is null; rmse ditto; both must show —
  assert.ok(html.includes("—"), "missing dash for null per-source metric");
});

test("scalar metrics keep their own rows", async () => {
  const html = await render();
  assert.match(html, /mean_paired_brier_delta/);
  assert.match(html, /0\.0123/);
  assert.match(html, /paired_n/);
});

test("a source added in a later phase (llm_adjusted) gets its own column automatically", async () => {
  const body = makeBody();
  body.content.metrics.brier.llm_adjusted = "0.2600";
  const html = await render(body);
  assert.match(html, /<th class="num">llm_adjusted<\/th>/);
  assert.match(html, /<td class="num">0\.2600<\/td>/);
  assert.ok(!html.includes("[object Object]"));
});
