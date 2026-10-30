import { z } from "zod";
import { qualityPolicyIdSchema, experimentalEvaluationIdSchema } from "./requests.js";
const hash = z.string().regex(/^[a-f0-9]{64}$/);
const threshold = z.string().regex(/^[0-9]{1,12}(?:\.[0-9]{1,12})?$/);
const decimal = z.string().regex(/^-?[0-9]+(?:\.[0-9]+)?$/);
const nonnegative = z.string().regex(/^[0-9]+(?:\.[0-9]+)?$/);
export const historicalQualityPolicySchema = z.strictObject({
  schema_version: z.literal("historical-quality-policy-v1"),
  policy_version: z.string().min(1).max(128).regex(/^\S+$/),
  data_mode: z.enum(["REPLAY", "DEMO", "LIVE_RESEARCH"]),
  horizon_sessions: z.union([z.literal(1),z.literal(3),z.literal(5),z.literal(10),z.literal(20)]),
  minimum_samples: z.number().int().positive().safe(), maximum_mae: threshold,
  minimum_direction_accuracy: threshold.refine(value => /^0*(?:0(?:\.[0-9]+)?|1(?:\.0+)?)$/.test(value)),
  minimum_mae_improvement: threshold, require_reference: z.boolean(),
});
export const qualityPolicyResponseSchema = z.strictObject({
  id: qualityPolicyIdSchema, content_hash: hash, policy: historicalQualityPolicySchema,
}).refine(value => value.id === "historical-quality-policy-" + value.content_hash);
const qualityCheckSchema = z.discriminatedUnion("name", [
  z.strictObject({name:z.literal("minimum_samples"),observed:z.number().int().nonnegative().safe(),required:z.number().int().positive().safe(),passed:z.boolean()}),
  z.strictObject({name:z.enum(["maximum_mae","minimum_direction_accuracy"]),observed:nonnegative.nullable(),required:threshold,passed:z.boolean()}),
  z.strictObject({name:z.literal("required_reference"),observed:z.boolean(),required:z.boolean(),passed:z.boolean()}),
  z.strictObject({name:z.enum(["mae_improvement:zero-return-v1","mae_improvement:momentum20-v1","mae_improvement:reference"]),observed:decimal.nullable(),required:threshold,passed:z.boolean()}),
]);
export const qualityAssessmentResponseSchema = z.strictObject({
  schema_version:z.literal("historical-quality-assessment-v1"),
  candidate_evaluation_id:experimentalEvaluationIdSchema,reference_evaluation_id:experimentalEvaluationIdSchema.nullable(),
  cohort_hash:hash,policy:historicalQualityPolicySchema,policy_hash:hash,
  checks:z.array(qualityCheckSchema).min(5).max(7),numeric_pass:z.boolean(),
  qualification:z.literal("UNVERIFIED"),promotion_allowed:z.literal(false),
  promotion_blockers:z.tuple([z.literal("HISTORICAL_EVIDENCE_UNQUALIFIED"),z.literal("HUMAN_APPROVAL_REQUIRED")]),content_hash:hash,
}).refine(value => {
  const names=["minimum_samples","maximum_mae","minimum_direction_accuracy","required_reference","mae_improvement:zero-return-v1"];
  if(value.policy.horizon_sessions===5) names.push("mae_improvement:momentum20-v1");
  if(value.reference_evaluation_id!==null) names.push("mae_improvement:reference");
  return names.length===value.checks.length && names.every((name,i)=>name===value.checks[i]?.name)
    && value.numeric_pass===value.checks.every(check=>check.passed);
});
export type QualityAssessmentResponse = z.infer<typeof qualityAssessmentResponseSchema>;
export type QualityPolicyResponse = z.infer<typeof qualityPolicyResponseSchema>;
export const qualityPolicyPageSchema = z.strictObject({
  items:z.array(qualityPolicyResponseSchema).max(50),next_cursor:qualityPolicyIdSchema.nullable(),
}).refine(page=>new Set(page.items.map(row=>row.id)).size===page.items.length
  && (page.next_cursor===null || page.next_cursor===page.items.at(-1)?.id));
export type QualityPolicyPage = z.infer<typeof qualityPolicyPageSchema>;
