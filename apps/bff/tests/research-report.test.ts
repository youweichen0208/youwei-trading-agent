import { describe, expect, it } from "vitest";
import { researchReportSchema, type ResearchEvidence } from "@yta/contracts";
import { validateResearchReport, type ReportContext } from "../src/research-report.js";

const hash = "a".repeat(64), asOf = "2024-11-22T21:10:00.000001Z";
const evidence: ResearchEvidence[] = ["market_snapshot", "indicators"].map((tool, i) => ({
  tool_call_id: `verified-tool-${i}`, tool: tool as ResearchEvidence["tool"],
  source_id: "synthetic-fixture", data_snapshot_hash: hash, available_at: asOf,
}));
const draftId = "00000000-0000-4000-8000-000000000087";
const context: ReportContext = {
  data_mode: "REPLAY", as_of: asOf, evidence,
  draft: { draft_id: draftId, data_snapshot_hash: hash, data_mode: "REPLAY", as_of: asOf },
  failure_code: null,
};
function report() {
  return { schema_version: "research-report-v1", profile_id: "alpha-orchestrator", symbol: "NVDA",
    data_mode: "REPLAY", as_of: asOf, summary: "Synthetic REPLAY observation.",
    risks: ["Synthetic data does not establish real predictive performance."],
    status: "DRAFT_READY", evidence: structuredClone(evidence), draft_id: draftId };
}

describe("research report provenance boundary", () => {
  it("accepts exact server-backed references without freezing anything", () => {
    const before = structuredClone(context);
    expect(validateResearchReport(report(), context)).toEqual(report());
    expect(context).toEqual(before);
  });
  it.each([
    { confirmed: true }, { expected_return: "0.5" }, { owner_id: draftId },
    { probability_up: "0.9" }, { tool_permissions: ["freeze"] },
    { data_mode: "LIVE_RESEARCH" }, { as_of: "2024-11-23T21:10:00Z" },
    { draft_id: "00000000-0000-4000-8000-000000000088" }, { risks: [] },
  ])("rejects injected or mismatched fields %j", change => {
    expect(() => validateResearchReport({ ...report(), ...change }, context)).toThrow("verified run evidence");
  });
  it.each([
    { tool_call_id: "invented" }, { source_id: "different-provider" },
    { data_snapshot_hash: "b".repeat(64) }, { available_at: "2024-11-22T21:10:00Z" },
  ])("rejects forged evidence %j", change => {
    const value = report(); Object.assign(value.evidence[0], change);
    expect(() => validateResearchReport(value, context)).toThrow("verified run evidence");
  });
  it("rejects duplicate citations and missing tool kinds", () => {
    const value = report(); value.evidence = [value.evidence[0], value.evidence[0]];
    expect(() => validateResearchReport(value, context)).toThrow();
    const duplicateContext = { ...context, evidence: [...evidence, evidence[0]] };
    expect(() => validateResearchReport(report(), duplicateContext)).toThrow();
    const sameKind = evidence.map(item => ({ ...item, tool: "market_snapshot" as const }));
    expect(() => validateResearchReport({ ...report(), evidence: sameKind }, { ...context, evidence: sameKind })).toThrow();
  });
  it("rejects future evidence even within the same millisecond", () => {
    const future = evidence.map(item => ({ ...item, available_at: "2024-11-22T21:10:00.000002Z" }));
    expect(() => validateResearchReport({ ...report(), evidence: future }, { ...context, evidence: future })).toThrow();
    const earlier = evidence.map(item => ({ ...item, available_at: "2024-11-22T16:10:00.000000-05:00" }));
    expect(validateResearchReport({ ...report(), evidence: earlier }, { ...context, evidence: earlier }).status).toBe("DRAFT_READY");
  });
  it("rejects absent or inconsistent server draft metadata", () => {
    for (const draft of [null, { ...context.draft!, data_snapshot_hash: "b".repeat(64) },
      { ...context.draft!, data_mode: "LIVE_RESEARCH" as const }, { ...context.draft!, as_of: "2024-11-23T00:00:00Z" }]) {
      expect(() => validateResearchReport(report(), { ...context, draft })).toThrow();
    }
  });
  it("permits unavailable only for a matching verified failure and no draft reference", () => {
    const value = { ...report(), status: "UNAVAILABLE", draft_id: null, evidence: [], reason_code: "DATA_UNAVAILABLE" };
    const failed: ReportContext = { ...context, draft: null, failure_code: "DATA_UNAVAILABLE" };
    expect(validateResearchReport(value, failed).status).toBe("UNAVAILABLE");
    expect(() => validateResearchReport(value, context)).toThrow();
    expect(() => validateResearchReport({ ...value, reason_code: "DATA_INVALID" }, failed)).toThrow();
    expect(researchReportSchema.safeParse({ ...value, draft_id: draftId }).success).toBe(false);
    expect(() => validateResearchReport(report(), failed)).toThrow();
  });
});
