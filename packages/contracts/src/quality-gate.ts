import {z} from "zod";
import {qualityGateRequestSchema,experimentalEvaluationIdSchema,experimentalModelIdSchema,experimentalCalibrationIdSchema} from "./requests.js";
const hash=z.string().regex(/^[a-f0-9]{64}$/);
const priceNames=["minimum_samples","maximum_mae","minimum_direction_accuracy","required_reference","mae_improvement:zero-return-v1","mae_improvement:momentum20-v1","mae_improvement:reference"] as const;
const probabilityNames=["minimum_calibration_samples","minimum_evaluation_samples","minimum_evaluation_up","minimum_evaluation_non_up","maximum_brier","maximum_ece"] as const;
export const qualityGateResponseSchema=qualityGateRequestSchema.extend({
  schema_version:z.literal("model-quality-gate-v1"),data_mode:z.enum(["REPLAY","DEMO","LIVE_RESEARCH"]),
  proposal_content_hash:hash,candidate_evaluation_id:experimentalEvaluationIdSchema,candidate_model_id:experimentalModelIdSchema,
  reference_evaluation_id:experimentalEvaluationIdSchema.nullable(),cohort_hash:hash,
  calibration_id:experimentalCalibrationIdSchema,calibration_content_hash:hash,probability_report_hash:hash,
  calibration_cohort_hash:hash,evaluation_cohort_hash:hash,price_policy_hash:hash,probability_policy_hash:hash,
  price_numeric_pass:z.boolean(),probability_numeric_pass:z.boolean(),numeric_pass:z.boolean(),
  failed_price_checks:z.array(z.enum(priceNames)).max(7),failed_probability_checks:z.array(z.enum(probabilityNames)).max(6),
  qualification:z.literal("UNVERIFIED"),promotion_allowed:z.literal(false),
  promotion_blockers:z.array(z.enum(["HISTORICAL_EVIDENCE_UNQUALIFIED","HUMAN_APPROVAL_REQUIRED","PRICE_NUMERIC_GATE_FAILED","PROBABILITY_NUMERIC_GATE_FAILED"])).min(2).max(4),content_hash:hash,
}).refine(row=>{
  const blockers=["HISTORICAL_EVIDENCE_UNQUALIFIED","HUMAN_APPROVAL_REQUIRED",...(!row.price_numeric_pass ? ["PRICE_NUMERIC_GATE_FAILED"] : []),...(!row.probability_numeric_pass ? ["PROBABILITY_NUMERIC_GATE_FAILED"] : [])];
  return row.price_numeric_pass===(row.failed_price_checks.length===0)
    && row.probability_numeric_pass===(row.failed_probability_checks.length===0)
    && row.numeric_pass===(row.price_numeric_pass && row.probability_numeric_pass)
    && JSON.stringify(row.promotion_blockers)===JSON.stringify(blockers)
    && JSON.stringify(row.failed_price_checks)===JSON.stringify(priceNames.filter(n=>row.failed_price_checks.includes(n)))
    && JSON.stringify(row.failed_probability_checks)===JSON.stringify(probabilityNames.filter(n=>row.failed_probability_checks.includes(n)))
    && (row.reference_evaluation_id!==null || !row.failed_price_checks.includes("mae_improvement:reference"));
});
export type QualityGateResponse=z.infer<typeof qualityGateResponseSchema>;
