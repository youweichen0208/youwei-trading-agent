import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { readExperimentalComparison, readExperimentalReport } from "../src/experimental-report.js";
import { safeRoute } from "../src/observability.js";

const report = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/experimental-price-report.json", import.meta.url), "utf8"));
const id = "model-evaluation-experimental-" + report.content_hash;
it("verifies a Python-generated registered report hash without rewriting timestamps or decimals", () => {
  expect(readExperimentalReport(report, id, "REPLAY")).toEqual(report);
  expect(safeRoute("/internal/v1/experimental-evaluations/" + id + "?private=value"))
    .toBe("/internal/v1/experimental-evaluations/:id");
});
it("rejects changed content, wrong identities, foreign mode and extra fields", () => {
  for (const changed of [
    {...report, secret: "private-value"}, {...report, model_sha256: "0".repeat(64)},
    {...report, cohort_hash: "0".repeat(64)},
    {...report, cohort: {...report.cohort, cutoff: report.cohort.cutoff.replace("+00:00", "Z")}},
    {...report, metrics: {...report.metrics, model: {...report.metrics.model, mae: "0.123"}}},
  ]) expect(() => readExperimentalReport(changed, id, "REPLAY")).toThrow("Experimental evaluation response is invalid");
  expect(() => readExperimentalReport(report, "model-evaluation-experimental-" + "0".repeat(64), "REPLAY")).toThrow();
  expect(() => readExperimentalReport(report, id, "LIVE_RESEARCH")).toThrow();
});

const comparison = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/experimental-report-comparison.json", import.meta.url), "utf8"));
it("preserves explicit comparison order and verifies every report content and common cohort", () => {
  const ids = comparison.evaluation_ids;
  expect(readExperimentalComparison(comparison, ids, "REPLAY")).toEqual(comparison);
  expect(safeRoute("/internal/v1/experimental-evaluations/compare")).toBe("/internal/v1/experimental-evaluations/compare");
  for (const bad of [
    {...comparison, evaluation_ids: [...ids].reverse()},
    {...comparison, reports: [...comparison.reports].reverse()},
    {...comparison, reports: comparison.reports.slice(0, 1)},
    {...comparison, evaluation_ids: [ids[0], ids[0]]},
    {...comparison, cohort_hash: "0".repeat(64)},
    {...comparison, private: "must-not-leak"},
    {...comparison, reports: [comparison.reports[0], {...comparison.reports[1], model_sha256: "0".repeat(64)}]},
  ]) expect(() => readExperimentalComparison(bad, ids, "REPLAY")).toThrow();
  expect(() => readExperimentalComparison(comparison, [...ids].reverse(), "REPLAY")).toThrow();
  expect(() => readExperimentalComparison(comparison, ids, "LIVE_RESEARCH")).toThrow();
});
