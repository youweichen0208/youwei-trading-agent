import { readFileSync } from "node:fs";
import { z } from "zod";
import * as contracts from "../dist/index.js";

const ref = name => ({$ref: `#/components/schemas/${name}`});
const object = (properties, required = Object.keys(properties)) => ({type: "object", properties, required, additionalProperties: false});
const array = items => ({type: "array", items});
const nullable = schema => ({anyOf: [schema, {type: "null"}]});
const string = {type: "string"};
const uuid = {type: "string", format: "uuid"};
const time = {type: "string", format: "date-time"};
const mode = {type: "string", enum: ["REPLAY", "LIVE_RESEARCH"]};
const sequence = {type: "string", pattern: "^[0-9]+$"};
const page = (item, cursor = uuid) => object({items: array(ref(item)), next_cursor: nullable(cursor)});
const parameter = (name, location, schema, required = true, description) => ({name, in: location, required, schema, ...(description ? {description} : {})});
const json = schema => ({"application/json": {schema}});

export function buildBffOpenApi() {
  const requests = JSON.parse(readFileSync(new URL("../bff-requests.schemas.json", import.meta.url), "utf8"));
  const alpha = JSON.parse(readFileSync(new URL("../alpha.openapi.json", import.meta.url), "utf8"));
  const schemas = {...alpha.components.schemas, ...requests.schemas};
  schemas.MarketSnapshot = alpha.paths["/internal/v1/market/snapshot"].get.responses["200"].content["application/json"].schema;
  for (const name of ["trainingEvidencePageSchema", "trainingEvidenceResponseSchema", "trainingEvidenceIdSchema", "backtestPageSchema", "backtestResponseSchema", "backtestIdSchema", "qualityGateResponseSchema", "probabilityPolicyPageSchema", "probabilityPolicyResponseSchema", "probabilityAssessmentResponseSchema", "calibrationReportResponseSchema", "modelReviewPageSchema", "modelReviewResponseSchema", "qualityPolicyPageSchema", "qualityPolicyResponseSchema", "qualityAssessmentResponseSchema", "modelProposalPageSchema", "modelProposalResponseSchema", "experimentalReportComparisonSchema", "experimentalEvaluationPageSchema", "experimentalPriceReportSchema", "experimentalModelPageSchema", "marketFreshnessSchema", "marketEvidenceSchema", "researchReportSchema", "errorSchema", "forecastSchema", "agentEventSchema", "chartDataSchema", "evaluationReportSchema", "evaluationPageSchema"]) {
    schemas[name] = z.toJSONSchema(contracts[name], {io: "input"});
  }
  schemas.Identity = object({user: object({id: uuid, email: {type: "string", format: "email"}}), csrf_token: {type: "string", pattern: "^[a-f0-9]{64}$"}, data_mode: mode});
  schemas.Me = object({...schemas.Identity.properties, experimental_models_enabled: {type: "boolean"}, can_review_models: {type: "boolean"}, runtime: {type: "string", enum: ["replay-fixture", "unverified", "hermes"]}});
  schemas.Health = object({status: {const: "ok"}, service: {const: "yta-bff"}, data_mode: mode});
  schemas.Readiness = object({status: {enum: ["ready", "not_ready"]}, service: {const: "yta-bff"}, data_mode: mode,
    checks: object({database: {enum: ["ready", "schema_missing", "permissions_invalid", "immutable_guards_missing", "unavailable"]},
      alpha: {enum: ["ready", "not_ready"]}, research: {enum: ["replay", "unverified"]}, admission: {enum: ["OPEN", "PAUSED"]}})});
  schemas.Profiles = object({items: array(object({id: {const: "alpha-orchestrator"}, name: string}))});
  schemas.Session = object({session_id: uuid, profile_id: {const: "alpha-orchestrator"}, data_mode: mode, created_at: time});
  schemas.SessionPage = page("Session");
  schemas.Draft = object({draft_id: uuid, forecast: ref("forecastSchema")});
  schemas.Message = object({id: sequence, run_id: uuid, role: {enum: ["user", "assistant"]}, content: string, partial: {type: "boolean"}, created_at: time});
  schemas.MessagePage = page("Message", sequence);
  schemas.Run = object({run_id: uuid, session_id: uuid, status: {enum: ["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELED", "EXECUTION_UNKNOWN"]},
    data_mode: mode, draft: nullable(ref("Draft")), report: nullable(ref("researchReportSchema")), created_at: time});
  // Historical facts retain version-specific payloads. Do not mislabel all outcomes as the latest schema.
  schemas.Prediction = object({prediction_id: uuid, draft_id: uuid, supersedes_prediction_id: nullable(uuid), created_at: time, forecast: ref("forecastSchema"),
    status: {enum: ["FROZEN", "SETTLED", "UNSCORABLE", "WAITING_DATA", "SETTLEMENT_FAILED"]},
    outcomes: array(object({settlement_version: {type: "integer", minimum: 1}, status: {enum: ["SETTLED", "UNSCORABLE"]}, observed_at: time,
      result: {type: "object", additionalProperties: true, description: "Immutable, version-specific settlement or unscorable fact; inspect calculation/protocol version. This envelope does not fully constrain historical payload variants."}})),
    events: array(object({sequence: {type: "integer", minimum: 1}, type: string, occurred_at: time,
      payload: {type: "object", additionalProperties: true, description: "Immutable event-type-specific audit payload."}}))});
  schemas.PredictionPage = page("Prediction");
  schemas.AcceptedRun = object({run_id: uuid});
  schemas.Ok = object({ok: {const: true}});

  const paths = {};
  function route(method, path, id, response, options = {}) {
    const parameters = [];
    if (path.includes("{id}")) parameters.push(parameter("id", "path", ref(options.idSchema ?? "resourceIdSchema")));
    if (path.includes("{symbol}")) parameters.push(parameter("symbol", "path", ref("stockSymbolSchema")));
    if (options.query) {
      const query = requests.schemas[options.query];
      for (const [name, schema] of Object.entries(query.properties)) parameters.push(parameter(name, "query", schema, false));
    }
    if (method === "post") parameters.push(parameter("Origin", "header", {type: "string", format: "uri"}, true, "Must match the configured public web origin."));
    if (options.key) parameters.push(parameter("Idempotency-Key", "header", ref(options.keySchema ?? "idempotencyKeySchema"), true));
    if (id === "sendMessage") parameters.push(parameter("Idempotency-Key", "header", ref("resourceIdSchema"), false, "If supplied, must equal body.request_id. Reuse that identity after response loss; never resend unknown execution under a new key."));
    if (id === "runEvents") parameters.push(parameter("Last-Event-ID", "header", ref("resourceIdSchema"), false, "Takes precedence over query cursor; both values must be valid UUIDs belonging to this run."));
    const success = {description: options.description ?? "Successful response", headers: {"X-Request-ID": {schema: string}}, content: json(ref(response))};
    if (id === "runEvents") success.content = {"text/event-stream": {schema: string, "x-event-schema": ref("agentEventSchema")}};
    const operation = {operationId: id, summary: options.summary ?? id,
      security: options.public ? [] : method === "post"
        ? [{session: [], csrf: []}, {secureSession: [], csrf: []}]
        : [{session: []}, {secureSession: []}],
      parameters,
      responses: {[options.status ?? 200]: success, default: {description: "Error; error.code refines HTTP status. Includes request_id and retryable. Parser errors: 400, 413, 415; invalid fields: 422; unauthenticated: 401; CSRF/origin: 403; missing or foreign resource: 404; conflict: 409; rate/stream limit: 429; overload/dependency unavailable: 503.", content: json(ref("errorSchema"))}},
    };
    if (options.body) operation.requestBody = {required: options.body !== "emptyRequestSchema", content: json(ref(options.body))};
    if (options.query) operation["x-query-contract"] = ref(options.query);
    if (options.description) operation.description = options.description;
    if (method === "post" && ["/model-review-quality-gates", "/model-quality-gates", "/probability-quality-assessments", "/model-reviews", "/historical-quality-assessments", "/model-proposals", "/sessions", "/sessions/{id}/messages", "/predictions",
      "/predictions/{id}/corrections", "/evaluation-reports", "/replay/predictions/{id}/settle"].includes(path)) {
      operation.responses[503] = {description: "WRITE_ADMISSION_PAUSED: authenticated business-write admission is closed before persistence or upstream dispatch. Also blocks same-key POST retries; query known Run/history and preserve original request identities. Login, logout, reads and cancellation remain available. Existing work and Jobs are not stopped by this control.", content: json(ref("errorSchema"))};
    }
    if (id === "readiness") operation.responses[503] = {...success, description: "Dependencies, write admission or real integration are not ready; this response is a Readiness object, not an error envelope."};
    if (id === "sendMessage") operation.responses[429] = {description: "RESEARCH_QUOTA_EXCEEDED: the authenticated owner's rolling 24-hour admission quota for this data mode is exhausted. No new Run or upstream execution is created. Existing same-key requests still return their original Run; failures, cancellations and unknown executions do not refund quota. This is a request-count limit, not a token or currency budget. Do not automatically retry with a new key.", content: json(ref("errorSchema"))};
    (paths[path] ??= {})[method] = operation;
  }
  route("get", "/health", "health", "Health", {public: true});
  route("get", "/ready", "readiness", "Readiness", {public: true});
  route("post", "/auth/login", "login", "Identity", {public: true, body: "loginRequestSchema", description: "Sets an HttpOnly SameSite=Strict session cookie. Development/test use yta_session; staging/production use Secure __Host-yta_session. Login limits include shared peer budget and process-local password computation capacity."});
  route("post", "/auth/logout", "logout", "Ok", {body: "emptyRequestSchema"});
  route("get", "/me", "me", "Me");
  route("get", "/profiles", "profiles", "Profiles");
  route("get", "/sessions", "sessions", "SessionPage", {query: "pageQuerySchema"});
  route("post", "/sessions", "createSession", "Session", {status: 201, body: "createSessionSchema", key: true});
  route("get", "/sessions/{id}/messages", "messages", "MessagePage", {query: "messageQuerySchema"});
  route("post", "/sessions/{id}/messages", "sendMessage", "AcceptedRun", {status: 202, body: "sendMessageSchema"});
  route("get", "/runs/{id}", "run", "Run");
  route("post", "/runs/{id}/cancel", "cancelRun", "Run", {status: 202, body: "emptyRequestSchema", key: true, description: "Requests cancellation; the returned run may still be running. Poll or consume events for the terminal state; cancellation does not undo existing effects."});
  route("get", "/runs/{id}/events", "runEvents", "agentEventSchema", {query: "eventQuerySchema", description: "SSE id is event_id, event is type, data is one agent event JSON object. Retry hint 1500 ms; comment heartbeats are not events. Reconnect with Last-Event-ID; no automatic resubmission. Terminal runs close the stream; disconnect or expired auth also closes it. A failure after headers closes the stream without a JSON error. Maximum four streams per owner per BFF process."});
  route("get", "/stocks/{symbol}/freshness", "freshness", "marketFreshnessSchema");
  route("get", "/stocks/{symbol}/bars", "bars", "MarketSnapshot");
  route("get", "/stocks/{symbol}/indicators", "indicators", "chartDataSchema");
  route("get", "/prediction-drafts/{id}", "draft", "Draft");
  route("get", "/prediction-drafts/{id}/evidence", "draftEvidence", "marketEvidenceSchema");
  route("post", "/predictions", "freeze", "Prediction", {body: "freezeRequestSchema", key: true, description: "Only authenticated user confirmation may freeze a draft; ownership and financial facts are server-controlled."});
  route("get", "/predictions", "predictions", "PredictionPage", {query: "predictionQuerySchema"});
  route("get", "/predictions/{id}", "prediction", "Prediction");
  route("post", "/predictions/{id}/corrections", "correctPrediction", "Prediction", {body: "freezeRequestSchema", key: true});
  route("post", "/replay/predictions/{id}/settle", "settleReplay", "Prediction", {body: "emptyRequestSchema", key: true, description: "REPLAY development/test only. Returns 404 when disabled. Header key is validated; settlement deduplication is based on Alpha's prediction/input/version identity."});
  route("get", "/experimental-models/{id}/evaluations", "experimentalEvaluations", "experimentalEvaluationPageSchema", {idSchema: "experimentalModelIdSchema", query: "experimentalEvaluationQuerySchema"});
  route("post", "/model-reviews", "submitModelReview", "modelReviewResponseSchema", {body:"modelReviewSubmissionSchema",key:true,keySchema:"modelProposalKeySchema",description:"Server-configured model reviewer only. Exact UUID/body retries preserve the original blocked or rejected decision."});
  route("get", "/experimental-models/{id}/training-evidence", "trainingEvidenceList", "trainingEvidencePageSchema", {idSchema:"experimentalModelIdSchema",query:"trainingEvidenceQuerySchema",description:"Stored-integrity discovery; saved match status is not current native retraining or source qualification."});
  route("get", "/training-evidence/{id}", "trainingEvidence", "trainingEvidenceResponseSchema", {idSchema:"trainingEvidenceIdSchema",description:"Authenticated, mode-scoped summary after native retraining. No raw training inputs or promotion authority."});
  route("get", "/backtest-reports", "backtestReports", "backtestPageSchema", {query:"backtestQuerySchema",description:"Mode-scoped stored-integrity discovery. Native inference is replayed on fixed detail reads."});
  route("get", "/backtest-reports/{id}", "backtestReport", "backtestResponseSchema", {idSchema:"backtestIdSchema", description:"Authenticated historical backtest projection. Alpha fully replays private inputs; historical proxy execution is not actual trading or qualified performance."});
  route("get", "/experimental-calibrations/{id}", "calibrationReport", "calibrationReportResponseSchema", {description:"Authenticated, mode-scoped experimental calibration report. Not production probability qualification."});
  route("get", "/model-review-proposals", "reviewProposals", "modelProposalPageSchema", {query:"modelProposalQuerySchema",description:"Configured reviewers only: all proposal owners in the server data mode; not a pending-only queue."});
  route("get", "/model-review-proposals/{id}", "reviewProposal", "modelProposalResponseSchema", {description:"Configured reviewers only: full replay of another owner's proposal."});
  route("get", "/model-proposals/{id}/reviews", "modelReviewHistory", "modelReviewPageSchema", {query:"modelProposalQuerySchema",description:"Server-configured model reviewers only; newest-first immutable review chain."});
  route("get", "/model-reviews/{id}", "modelReview", "modelReviewResponseSchema", {description:"Server-configured model reviewer only; full review-chain replay."});
  route("get", "/historical-quality-policies", "qualityPolicies", "qualityPolicyPageSchema", {query:"qualityPolicyQuerySchema"});
  route("get", "/historical-quality-policies/{id}", "qualityPolicy", "qualityPolicyResponseSchema", {idSchema:"qualityPolicyIdSchema"});
  route("get", "/historical-quality-assessments/{id}", "qualityAssessment", "qualityAssessmentResponseSchema", {idSchema:"qualityAssessmentIdSchema"});
  route("post", "/historical-quality-assessments", "assessQuality", "qualityAssessmentResponseSchema", {body:"qualityAssessmentRequestSchema"});
  route("get", "/model-quality-gates/{id}", "qualityGate", "qualityGateResponseSchema", {idSchema:"qualityGateIdSchema"});
  route("post", "/model-quality-gates", "createQualityGate", "qualityGateResponseSchema", {body:"qualityGateRequestSchema"});
  route("get", "/model-review-quality-gates/{id}", "reviewQualityGate", "qualityGateResponseSchema", {idSchema:"qualityGateIdSchema"});
  route("post", "/model-review-quality-gates", "createReviewQualityGate", "qualityGateResponseSchema", {body:"qualityGateRequestSchema"});
  route("get", "/probability-quality-policies", "probabilityPolicies", "probabilityPolicyPageSchema", {query:"probabilityPolicyQuerySchema"});
  route("get", "/probability-quality-policies/{id}", "probabilityPolicy", "probabilityPolicyResponseSchema", {idSchema:"probabilityPolicyIdSchema"});
  route("get", "/probability-quality-assessments/{id}", "probabilityAssessment", "probabilityAssessmentResponseSchema", {idSchema:"probabilityAssessmentIdSchema"});
  route("post", "/probability-quality-assessments", "assessProbability", "probabilityAssessmentResponseSchema", {body:"probabilityAssessmentRequestSchema"});
  route("get", "/model-proposals", "modelProposals", "modelProposalPageSchema", {query: "modelProposalQuerySchema"});
  route("post", "/model-proposals", "submitModelProposal", "modelProposalResponseSchema", {body: "modelProposalRequestSchema", key: true, keySchema: "modelProposalKeySchema", description: "Submit immutable candidate evidence, not approval. Preserve the same UUID key and body after response loss."});
  route("get", "/model-proposals/{id}", "modelProposal", "modelProposalResponseSchema");
  route("post", "/experimental-evaluations/compare", "experimentalComparison", "experimentalReportComparisonSchema", {body: "experimentalComparisonRequestSchema", description: "Read-only comparison of explicit registered reports. Requires CSRF and same-origin session; does not approve or promote models."});
  route("get", "/experimental-evaluations/{id}", "experimentalEvaluation", "experimentalPriceReportSchema", {idSchema: "experimentalEvaluationIdSchema", description: "Read-only registered experimental evidence; not approval or formal forward-research results."});
  route("get", "/experimental-models", "experimentalModels", "experimentalModelPageSchema", {query: "experimentalModelQuerySchema", description: "Experimental metadata only; disabled by default, server mode scoped, not qualified final evaluation."});
  route("get", "/evaluation-reports", "evaluationReports", "evaluationPageSchema", {query: "pageQuerySchema"});
  route("post", "/evaluation-reports", "evaluate", "evaluationReportSchema", {body: "evaluationRequestSchema", description: "Owner and data mode come from the authenticated environment. Alpha deduplicates identical report content; no client-supplied cohort or idempotency key is required."});
  route("get", "/evaluation-reports/{id}", "evaluationReport", "evaluationReportSchema");

  // Keep only schema definitions actually reachable from the public operations.
  const used = new Set();
  function collect(value) {
    if (!value || typeof value !== "object") return;
    if (typeof value.$ref === "string" && value.$ref.startsWith("#/components/schemas/")) {
      const name = value.$ref.slice("#/components/schemas/".length);
      if (!schemas[name]) throw new Error("Missing BFF schema: " + name);
      if (!used.has(name)) { used.add(name); collect(schemas[name]); }
    }
    for (const child of Object.values(value)) collect(child);
  }
  collect(paths);
  for (const name of Object.keys(schemas)) if (!used.has(name)) delete schemas[name];

  return {openapi: "3.1.0", info: {title: "YTA BFF API", version: "1.0.0", description: "Browser-facing API. Internal Alpha, database and Hermes credentials are never accepted from clients. JSON bodies are limited to 32 KiB after decompression. Zod normalization and cross-field refinements remain authoritative. Historical fact payloads are versioned and partially open in this document."},
    servers: [{url: "/api/yta/v1"}], paths,
    components: {securitySchemes: {
      session: {type: "apiKey", in: "cookie", name: "yta_session", description: "Development/test only."},
      secureSession: {type: "apiKey", in: "cookie", name: "__Host-yta_session", description: "Staging/production, Secure cookie."},
      csrf: {type: "apiKey", in: "header", name: "X-CSRF-Token", description: "Obtained from login or /me; required with session cookie on authenticated mutations."},
    }, schemas}};
}
