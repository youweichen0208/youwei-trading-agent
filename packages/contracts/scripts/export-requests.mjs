import { readFileSync, writeFileSync } from "node:fs";
import { z } from "zod";
import * as contracts from "../dist/index.js";

const names = [
  "trainingEvidenceQuerySchema",
  "backtestQuerySchema", "resourceIdSchema", "stockSymbolSchema", "idempotencyKeySchema", "emptyRequestSchema",
  "loginRequestSchema", "pageQuerySchema", "predictionQuerySchema", "messageQuerySchema",
  "eventQuerySchema", "createSessionSchema", "sendMessageSchema", "freezeRequestSchema",
  "qualityGateIdSchema", "qualityGateRequestSchema", "probabilityPolicyQuerySchema", "probabilityPolicyIdSchema", "probabilityAssessmentIdSchema", "probabilityAssessmentRequestSchema", "experimentalCalibrationIdSchema", "modelReviewSubmissionSchema", "qualityPolicyQuerySchema", "qualityPolicyIdSchema", "qualityAssessmentIdSchema", "qualityAssessmentRequestSchema", "modelProposalQuerySchema", "modelProposalKeySchema", "modelProposalRequestSchema", "experimentalComparisonRequestSchema", "evaluationRequestSchema", "experimentalEvaluationQuerySchema", "experimentalEvaluationIdSchema", "experimentalModelIdSchema", "experimentalModelQuerySchema",
];
const artifact = {
  schema_version: 1,
  description: "BFF request input schemas. Zod remains authoritative for normalization and cross-field refinements; this is not a complete OpenAPI document.",
  schemas: Object.fromEntries(names.map(name => [name, z.toJSONSchema(contracts[name], {io: "input"})])),
};
const file = new URL("../bff-requests.schemas.json", import.meta.url);
const expected = JSON.stringify(artifact, null, 2) + "\n";
const args = process.argv.slice(2);
if (args.length && (args.length !== 1 || args[0] !== "--check")) throw new Error("Usage: export-requests.mjs [--check]");
if (args[0] === "--check") {
  if (readFileSync(file, "utf8") !== expected) throw new Error("BFF request contract drift: run pnpm contracts:requests:export");
} else writeFileSync(file, expected);
console.log("BFF request schemas: " + names.length + (args.length ? " checked" : " exported"));
