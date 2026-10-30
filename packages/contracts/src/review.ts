import {z} from "zod";
import {modelReviewRequestSchema,combinedModelReviewRequestSchema,qualityAssessmentIdSchema,probabilityAssessmentIdSchema,experimentalModelIdSchema} from "./requests.js";
const hash=z.string().regex(/^[a-f0-9]{64}$/);
const failedCheck=z.enum(["minimum_samples","maximum_mae","minimum_direction_accuracy","required_reference",
  "mae_improvement:zero-return-v1","mae_improvement:momentum20-v1","mae_improvement:reference"]);
export const modelReviewRecordSchema=modelReviewRequestSchema.omit({expected_previous_review_id:true}).extend({
  schema_version:z.literal("model-review-record-v1"),reviewer_id:z.uuid(),request_id:z.uuid(),
  data_mode:z.enum(["REPLAY","DEMO","LIVE_RESEARCH"]),proposal_content_hash:hash,assessment_content_hash:hash,
  policy_hash:hash,candidate_model_id:experimentalModelIdSchema,disposition:z.enum(["BLOCKED","REJECTED"]),
  blocking_reasons:z.array(z.enum(["HISTORICAL_EVIDENCE_UNQUALIFIED","NUMERIC_GATE_FAILED"])).max(2),
  failed_checks:z.array(failedCheck).max(7),promotion_allowed:z.literal(false),
  previous_review_id:z.uuid().nullable(),revision:z.number().int().positive().safe(),content_hash:hash,
}).refine(row=>{
  const disposition=row.action==="REJECT" ? "REJECTED" : "BLOCKED";
  const blockers=disposition==="REJECTED" ? [] : ["HISTORICAL_EVIDENCE_UNQUALIFIED",...(row.failed_checks.length ? ["NUMERIC_GATE_FAILED"] : [])];
  return row.disposition===disposition && JSON.stringify(row.blocking_reasons)===JSON.stringify(blockers)
    && new Set(row.failed_checks).size===row.failed_checks.length
    && (row.revision===1)===(row.previous_review_id===null)
    && row.assessment_id==="historical-quality-assessment-"+row.assessment_content_hash;
});
export const combinedModelReviewRecordSchema=combinedModelReviewRequestSchema.omit({expected_previous_review_id:true}).extend({
  schema_version:z.literal("model-review-record-v2"),reviewer_id:z.uuid(),request_id:z.uuid(),
  data_mode:z.enum(["REPLAY","DEMO","LIVE_RESEARCH"]),proposal_content_hash:hash,quality_gate_content_hash:hash,
  price_assessment_id:qualityAssessmentIdSchema,probability_assessment_id:probabilityAssessmentIdSchema,
  price_policy_hash:hash,probability_policy_hash:hash,candidate_model_id:experimentalModelIdSchema,
  disposition:z.enum(["BLOCKED","REJECTED"]),promotion_allowed:z.literal(false),
  blocking_reasons:z.array(z.enum(["HISTORICAL_EVIDENCE_UNQUALIFIED","PRICE_NUMERIC_GATE_FAILED","PROBABILITY_NUMERIC_GATE_FAILED"])).max(3),
  failed_price_checks:z.array(failedCheck).max(7),
  failed_probability_checks:z.array(z.enum(["minimum_calibration_samples","minimum_evaluation_samples","minimum_evaluation_up","minimum_evaluation_non_up","maximum_brier","maximum_ece"])).max(6),
  previous_review_id:z.uuid().nullable(),revision:z.number().int().positive().safe(),content_hash:hash,
}).refine(row=>{
  const disposition=row.action==="REJECT" ? "REJECTED" : "BLOCKED";
  const blockers=disposition==="REJECTED" ? [] : ["HISTORICAL_EVIDENCE_UNQUALIFIED",
    ...(row.failed_price_checks.length ? ["PRICE_NUMERIC_GATE_FAILED"] : []),
    ...(row.failed_probability_checks.length ? ["PROBABILITY_NUMERIC_GATE_FAILED"] : [])];
  return row.disposition===disposition && JSON.stringify(row.blocking_reasons)===JSON.stringify(blockers)
    && new Set(row.failed_price_checks).size===row.failed_price_checks.length
    && new Set(row.failed_probability_checks).size===row.failed_probability_checks.length
    && (row.revision===1)===(row.previous_review_id===null)
    && row.quality_gate_id==="model-quality-gate-"+row.quality_gate_content_hash;
});
export const modelReviewResponseSchema=z.strictObject({id:z.uuid(),created_at:z.iso.datetime({offset:true}),review:z.discriminatedUnion("schema_version",[modelReviewRecordSchema,combinedModelReviewRecordSchema])});
export type ModelReviewResponse=z.infer<typeof modelReviewResponseSchema>;

export const modelReviewPageSchema=z.strictObject({items:z.array(modelReviewResponseSchema).max(50),next_cursor:z.uuid().nullable()});
export type ModelReviewPage=z.infer<typeof modelReviewPageSchema>;
