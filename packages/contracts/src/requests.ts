import { z } from "zod";

export const resourceIdSchema = z.uuid();
// Normalize request spelling only; immutable financial DTOs retain canonical literals.
export const stockSymbolSchema = z.string().max(32)
  .regex(/^[ \t\r\n]*[Nn][Vv][Dd][Aa][ \t\r\n]*$/)
  .transform(() => "NVDA" as const);
export const idempotencyKeySchema = z.string().min(1).max(128).regex(/^[A-Za-z0-9._:-]+$/);
export const emptyRequestSchema = z.strictObject({});
export const loginRequestSchema = z.strictObject({
  email: z.email().max(254).transform(value => value.toLowerCase()),
  password: z.string().min(1).max(256),
});

// HTTP query values are strings. Reject arrays, numeric coercion and unknown fields.
export const pageQuerySchema = z.strictObject({
  cursor: resourceIdSchema.optional(),
  limit: z.string().regex(/^(?:[1-9]|[1-9][0-9]|100)$/).transform(Number).optional(),
});
export const predictionQuerySchema = pageQuerySchema.extend({
  status: z.enum(["FROZEN", "SETTLED", "UNSCORABLE", "WAITING_DATA", "SETTLEMENT_FAILED"]).optional(),
});
// Message sequence IDs stay strings to preserve bigint precision.
export const messageQuerySchema = z.strictObject({
  cursor: z.string().regex(/^\d{1,18}$/).default("0"),
});
export const eventQuerySchema = z.strictObject({cursor: resourceIdSchema.optional()});

export const experimentalModelIdSchema = z.string().regex(/^lightgbm-experimental-[a-f0-9]{64}$/);
export const experimentalModelQuerySchema = z.strictObject({
  cursor: experimentalModelIdSchema.optional(),
  limit: z.string().regex(/^(?:[1-9]|[1-4][0-9]|50)$/).transform(Number).optional(),
  horizon_sessions: z.enum(["1", "3", "5", "10", "20"]).optional(),
});
export const experimentalEvaluationIdSchema = z.string().regex(/^model-evaluation-experimental-[a-f0-9]{64}$/);
export const experimentalEvaluationQuerySchema = z.strictObject({
  cursor: experimentalEvaluationIdSchema.optional(),
  limit: z.string().regex(/^(?:[1-9]|[1-4][0-9]|50)$/).transform(Number).optional(),
});

export const experimentalComparisonRequestSchema = z.strictObject({
  evaluation_ids: z.array(experimentalEvaluationIdSchema).min(2).max(10),
}).refine(input => new Set(input.evaluation_ids).size === input.evaluation_ids.length,
  {message: "Select distinct registered reports"});

export const modelProposalKeySchema = z.uuid().transform(value => value.toLowerCase());
export const modelProposalRequestSchema = z.strictObject({
  candidate_evaluation_id: experimentalEvaluationIdSchema,
  reference_evaluation_id: experimentalEvaluationIdSchema.nullable().default(null),
  rationale: z.string().min(1).regex(/\S/u).refine(value => [...value].length <= 2000,
    {message: "Rationale must contain at most 2000 characters"}),
});

export const modelProposalQuerySchema = z.strictObject({
  cursor: modelProposalKeySchema.optional(),
  limit: z.string().regex(/^(?:[1-9]|[1-4][0-9]|50)$/).transform(Number).optional(),
});

export const qualityPolicyIdSchema = z.string().regex(/^historical-quality-policy-[a-f0-9]{64}$/);
export const qualityAssessmentIdSchema = z.string().regex(/^historical-quality-assessment-[a-f0-9]{64}$/);
export const qualityAssessmentRequestSchema = z.strictObject({
  policy_id: qualityPolicyIdSchema,
  candidate_evaluation_id: experimentalEvaluationIdSchema,
  reference_evaluation_id: experimentalEvaluationIdSchema.nullable().default(null),
});
export const qualityPolicyQuerySchema = z.strictObject({
  cursor: qualityPolicyIdSchema.optional(),
  limit: z.string().regex(/^(?:[1-9]|[1-4][0-9]|50)$/).transform(Number).optional(),
  horizon_sessions: z.enum(["1","3","5","10","20"]).optional(),
});
export const modelReviewRequestSchema = z.strictObject({
  proposal_id: modelProposalKeySchema,
  assessment_id: qualityAssessmentIdSchema,
  action: z.enum(["APPROVE","REJECT"]),
  reason: z.string().min(1).regex(/\S/u).refine(value=>[...value].length<=2000),
  expected_previous_review_id: modelProposalKeySchema.nullable().default(null),
});

export const experimentalCalibrationIdSchema=z.string().regex(/^calibration-experimental-[a-f0-9]{64}$/);

export const probabilityPolicyIdSchema=z.string().regex(/^probability-quality-policy-[a-f0-9]{64}$/);
export const probabilityAssessmentIdSchema=z.string().regex(/^probability-quality-assessment-[a-f0-9]{64}$/);
export const probabilityAssessmentRequestSchema=z.strictObject({policy_id:probabilityPolicyIdSchema,calibration_id:experimentalCalibrationIdSchema});
export const probabilityPolicyQuerySchema=z.strictObject({cursor:probabilityPolicyIdSchema.optional(),
  limit:z.string().regex(/^(?:[1-9]|[1-4][0-9]|50)$/).transform(Number).optional(),horizon_sessions:z.enum(["1","3","5","10","20"]).optional()});

export const qualityGateIdSchema=z.string().regex(/^model-quality-gate-[a-f0-9]{64}$/);
export const qualityGateRequestSchema=z.strictObject({proposal_id:modelProposalKeySchema,price_assessment_id:qualityAssessmentIdSchema,probability_assessment_id:probabilityAssessmentIdSchema});

export const combinedModelReviewRequestSchema=modelReviewRequestSchema.omit({assessment_id:true}).extend({quality_gate_id:qualityGateIdSchema});
export const modelReviewSubmissionSchema=z.union([modelReviewRequestSchema,combinedModelReviewRequestSchema]);
