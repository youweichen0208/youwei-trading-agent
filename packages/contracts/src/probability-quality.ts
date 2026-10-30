import {z} from "zod";
import {probabilityPolicyIdSchema,experimentalCalibrationIdSchema,experimentalModelIdSchema} from "./requests.js";
import {calibrationReportSchema} from "./calibration.js";
const hash=z.string().regex(/^[a-f0-9]{64}$/);
const threshold=z.string().regex(/^(?:0(?:\.[0-9]{1,12})?|1(?:\.0{1,12})?)$/);
const count=z.number().int().nonnegative().safe(),minimum=z.number().int().positive().safe();
export const probabilityQualityPolicySchema=z.strictObject({
  schema_version:z.literal("probability-quality-policy-v1"),policy_version:z.string().min(1).max(128).regex(/^\S+$/),
  data_mode:z.enum(["REPLAY","DEMO","LIVE_RESEARCH"]),
  horizon_sessions:z.union([z.literal(1),z.literal(3),z.literal(5),z.literal(10),z.literal(20)]),
  minimum_calibration_samples:minimum,minimum_evaluation_samples:minimum,
  minimum_evaluation_up:minimum,minimum_evaluation_non_up:minimum,maximum_brier:threshold,maximum_ece:threshold,
});
export const probabilityPolicyResponseSchema=z.strictObject({id:probabilityPolicyIdSchema,content_hash:hash,policy:probabilityQualityPolicySchema})
  .refine(row=>row.id==="probability-quality-policy-"+row.content_hash);
const check=z.discriminatedUnion("name",[
  z.strictObject({name:z.enum(["minimum_calibration_samples","minimum_evaluation_samples","minimum_evaluation_up","minimum_evaluation_non_up"]),observed:count,required:minimum,passed:z.boolean()}),
  z.strictObject({name:z.enum(["maximum_brier","maximum_ece"]),observed:calibrationReportSchema.shape.reliability.shape.brier,required:threshold,passed:z.boolean()}),
]);
export const probabilityAssessmentResponseSchema=z.strictObject({
  schema_version:z.literal("probability-quality-assessment-v1"),calibration_id:experimentalCalibrationIdSchema,model_id:experimentalModelIdSchema,
  calibration_content_hash:hash,report_hash:hash,calibration_cohort_hash:hash,evaluation_cohort_hash:hash,
  policy:probabilityQualityPolicySchema,policy_hash:hash,checks:z.array(check).length(6),numeric_pass:z.boolean(),
  qualification:z.literal("UNVERIFIED"),promotion_allowed:z.literal(false),
  promotion_blockers:z.tuple([z.literal("HISTORICAL_EVIDENCE_UNQUALIFIED"),z.literal("HUMAN_APPROVAL_REQUIRED")]),content_hash:hash,
}).refine(row=>{
  const names=["minimum_calibration_samples","minimum_evaluation_samples","minimum_evaluation_up","minimum_evaluation_non_up","maximum_brier","maximum_ece"];
  return names.every((name,i)=>row.checks[i]?.name===name) && row.numeric_pass===row.checks.every(c=>c.passed)
    && row.checks.every(c=>c.required===row.policy[c.name])
    && row.checks.slice(0,4).every(c=>typeof c.observed==="number" && typeof c.required==="number" && c.passed===(c.observed>=c.required))
    && row.checks[1]?.observed===Number(row.checks[2]?.observed)+Number(row.checks[3]?.observed);
});
export const probabilityPolicyPageSchema=z.strictObject({items:z.array(probabilityPolicyResponseSchema).max(50),next_cursor:probabilityPolicyIdSchema.nullable()})
 .refine(page=>new Set(page.items.map(r=>r.id)).size===page.items.length && (page.next_cursor===null || page.next_cursor===page.items.at(-1)?.id));
export type ProbabilityAssessmentResponse=z.infer<typeof probabilityAssessmentResponseSchema>;
export type ProbabilityPolicyResponse=z.infer<typeof probabilityPolicyResponseSchema>;
export type ProbabilityPolicyPage=z.infer<typeof probabilityPolicyPageSchema>;
