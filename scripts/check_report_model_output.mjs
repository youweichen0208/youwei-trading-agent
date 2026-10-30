import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { validateResearchReport } from "../apps/bff/dist/research-report.js";

try {
  const revision = process.argv[2] ?? "original";
  assert.ok(["original", "wording-v2"].includes(revision));
  const suffix = revision === "original" ? "" : "-wording-v2";
  const input = JSON.parse(readFileSync(".local/report-model-input.json", "utf8"));
  const observations = [];
  for (const name of ["ready", "unavailable"]) {
    const raw = readFileSync(`.local/report-model-${name}${suffix}.json`, "utf8");
    const result = JSON.parse(raw);
    assert.equal(result.status, "completed");
    assert.equal(result.extra_tools, 0);
    const context = input.cases[name];
    const report = validateResearchReport(result.report, context);
    assert.equal(report.status, name === "ready" ? "DRAFT_READY" : "UNAVAILABLE");
    if (name === "ready") assert.equal(report.evidence.length, 2);
    else { assert.equal(report.draft_id, null); assert.equal(report.evidence.length, 0); }
    assert.throws(() => validateResearchReport({...report, confirmed: true}, context));
    assert.throws(() => validateResearchReport({...report, data_mode: name === "ready" ? "LIVE_RESEARCH" : "REPLAY"}, context));
    if (name === "ready") {
      const forged = structuredClone(report);
      forged.evidence[0].tool_call_id = "invented-reference";
      assert.throws(() => validateResearchReport(forged, context));
    }
    observations.push({case: name, requested_model: result.requested_model,
      status: report.status, data_mode: report.data_mode, evidence_count: report.evidence.length,
      trusted_context_validation: "passed", negative_controls_rejected: true,
      extra_tools: result.extra_tools, usage: result.usage,
      output_sha256: createHash("sha256").update(raw).digest("hex")});
  }
  console.log(JSON.stringify({observations, qualification_status: "UNVERIFIED"}));
} catch {
  console.error(JSON.stringify({error_code: "REPORT_MODEL_VALIDATION_FAILED"}));
  process.exitCode = 1;
}
