import { z } from "zod";
export * from "./requests.js";
import { modelProposalRequestSchema, experimentalModelIdSchema, experimentalEvaluationIdSchema } from "./requests.js";

export const dataModeSchema = z.enum(["LIVE_RESEARCH", "REPLAY", "DEMO"]);
export const decimalSchema = z.string().regex(/^-?\d+(?:\.\d+)?$/);
export const sha256Schema = z.string().regex(/^[a-f0-9]{64}$/);
export const instantSchema = z.iso.datetime({ offset: true });
export const sessionDateSchema = z.iso.date();
const freshnessBase = {
  data_mode: dataModeSchema, cache_key: z.string().min(1).max(1000),
  ttl_seconds: z.number().positive().max(3600),
};
const knownMarketSource = {source_id: z.string().min(1), data_snapshot_hash: sha256Schema, as_of: instantSchema};
export const marketFreshnessSchema = z.discriminatedUnion("status", [
  z.strictObject({...freshnessBase, ...knownMarketSource, status: z.literal("FRESH"), reason: z.null()}),
  z.strictObject({...freshnessBase, ...knownMarketSource, status: z.literal("STALE"), reason: z.enum(["REFRESH_FAILED", "SESSION_OUTDATED"])}),
  z.strictObject({...freshnessBase, status: z.literal("UNAVAILABLE"), reason: z.literal("REFRESH_FAILED"),
    source_id: z.null(), data_snapshot_hash: z.null(), as_of: z.null()}),
]);
// Wire validation only; Alpha owns OHLC arithmetic and canonical snapshot hashing.
export const marketEvidenceSchema = z.strictObject({
  data_snapshot_hash: sha256Schema,
  snapshot: z.strictObject({
    symbol: z.literal("NVDA"), provider: z.string().min(1).max(100), source_id: z.string().min(1),
    data_mode: dataModeSchema, adjustment: z.literal("RAW"), as_of: instantSchema,
    actions_verified_through: sessionDateSchema,
    bars: z.array(z.strictObject({
      session: sessionDateSchema, open: decimalSchema, high: decimalSchema,
      low: decimalSchema, close: decimalSchema, volume: z.number().int().nonnegative(),
      available_at: instantSchema, retrieved_at: instantSchema, revision: z.string().min(1).max(100),
    })).min(1).max(10000),
    splits: z.array(z.strictObject({effective_session: sessionDateSchema,
      new_shares_per_old: decimalSchema, available_at: instantSchema, source_id: z.string().min(1)})),
  }),
});
export const directionSchema = z.enum(["UP", "DOWN", "FLAT"]);

export const errorSchema = z.strictObject({
  error: z.strictObject({
    code: z.string(), message: z.string(), request_id: z.string(), retryable: z.boolean(),
  }),
});

export const forecastSchema = z.strictObject({
  symbol: z.literal("NVDA"),
  horizon_sessions: z.literal(5),
  data_mode: dataModeSchema,
  as_of: instantSchema,
  expires_at: instantSchema,
  base_session: sessionDateSchema,
  base_close: decimalSchema,
  base_revision: z.string(),
  target_session: sessionDateSchema,
  target_close_at: instantSchema,
  expected_return: decimalSchema,
  direction: directionSchema,
  probability_up: z.null(),
  model_kind: z.literal("BASELINE"),
  model_version: z.literal("momentum20-v1"),
  model_sha256: sha256Schema,
  data_snapshot_hash: sha256Schema,
  feature_schema_version: z.literal("daily-price-v1"),
  protocol_version: z.literal("eod-price-v1"),
  return_basis: z.literal("split-only-price-return"),
  direction_rule: z.literal("sign-zero-v1"),
  calendar_version: z.string(),
  git_sha: z.string().regex(/^[a-f0-9]{40}$/),
});

export const freezeRequestSchema = z.strictObject({
  draft_id: z.uuid(), confirmed: z.literal(true),
});

export const createSessionSchema = z.strictObject({
  profile_id: z.literal("alpha-orchestrator").default("alpha-orchestrator"),
});

export const sendMessageSchema = z.strictObject({
  text: z.string().trim().min(1).max(8000), request_id: z.uuid(),
});

// No model-generated JavaScript, HTML, SQL or external URL is executable by this contract.
export const canvasCommandSchema = z.discriminatedUnion("type", [
  z.strictObject({
    command_id: z.uuid(), session_id: z.uuid(), run_id: z.uuid(),
    version: z.literal(1), type: z.literal("chart.show"),
    payload: z.strictObject({
      symbol: z.literal("NVDA"), timeframe: z.literal("1D"),
      indicators: z.array(z.enum(["SMA20", "BOLL", "RSI14", "MACD"])).max(4),
      as_of: instantSchema, data_mode: dataModeSchema,
    }),
  }),
  z.strictObject({
    command_id: z.uuid(), session_id: z.uuid(), run_id: z.uuid(),
    version: z.literal(1), type: z.literal("canvas.reset"), payload: z.strictObject({}),
  }),
]);

// Model-written prose and references only. Financial facts remain in Alpha's draft.
export const researchEvidenceSchema = z.strictObject({
  tool_call_id: z.string().min(1).max(128),
  tool: z.enum(["market_snapshot", "indicators"]),
  source_id: z.string().min(1).max(1000),
  data_snapshot_hash: sha256Schema,
  available_at: instantSchema.max(64),
});
const reportFields = {
  schema_version: z.literal("research-report-v1"),
  profile_id: z.literal("alpha-orchestrator"),
  symbol: z.literal("NVDA"),
  data_mode: dataModeSchema,
  as_of: instantSchema.max(64),
  summary: z.string().trim().min(1).max(4000),
  risks: z.array(z.string().trim().min(1).max(1000)).min(1).max(12),
};
export const researchReportSchema = z.discriminatedUnion("status", [
  z.strictObject({
    ...reportFields,
    status: z.literal("DRAFT_READY"),
    evidence: z.array(researchEvidenceSchema).min(2).max(16),
    draft_id: z.uuid(),
  }),
  z.strictObject({
    ...reportFields,
    status: z.literal("UNAVAILABLE"),
    evidence: z.array(researchEvidenceSchema).max(16),
    draft_id: z.null(),
    reason_code: z.enum(["DATA_UNAVAILABLE", "DATA_INVALID", "DRAFT_UNAVAILABLE"]),
  }),
]);
export type ResearchReport = z.infer<typeof researchReportSchema>;
export type ResearchEvidence = z.infer<typeof researchEvidenceSchema>;

const eventBase = {
  event_id: z.uuid(), schema_version: z.literal(1), occurred_at: instantSchema,
  session_id: z.uuid(), run_id: z.uuid(), profile_id: z.literal("alpha-orchestrator"),
  sequence: z.number().int().positive(),
};

export const agentEventSchema = z.discriminatedUnion("type", [
  z.strictObject({ ...eventBase, type: z.literal("run.started"), payload: z.strictObject({}) }),
  z.strictObject({ ...eventBase, type: z.literal("message.delta"), payload: z.strictObject({ text: z.string() }) }),
  z.strictObject({ ...eventBase, type: z.literal("message.completed"), payload: z.strictObject({ text: z.string(), report: researchReportSchema.optional() }) }),
  z.strictObject({ ...eventBase, type: z.literal("tool.started"), payload: z.strictObject({ tool: z.string(), tool_call_id: z.string() }) }),
  z.strictObject({ ...eventBase, type: z.literal("tool.completed"), payload: z.strictObject({ tool: z.string(), tool_call_id: z.string(), success: z.boolean() }) }),
  z.strictObject({ ...eventBase, type: z.literal("canvas.command"), payload: canvasCommandSchema }),
  z.strictObject({ ...eventBase, type: z.literal("prediction.draft"), payload: z.strictObject({ draft_id: z.uuid(), forecast: forecastSchema }) }),
  z.strictObject({ ...eventBase, type: z.literal("run.completed"), payload: z.strictObject({}) }),
  z.strictObject({ ...eventBase, type: z.literal("run.canceled"), payload: z.strictObject({}) }),
  z.strictObject({ ...eventBase, type: z.literal("run.failed"), payload: errorSchema.shape.error }),
  z.strictObject({ ...eventBase, type: z.literal("run.execution_unknown"), payload: errorSchema.shape.error }),
]);

export type Forecast = z.infer<typeof forecastSchema>;
export type DataMode = z.infer<typeof dataModeSchema>;
export type CanvasCommand = z.infer<typeof canvasCommandSchema>;
export type AgentEvent = z.infer<typeof agentEventSchema>;



// Public callers cannot choose an owner, data mode or arbitrary prediction subset.
export const evaluationRequestSchema = z.strictObject({
  base_from: sessionDateSchema,
  base_to: sessionDateSchema,
  cutoff: instantSchema,
}).refine(value => value.base_from <= value.base_to, {
  message: "base_from must not exceed base_to", path: ["base_to"],
});


export const chartDataSchema = z.strictObject({
  symbol: z.literal("NVDA"), provider: z.string(), as_of: instantSchema,
  data_mode: dataModeSchema, adjustment: z.literal("SPLIT_ONLY_AS_OF"),
  schema_version: z.literal("indicators-v3"), share_basis_session: sessionDateSchema,
  data_snapshot_hash: sha256Schema, definitions: z.record(z.string(), z.string()),
  warmup_policy: z.string(), sessions: z.array(sessionDateSchema).min(1),
  bars: z.array(z.strictObject({session: sessionDateSchema, open: decimalSchema,
    high: decimalSchema, low: decimalSchema, close: decimalSchema,
    volume: decimalSchema, split_factor: decimalSchema})).min(1),
  series: z.record(z.string(), z.array(decimalSchema.nullable())),
}).superRefine((value, ctx) => {
  if (value.bars.length !== value.sessions.length || value.share_basis_session !== value.sessions.at(-1)
      || value.bars.some((bar, i) => bar.session !== value.sessions[i])
      || Object.values(value.series).some(series => series.length !== value.sessions.length)
      || ["SMA20", "BOLL_UPPER", "BOLL_MIDDLE", "BOLL_LOWER", "RSI14", "MACD", "MACD_SIGNAL"].some(name => !value.series[name])) {
    ctx.addIssue({code: "custom", message: "Chart bars and indicator sessions must agree"});
  }
});
export type ChartData = z.infer<typeof chartDataSchema>;

// Saved v1 reports may contain Decimal's exponent notation (e.g. 0E-12).
// Keep the original string; Number is only a display/range guard, never scoring arithmetic.
const evaluationMetricSchema = z.string().regex(/^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/)
  .refine(value => Number.isFinite(Number(value) * 100), "Metric cannot be displayed safely");
const countSchema = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const evaluationCountsSchema = z.strictObject({
  SETTLED: countSchema, UNSCORABLE: countSchema, WAITING_TARGET: countSchema,
  WAITING_DATA: countSchema, SETTLEMENT_FAILED: countSchema,
});
const evaluationFields = {
  report_id: z.uuid(), content_hash: sha256Schema, data_mode: dataModeSchema,
  base_from: sessionDateSchema, base_to: sessionDateSchema, cutoff: instantSchema,
  evaluation_version: z.enum(["price-evaluation-v1", "price-evaluation-v2"]),
  protocol_version: z.literal("eod-price-v1"), model_version: z.literal("momentum20-v1"),
  git_sha: z.string().regex(/^[a-f0-9]{40}$/),
  return_basis: z.literal("split-only-price-return"),
  cohort_policy: z.literal("all-owner-forecasts-in-base-range"),
  sample_count: countSchema, scored_count: countSchema, correction_count: countSchema.optional(),
  counts: evaluationCountsSchema,
  direction_accuracy: evaluationMetricSchema.refine(value => Number(value) <= 1).nullable(),
  mae: evaluationMetricSchema.nullable(), zero_return_mae: evaluationMetricSchema.nullable(),
};
type EvaluationStatistics = z.infer<z.ZodObject<typeof evaluationFields>>;
function checkEvaluationStatistics(value: EvaluationStatistics, ctx: z.RefinementCtx) {
  const metrics = [value.direction_accuracy, value.mae, value.zero_return_mae];
  if (value.base_from > value.base_to
    || Object.values(value.counts).reduce((sum, count) => sum + count, 0) !== value.sample_count
    || value.scored_count !== value.counts.SETTLED
    || metrics.some(metric => (metric === null) !== (value.scored_count === 0))
    || (value.evaluation_version === "price-evaluation-v2" && value.correction_count === undefined)
    || (value.correction_count !== undefined && value.correction_count > value.sample_count)) {
    ctx.addIssue({code: "custom", message: "Evaluation statistics are inconsistent"});
  }
}
export const evaluationSummarySchema = z.strictObject({
  ...evaluationFields, created_at: instantSchema,
}).superRefine(checkEvaluationStatistics);
const evaluationManifestRowSchema = z.strictObject({
  prediction_id: z.uuid(), base_session: sessionDateSchema, forecast_hash: sha256Schema,
  supersedes_prediction_id: z.uuid().nullable().optional(),
  status: z.enum(["SETTLED", "UNSCORABLE", "WAITING_TARGET", "WAITING_DATA", "SETTLEMENT_FAILED"]),
  outcome_id: z.uuid().nullable(), settlement_version: z.number().int().positive().nullable(),
  // Outcome payloads are immutable versioned facts. This view uses their IDs/status only.
  result: z.record(z.string(), z.unknown()).nullable(),
});
export const evaluationReportSchema = z.strictObject({
  ...evaluationFields,
  base_session_counts: z.record(sessionDateSchema, countSchema),
  manifest: z.array(evaluationManifestRowSchema),
}).superRefine((value, ctx) => {
  checkEvaluationStatistics(value, ctx);
  const statuses = {SETTLED: 0, UNSCORABLE: 0, WAITING_TARGET: 0, WAITING_DATA: 0, SETTLEMENT_FAILED: 0};
  const sessions: Record<string, number> = {};
  const ids = new Set(value.manifest.map(row => row.prediction_id));
  let invalid = ids.size !== value.sample_count || value.manifest.length !== value.sample_count;
  for (const row of value.manifest) {
    statuses[row.status]++;
    sessions[row.base_session] = (sessions[row.base_session] ?? 0) + 1;
    const hasOutcome = row.status === "SETTLED" || row.status === "UNSCORABLE";
    invalid ||= row.base_session < value.base_from || row.base_session > value.base_to
      || (row.outcome_id !== null) !== hasOutcome || (row.settlement_version !== null) !== hasOutcome
      || (row.result !== null) !== hasOutcome
      || (value.evaluation_version === "price-evaluation-v2" && row.supersedes_prediction_id === undefined)
      || (row.supersedes_prediction_id != null
        && (row.supersedes_prediction_id === row.prediction_id || !ids.has(row.supersedes_prediction_id)));
  }
  invalid ||= Object.entries(statuses).some(([name, count]) => value.counts[name as keyof typeof statuses] !== count)
    || Object.keys(sessions).length !== Object.keys(value.base_session_counts).length
    || Object.entries(sessions).some(([day, count]) => value.base_session_counts[day] !== count)
    || (value.correction_count !== undefined
      && value.manifest.filter(row => row.supersedes_prediction_id != null).length !== value.correction_count);
  if (invalid) ctx.addIssue({code: "custom", message: "Evaluation manifest and totals must agree"});
});
export const evaluationPageSchema = z.strictObject({
  items: z.array(evaluationSummarySchema).max(100), next_cursor: z.uuid().nullable(),
}).superRefine((value, ctx) => {
  if (new Set(value.items.map(row => row.report_id)).size !== value.items.length
    || (value.next_cursor !== null && value.next_cursor !== value.items.at(-1)?.report_id)) {
    ctx.addIssue({code: "custom", message: "Evaluation page IDs and cursor must agree"});
  }
});
export type EvaluationReport = z.infer<typeof evaluationReportSchema>;
export type EvaluationSummary = z.infer<typeof evaluationSummarySchema>;


export const experimentalModelSummarySchema = z.strictObject({
  id: experimentalModelIdSchema,
  status: z.literal("EXPERIMENTAL"), qualification: z.literal("UNVERIFIED"),
  data_mode: dataModeSchema, horizon_sessions: z.union([z.literal(1), z.literal(3), z.literal(5), z.literal(10), z.literal(20)]),
  registered_at: instantSchema, training_cutoff: instantSchema,
  model_sha256: sha256Schema, registration_sha256: sha256Schema,
  training_samples: z.number().int().positive(), reserved_window_samples: z.number().int().positive(),
  reserved_window_mae: z.string().regex(/^[0-9]+(?:\.[0-9]+)?$/),
  reserved_window_zero_return_mae: z.string().regex(/^[0-9]+(?:\.[0-9]+)?$/),
  metric_scope: z.literal("RESERVED_WINDOW_NOT_QUALIFIED_FINAL_EVALUATION"),
}).refine(row => row.id === `lightgbm-experimental-${row.registration_sha256}`, {message: "Model identity must match registration hash"});
export const experimentalModelPageSchema = z.strictObject({
  items: z.array(experimentalModelSummarySchema).max(50),
  next_cursor: experimentalModelIdSchema.nullable(),
}).refine(page => new Set(page.items.map(row => row.id)).size === page.items.length
  && (page.next_cursor === null || page.next_cursor === page.items.at(-1)?.id),
  {message: "Model page identities and cursor must agree"});
export type ExperimentalModelSummary = z.infer<typeof experimentalModelSummarySchema>;

const experimentalPriceMetricsSchema = z.strictObject({
  sample_count: z.number().int().nonnegative().safe(),
  mae: z.string().regex(/^[0-9]+(?:\.[0-9]+)?$/).nullable(),
  direction_accuracy: z.string().regex(/^(?:0(?:\.[0-9]+)?|1(?:\.0+)?)$/).nullable(),
});
export const experimentalPriceReportSchema = z.strictObject({
  schema_version: z.literal("experimental-price-evaluation-v1"),
  status: z.literal("EXPERIMENTAL"), qualification: z.literal("UNVERIFIED"),
  scope: z.literal("POST_CALIBRATION_HISTORICAL_EVALUATION"),
  model_id: experimentalModelIdSchema, model_sha256: sha256Schema,
  calibration_id: z.string().regex(/^calibration-experimental-[a-f0-9]{64}$/),
  calibration_content_hash: sha256Schema,
  protocol_version: z.literal("eod-price-v1"), return_basis: z.literal("split-only-price-return"),
  cohort: z.strictObject({
    label_ids: z.array(sha256Schema).min(1), cutoff: instantSchema,
    data_mode: dataModeSchema,
    horizon_sessions: z.union([z.literal(1), z.literal(3), z.literal(5), z.literal(10), z.literal(20)]),
    calendar_version: z.string().min(1),
  }),
  cohort_hash: sha256Schema,
  metrics: z.strictObject({model: experimentalPriceMetricsSchema,
    "zero-return-v1": experimentalPriceMetricsSchema,
    "momentum20-v1": experimentalPriceMetricsSchema.nullable()}),
  momentum_unavailable_reason: z.literal("FIVE_SESSION_ONLY").nullable(),
  manifest: z.array(z.strictObject({
    label_id: sha256Schema, base_session: sessionDateSchema, target_session: sessionDateSchema,
    feature_as_of: instantSchema, label_available_at: instantSchema,
    base_snapshot_hash: sha256Schema, observation_snapshot_hash: sha256Schema,
    actual_return: decimalSchema, actual_direction: directionSchema,
    model_expected_return: decimalSchema, momentum_expected_return: decimalSchema.nullable(),
    zero_expected_return: z.literal("0.000000000000"),
  })).min(1),
  code_sha256: z.strictObject({price_evaluation: sha256Schema, forecast: sha256Schema,
    training: sha256Schema, calibration: sha256Schema}),
  content_hash: sha256Schema,
}).superRefine((report, ctx) => {
  const n = report.manifest.length, five = report.cohort.horizon_sessions === 5;
  let invalid = report.calibration_id !== `calibration-experimental-${report.calibration_content_hash}`
    || report.cohort.label_ids.length !== n || new Set(report.cohort.label_ids).size !== n
    || new Set(report.manifest.map(row => row.base_session)).size !== n
    || (report.metrics["momentum20-v1"] !== null) !== five
    || report.momentum_unavailable_reason !== (five ? null : "FIVE_SESSION_ONLY");
  for (const metric of Object.values(report.metrics)) {
    if (metric) invalid ||= metric.sample_count !== n || metric.mae === null || metric.direction_accuracy === null;
  }
  report.manifest.forEach((row, index) => {
    invalid ||= row.label_id !== report.cohort.label_ids[index]
      || row.target_session <= row.base_session || (row.momentum_expected_return !== null) !== five
      || (index > 0 && row.base_session <= report.manifest[index - 1]!.base_session);
  });
  if (invalid) ctx.addIssue({code: "custom", message: "Experimental report cohort and metrics must agree"});
});
export type ExperimentalPriceReport = z.infer<typeof experimentalPriceReportSchema>;

export const experimentalEvaluationSummarySchema = z.strictObject({
  id: experimentalEvaluationIdSchema, model_id: experimentalModelIdSchema,
  content_hash: sha256Schema, cohort_hash: sha256Schema, data_mode: dataModeSchema,
  horizon_sessions: z.union([z.literal(1), z.literal(3), z.literal(5), z.literal(10), z.literal(20)]),
  registered_at: instantSchema, cutoff: instantSchema,
  sample_count: z.number().int().positive().safe(), qualification: z.literal("UNVERIFIED"),
}).refine(row => row.id === "model-evaluation-experimental-" + row.content_hash,
  {message: "Report identity must match its content hash"});
export const experimentalEvaluationPageSchema = z.strictObject({
  items: z.array(experimentalEvaluationSummarySchema).max(50), next_cursor: experimentalEvaluationIdSchema.nullable(),
}).refine(page => new Set(page.items.map(row => row.id)).size === page.items.length
  && (page.next_cursor === null || page.next_cursor === page.items.at(-1)?.id),
  {message: "Report page identities and cursor must agree"});
export type ExperimentalEvaluationPage = z.infer<typeof experimentalEvaluationPageSchema>;

export const experimentalReportComparisonSchema = z.strictObject({
  schema_version: z.literal("experimental-report-comparison-v1"),
  status: z.literal("EXPERIMENTAL"), qualification: z.literal("UNVERIFIED"),
  evaluation_ids: z.array(experimentalEvaluationIdSchema).min(2).max(10),
  cohort_hash: sha256Schema,
  reports: z.array(experimentalPriceReportSchema).min(2).max(10),
}).refine(value => new Set(value.evaluation_ids).size === value.evaluation_ids.length
  && value.evaluation_ids.length === value.reports.length
  && value.reports.every((report, i) => value.evaluation_ids[i] === "model-evaluation-experimental-" + report.content_hash
    && report.cohort_hash === value.cohort_hash), {message: "Comparison identities and cohort must agree"});
export type ExperimentalReportComparison = z.infer<typeof experimentalReportComparisonSchema>;

export const modelProposalPayloadSchema = modelProposalRequestSchema.extend({
  reference_evaluation_id: experimentalEvaluationIdSchema.nullable(),
  schema_version: z.literal("model-proposal-v1"), status: z.literal("SUBMITTED"), qualification: z.literal("UNVERIFIED"),
  owner_id: z.uuid(), request_id: z.uuid(), data_mode: dataModeSchema,
  candidate_model_id: experimentalModelIdSchema, candidate_report_hash: sha256Schema,
  reference_model_id: experimentalModelIdSchema.nullable(), reference_report_hash: sha256Schema.nullable(),
  cohort_hash: sha256Schema,
}).refine(p => p.candidate_evaluation_id === "model-evaluation-experimental-" + p.candidate_report_hash
  && (p.reference_evaluation_id === null ? p.reference_model_id === null && p.reference_report_hash === null
    : p.reference_evaluation_id === "model-evaluation-experimental-" + p.reference_report_hash
      && p.reference_model_id !== null && p.reference_model_id !== p.candidate_model_id),
  {message: "Proposal evidence identities must agree"});
export const modelProposalResponseSchema = z.strictObject({
  id: z.uuid(), created_at: instantSchema, content_hash: sha256Schema, proposal: modelProposalPayloadSchema,
});
export type ModelProposalResponse = z.infer<typeof modelProposalResponseSchema>;

export const modelProposalPageSchema = z.strictObject({
  items: z.array(modelProposalResponseSchema).max(50), next_cursor: z.uuid().nullable(),
}).refine(page => new Set(page.items.map(row => row.id)).size === page.items.length
  && (page.next_cursor === null || page.next_cursor === page.items.at(-1)?.id),
  {message: "Proposal page identities and cursor must agree"});
export type ModelProposalPage = z.infer<typeof modelProposalPageSchema>;
export * from "./quality.js";
export * from "./review.js";

export * from "./calibration.js";

export * from "./probability-quality.js";

export * from "./quality-gate.js";

export * from "./backtest.js";

export * from "./training-evidence.js";
