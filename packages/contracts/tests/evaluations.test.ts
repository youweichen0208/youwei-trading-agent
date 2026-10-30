import { describe, expect, it } from "vitest";
import { evaluationReportSchema, evaluationPageSchema } from "../src/index.js";

const id = "0193f00a-1234-7000-8000-000000000001";
const parent = "0193f00a-1234-7000-8000-000000000002";
const report = {
  report_id: id, content_hash: "a".repeat(64), data_mode: "REPLAY",
  base_from: "2024-11-22", base_to: "2024-11-22", cutoff: "2024-12-03T00:00:00Z",
  evaluation_version: "price-evaluation-v2", protocol_version: "eod-price-v1",
  model_version: "momentum20-v1", git_sha: "0".repeat(40),
  return_basis: "split-only-price-return", cohort_policy: "all-owner-forecasts-in-base-range",
  sample_count: 2, scored_count: 1, correction_count: 1,
  counts: {SETTLED: 1, UNSCORABLE: 0, WAITING_TARGET: 0, WAITING_DATA: 1, SETTLEMENT_FAILED: 0},
  direction_accuracy: "1.000000000000", mae: "0E-12", zero_return_mae: "0.005000000000",
  base_session_counts: {"2024-11-22": 2},
  manifest: [
    {prediction_id: parent, base_session: "2024-11-22", forecast_hash: "b".repeat(64),
      supersedes_prediction_id: null, status: "SETTLED", outcome_id: id, settlement_version: 1,
      result: {direction_correct: true, absolute_error: "0E-12"}},
    {prediction_id: id, base_session: "2024-11-22", forecast_hash: "c".repeat(64),
      supersedes_prediction_id: parent, status: "WAITING_DATA", outcome_id: null,
      settlement_version: null, result: null},
  ],
};
const {manifest: _, base_session_counts: __, ...statistics} = report;
const summary = {...statistics, created_at: "2026-10-27T00:00:00Z"};

describe("immutable evaluation response contracts", () => {
  it("preserves Decimal strings and accepts historical v1 without invented correction counts", () => {
    expect(evaluationReportSchema.parse(report).mae).toBe("0E-12");
    const {correction_count: _, ...v1} = report;
    const old = {...v1, evaluation_version: "price-evaluation-v1", manifest: v1.manifest.map(({supersedes_prediction_id: _, ...row}) => row)};
    expect(evaluationReportSchema.parse(old).correction_count).toBeUndefined();
    expect(evaluationReportSchema.safeParse({...old, evaluation_version: "price-evaluation-v2"}).success).toBe(false);
  });
  it("rejects incompatible versions, malformed dates, numbers and impossible statistics", () => {
    for (const change of [
      {evaluation_version: "future-v3"}, {base_from: "2024-11-23"}, {cutoff: "2024-12-03"},
      {sample_count: 3}, {scored_count: 0}, {correction_count: 3}, {direction_accuracy: "1.1"},
      {mae: 0}, {mae: null}, {mae: "NaN"}, {mae: "-0.1"}, {mae: "1e999"},
      {counts: {...report.counts, WAITING_DATA: -1}},
    ]) expect(evaluationReportSchema.safeParse({...report, ...change}).success).toBe(false);
  });
  it("rejects missing, duplicate, out-of-range and inconsistent manifest records", () => {
    for (const change of [
      {manifest: report.manifest.slice(1)}, {manifest: [report.manifest[0], report.manifest[0]]},
      {base_session_counts: {"2024-11-22": 1}}, {base_session_counts: {"2024-11-22": 2, "2024-11-25": 0}},
      ...[{base_session: "2024-11-25"}, {status: "SETTLED"}, {settlement_version: 1},
        {result: {}}, {supersedes_prediction_id: id}, {supersedes_prediction_id: null}]
        .map(row => ({manifest: [report.manifest[0], {...report.manifest[1], ...row}]})),
    ]) expect(evaluationReportSchema.safeParse({...report, ...change}).success).toBe(false);
  });
  it("requires null metrics for empty and wholly unscored cohorts", () => {
    const empty = {...report, sample_count: 0, scored_count: 0, correction_count: 0,
      counts: {...report.counts, SETTLED: 0, WAITING_DATA: 0}, manifest: [], base_session_counts: {},
      direction_accuracy: null, mae: null, zero_return_mae: null};
    expect(evaluationReportSchema.safeParse(empty).success).toBe(true);
    expect(evaluationReportSchema.safeParse({...empty, mae: "0E-12"}).success).toBe(false);
    expect(evaluationReportSchema.safeParse({...empty, sample_count: 1,
      counts: {...empty.counts, WAITING_DATA: 1}, base_session_counts: {"2024-11-22": 1},
      manifest: [{...report.manifest[1], supersedes_prediction_id: null}]}).success).toBe(true);
  });
  it("checks summaries and pagination without requiring full manifests", () => {
    expect(evaluationPageSchema.safeParse({items: [summary], next_cursor: id}).success).toBe(true);
    expect(evaluationPageSchema.safeParse({items: [], next_cursor: null}).success).toBe(true);
    for (const page of [
      {items: [summary, summary], next_cursor: null}, {items: [], next_cursor: id},
      {items: [summary], next_cursor: parent}, {items: [{...summary, correction_count: undefined}], next_cursor: null},
      {items: [{...summary, scored_count: 0}], next_cursor: null},
    ]) expect(evaluationPageSchema.safeParse(page).success).toBe(false);
  });
});
