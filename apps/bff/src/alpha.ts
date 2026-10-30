import {readResearchContext,readResearchContextSummary} from "./research-context.js";
import {trainingEvidenceQuerySchema,trainingEvidenceIdSchema} from "@yta/contracts";
import {readTrainingEvidencePage,readTrainingEvidence} from "./training-evidence.js";
import {readBacktestPage,readBacktestReport} from "./backtest-report.js";
import {backtestIdSchema,backtestQuerySchema} from "@yta/contracts";
import {readQualityGate} from "./quality-gate.js";
import {readProbabilityPolicyPage,readProbabilityPolicy,readProbabilityAssessment} from "./probability-quality.js";
import {readCalibrationReport} from "./calibration-report.js";
import {readModelReviewPage,readModelReview} from "./model-review.js";
import { readQualityPolicyPage, readQualityPolicy, readQualityAssessment } from "./model-quality.js";
import { qualityGateIdSchema, qualityGateRequestSchema, probabilityPolicyQuerySchema, probabilityPolicyIdSchema, probabilityAssessmentIdSchema, probabilityAssessmentRequestSchema, experimentalCalibrationIdSchema, modelReviewSubmissionSchema, qualityPolicyQuerySchema, qualityPolicyIdSchema, qualityAssessmentIdSchema, qualityAssessmentRequestSchema, modelProposalQuerySchema, modelProposalKeySchema, modelProposalRequestSchema, experimentalComparisonRequestSchema, experimentalModelIdSchema, experimentalEvaluationPageSchema, experimentalEvaluationQuerySchema, experimentalEvaluationIdSchema, experimentalModelPageSchema, experimentalModelQuerySchema, errorSchema, forecastSchema, marketEvidenceSchema, marketFreshnessSchema } from "@yta/contracts";
import { z } from "zod";
import type { Config } from "./config.js";
import { ApiError } from "./errors.js";
import { readReviewProposal, readReviewProposalPage, readModelProposalPage, readModelProposal } from "./model-proposal.js";
import { readExperimentalComparison, readExperimentalReport } from "./experimental-report.js";
import { trace, SpanKind, SpanStatusCode } from "@opentelemetry/api";
import { traceHeaders } from "./tracing.js";

import { safeRoute, safeUpstreamId, upstreamObservation } from "./observability.js";

export const draftSchema = z.strictObject({ draft_id: z.uuid(), forecast: forecastSchema });

export class AlphaClient {
  constructor(private config: Config) {}
  async request(path: string, ownerId?: string, body?: unknown, key?: string, signal?: AbortSignal): Promise<unknown> {
    return trace.getTracer("yta-bff").startActiveSpan("alpha.request", {kind: SpanKind.CLIENT,
      attributes: {"http.route": safeRoute(path), "http.request.method": body === undefined ? "GET" : "POST"}}, async span => {
      const started = performance.now();
      const observation: {status: number | null; id: string | null} = {status: null, id: null};
      let outcome: "success" | "failed" | "canceled" = "failed";
      try {
        const result = await this.performRequest(path, ownerId, body, key, signal, observation);
        outcome = "success";
        return result;
      } finally {
        if (signal?.aborted) outcome = "canceled";
        if (observation.status !== null) span.setAttribute("http.response.status_code", observation.status);
        if (observation.id) span.setAttribute("yta.upstream_request_id", observation.id);
        if (outcome !== "success") span.setStatus({code: SpanStatusCode.ERROR});
        upstreamObservation({route: safeRoute(path), method: body === undefined ? "GET" : "POST",
          status_code: observation.status, upstream_request_id: observation.id, outcome,
          duration_ms: Math.max(0, Math.round(performance.now() - started))});
        span.end();
      }
    });
  }
  private async performRequest(path: string, ownerId: string | undefined, body: unknown,
    key: string | undefined, signal: AbortSignal | undefined,
    observation: {status: number | null; id: string | null}): Promise<unknown> {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.config.YTA_ALPHA_TOKEN}`, "Content-Type": "application/json", ...traceHeaders() };
    if (ownerId) headers["X-YTA-Owner-ID"] = ownerId;
    if (key) headers["Idempotency-Key"] = key;
    let response: Response;
    let text: string;
    try {
      response = await fetch(`${this.config.YTA_ALPHA_URL}${path}`, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body), redirect: "error", signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000) });
      observation.status = response.status;
      observation.id = safeUpstreamId(response.headers.get("x-request-id"));
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      const parts: string[] = [];
      let bytes = 0;
      if (reader) {
        try {
          while (true) {
            const chunk = await reader.read();
            if (chunk.done) break;
            bytes += chunk.value.byteLength;
            if (bytes > 4_000_000) {
              await reader.cancel().catch(() => undefined);
              throw new ApiError("UPSTREAM_INVALID", "Calculation response exceeds size limit", 502);
            }
            parts.push(decoder.decode(chunk.value, {stream: true}));
          }
          parts.push(decoder.decode());
        } finally { reader.releaseLock(); }
      }
      text = parts.join("");
    } catch (error) {
      if (signal?.aborted) throw signal.reason;
      if (error instanceof ApiError) throw error;
      throw new ApiError("ALPHA_UNAVAILABLE", "Calculation service is unavailable; retry with the same request key", 503, true);
    }
    let value: unknown;
    try { value = JSON.parse(text); } catch { throw new ApiError("UPSTREAM_INVALID", "Calculation service returned an invalid response", 502); }
    if (!response.ok) {
      const error = errorSchema.safeParse(value);
      if (!error.success) throw new ApiError("ALPHA_UNAVAILABLE", "Calculation request failed", 502, true);
      throw new ApiError(error.data.error.code, error.data.error.message, response.status, error.data.error.retryable);
    }
    return value;
  }

  async prepareResearch(ownerId: string, runId: string, signal?: AbortSignal) {
    const id = z.uuid().parse(runId);
    return readResearchContextSummary(await this.request("/internal/v1/research-tool-contexts",
      ownerId, {run_id: id}, undefined, signal), this.config.YTA_DATA_MODE, id);
  }
  async researchContext(ownerId: string, runId: string, signal?: AbortSignal) {
    const id = z.uuid().parse(runId);
    return readResearchContext(await this.request(`/internal/v1/research-tool-contexts/${id}`,
      ownerId, undefined, undefined, signal), this.config.YTA_DATA_MODE, id);
  }
  async researchDraft(ownerId: string, runId: string, signal?: AbortSignal) {
    const id = z.uuid().parse(runId);
    const parsed = draftSchema.safeParse(await this.request(`/internal/v1/research-tool-contexts/${id}/draft`,
      ownerId, {}, undefined, signal));
    if (!parsed.success || parsed.data.forecast.data_mode !== this.config.YTA_DATA_MODE)
      throw new ApiError("RESEARCH_CONTEXT_INVALID", "Research draft scope is invalid", 502);
    return parsed.data;
  }

  async draft(ownerId: string, runId: string, signal?: AbortSignal) {
    return draftSchema.parse(await this.request("/internal/v1/prediction-drafts", ownerId, { request_id: runId, symbol: "NVDA" }, undefined, signal));
  }

  async draftEvidence(ownerId: string, draftId: string) {
    const result = marketEvidenceSchema.safeParse(await this.request(
      `/internal/v1/prediction-drafts/${z.uuid().parse(draftId)}/evidence`, ownerId));
    if (!result.success) throw new ApiError("UPSTREAM_INVALID", "Stored evidence response is invalid", 502);
    if (result.data.snapshot.data_mode !== this.config.YTA_DATA_MODE) {
      throw new ApiError("NOT_FOUND", "Draft evidence not found", 404);
    }
    return result.data;
  }

  async experimentalEvaluations(ownerId: string, modelId: string, input: z.output<typeof experimentalEvaluationQuerySchema>) {
    const id = experimentalModelIdSchema.parse(modelId);
    const params = new URLSearchParams({limit: String(input.limit ?? 20)});
    if (input.cursor) params.set("cursor", input.cursor);
    const parsed = experimentalEvaluationPageSchema.safeParse(await this.request(
      `/internal/v1/experimental-models/${id}/evaluations?${params}`, ownerId));
    if (!parsed.success || parsed.data.items.length > (input.limit ?? 20)
      || parsed.data.items.some(row => row.model_id !== id || row.data_mode !== this.config.YTA_DATA_MODE || row.id === input.cursor)
      || (input.cursor && parsed.data.next_cursor === input.cursor)) {
      throw new ApiError("UPSTREAM_INVALID", "Experimental evaluation list is invalid", 502);
    }
    return parsed.data;
  }

  async trainingEvidenceList(owner:string,modelId:string,query:z.output<typeof trainingEvidenceQuerySchema>) {
    const id=experimentalModelIdSchema.parse(modelId);
    const params=new URLSearchParams({limit:String(query.limit??20)});
    if(query.cursor)params.set("cursor",query.cursor);
    return readTrainingEvidencePage(await this.request(`/internal/v1/experimental-models/${id}/training-evidence?${params}`,owner),this.config.YTA_DATA_MODE,id,query);
  }
  async trainingEvidence(owner:string,identity:string) {
    const id=trainingEvidenceIdSchema.parse(identity);
    return readTrainingEvidence(await this.request("/internal/v1/training-evidence/"+id,owner),this.config.YTA_DATA_MODE,id);
  }
  async backtestReports(owner:string,query:z.output<typeof backtestQuerySchema>) {
    const params=new URLSearchParams({limit:String(query.limit??20)});
    for(const name of ["cursor","model_id","status"] as const)if(query[name])params.set(name,query[name]);
    return readBacktestPage(await this.request("/internal/v1/backtest-reports?"+params,owner),this.config.YTA_DATA_MODE,query);
  }
  async backtestReport(owner:string,identity:string) {
    const id=backtestIdSchema.parse(identity);
    return readBacktestReport(await this.request("/internal/v1/backtest-reports/"+id,owner),this.config.YTA_DATA_MODE,id);
  }
  async calibrationReport(owner:string,identity:string) {
    const id=experimentalCalibrationIdSchema.parse(identity);
    return readCalibrationReport(await this.request("/internal/v1/experimental-calibrations/"+id,owner),this.config.YTA_DATA_MODE,id);
  }
  async submitModelProposal(ownerId: string, input: z.output<typeof modelProposalRequestSchema>, key: string) {
    const request = modelProposalRequestSchema.parse(input), identity = modelProposalKeySchema.parse(key);
    return readModelProposal(await this.request("/internal/v1/model-proposals", ownerId, request, identity),
      ownerId, this.config.YTA_DATA_MODE, {key: identity, request});
  }

  async submitModelReview(owner: string, input: z.output<typeof modelReviewSubmissionSchema>, key: string) {
    const request=modelReviewSubmissionSchema.parse(input),identity=modelProposalKeySchema.parse(key);
    return readModelReview(await this.request("/internal/v1/model-reviews",owner,request,identity),this.config.YTA_DATA_MODE,
      {reviewer:owner,key:identity,request});
  }
  async modelReviewHistory(owner:string,identity:string,input:z.output<typeof modelProposalQuerySchema>) {
    const id=modelProposalKeySchema.parse(identity),params=new URLSearchParams({limit:String(input.limit ?? 20)});
    if(input.cursor)params.set("cursor",input.cursor);
    return readModelReviewPage(await this.request(`/internal/v1/model-proposals/${id}/reviews?${params}`,owner),this.config.YTA_DATA_MODE,id,input);
  }
  async modelReview(owner: string, identity: string) {
    const id=modelProposalKeySchema.parse(identity);
    return readModelReview(await this.request("/internal/v1/model-reviews/"+id,owner),this.config.YTA_DATA_MODE,{id});
  }
  async qualityPolicies(owner: string, input: z.output<typeof qualityPolicyQuerySchema>) {
    const params=new URLSearchParams({limit:String(input.limit ?? 20)});
    if(input.cursor) params.set("cursor",input.cursor);
    if(input.horizon_sessions) params.set("horizon_sessions",input.horizon_sessions);
    return readQualityPolicyPage(await this.request(`/internal/v1/historical-quality-policies?${params}`,owner),this.config.YTA_DATA_MODE,input);
  }
  async qualityPolicy(owner: string, identity: string) {
    const id=qualityPolicyIdSchema.parse(identity);
    return readQualityPolicy(await this.request("/internal/v1/historical-quality-policies/"+id,owner),id,this.config.YTA_DATA_MODE);
  }
  async qualityAssessment(owner: string, identity: string) {
    const id=qualityAssessmentIdSchema.parse(identity);
    return readQualityAssessment(await this.request("/internal/v1/historical-quality-assessments/"+id,owner),this.config.YTA_DATA_MODE,{id});
  }
  async assessQuality(owner: string, input: z.output<typeof qualityAssessmentRequestSchema>) {
    const request=qualityAssessmentRequestSchema.parse(input);
    return readQualityAssessment(await this.request("/internal/v1/historical-quality-assessments",owner,request),this.config.YTA_DATA_MODE,{request});
  }

  async reviewQualityGate(owner:string,identity:string) {
    const id=qualityGateIdSchema.parse(identity);
    return readQualityGate(await this.request("/internal/v1/model-review-quality-gates/"+id,owner),this.config.YTA_DATA_MODE,{id});
  }
  async createReviewQualityGate(owner:string,input:z.output<typeof qualityGateRequestSchema>) {
    const request=qualityGateRequestSchema.parse(input);
    return readQualityGate(await this.request("/internal/v1/model-review-quality-gates",owner,request),this.config.YTA_DATA_MODE,{request});
  }
  async qualityGate(owner:string,identity:string) {
    const id=qualityGateIdSchema.parse(identity);
    return readQualityGate(await this.request("/internal/v1/model-quality-gates/"+id,owner),this.config.YTA_DATA_MODE,{id});
  }
  async createQualityGate(owner:string,input:z.output<typeof qualityGateRequestSchema>) {
    const request=qualityGateRequestSchema.parse(input);
    return readQualityGate(await this.request("/internal/v1/model-quality-gates",owner,request),this.config.YTA_DATA_MODE,{request});
  }
  async probabilityPolicies(owner: string, input: z.output<typeof probabilityPolicyQuerySchema>) {
    const params=new URLSearchParams({limit:String(input.limit ?? 20)});
    if(input.cursor) params.set("cursor",input.cursor);
    if(input.horizon_sessions) params.set("horizon_sessions",input.horizon_sessions);
    return readProbabilityPolicyPage(await this.request(`/internal/v1/probability-quality-policies?${params}`,owner),this.config.YTA_DATA_MODE,input);
  }
  async probabilityPolicy(owner: string, identity: string) {
    const id=probabilityPolicyIdSchema.parse(identity);
    return readProbabilityPolicy(await this.request("/internal/v1/probability-quality-policies/"+id,owner),id,this.config.YTA_DATA_MODE);
  }
  async probabilityAssessment(owner: string, identity: string) {
    const id=probabilityAssessmentIdSchema.parse(identity);
    return readProbabilityAssessment(await this.request("/internal/v1/probability-quality-assessments/"+id,owner),this.config.YTA_DATA_MODE,{id});
  }
  async assessProbability(owner: string, input: z.output<typeof probabilityAssessmentRequestSchema>) {
    const request=probabilityAssessmentRequestSchema.parse(input);
    return readProbabilityAssessment(await this.request("/internal/v1/probability-quality-assessments",owner,request),this.config.YTA_DATA_MODE,{request});
  }

  async reviewProposals(reviewer:string,input:z.output<typeof modelProposalQuerySchema>) {
    const params=new URLSearchParams({limit:String(input.limit ?? 20)});
    if(input.cursor)params.set("cursor",input.cursor);
    return readReviewProposalPage(await this.request(`/internal/v1/model-review-proposals?${params}`,reviewer),this.config.YTA_DATA_MODE,input);
  }
  async reviewProposal(reviewer:string,identity:string) {
    const id=modelProposalKeySchema.parse(identity);
    return readReviewProposal(await this.request("/internal/v1/model-review-proposals/"+id,reviewer),this.config.YTA_DATA_MODE,id);
  }
  async modelProposals(ownerId: string, input: z.output<typeof modelProposalQuerySchema>) {
    const params = new URLSearchParams({limit: String(input.limit ?? 20)});
    if (input.cursor) params.set("cursor", input.cursor);
    return readModelProposalPage(await this.request(`/internal/v1/model-proposals?${params}`, ownerId),
      ownerId, this.config.YTA_DATA_MODE, input);
  }

  async modelProposal(ownerId: string, identity: string) {
    const id = z.uuid().parse(identity).toLowerCase();
    return readModelProposal(await this.request("/internal/v1/model-proposals/" + id, ownerId),
      ownerId, this.config.YTA_DATA_MODE, {id});
  }

  async experimentalComparison(ownerId: string, input: z.output<typeof experimentalComparisonRequestSchema>) {
    const request = experimentalComparisonRequestSchema.parse(input);
    return readExperimentalComparison(await this.request(
      "/internal/v1/experimental-evaluations/compare", ownerId, request), request.evaluation_ids, this.config.YTA_DATA_MODE);
  }

  async experimentalEvaluation(ownerId: string, identity: string) {
    const id = experimentalEvaluationIdSchema.parse(identity);
    return readExperimentalReport(await this.request(
      "/internal/v1/experimental-evaluations/" + id, ownerId), id, this.config.YTA_DATA_MODE);
  }

  async experimentalModels(ownerId: string, input: z.output<typeof experimentalModelQuerySchema>) {
    const params = new URLSearchParams({limit: String(input.limit ?? 20)});
    if (input.cursor) params.set("cursor", input.cursor);
    if (input.horizon_sessions) params.set("horizon_sessions", input.horizon_sessions);
    const result = experimentalModelPageSchema.safeParse(await this.request(
      "/internal/v1/experimental-models?" + params.toString(), ownerId));
    if (!result.success || result.data.items.length > (input.limit ?? 20)
      || result.data.items.some(row => row.data_mode !== this.config.YTA_DATA_MODE
        || (input.horizon_sessions !== undefined && String(row.horizon_sessions) !== input.horizon_sessions))
      || (input.cursor !== undefined && (result.data.next_cursor === input.cursor
        || result.data.items.some(row => row.id === input.cursor)))) {
      throw new ApiError("UPSTREAM_INVALID", "Experimental model directory response is invalid", 502);
    }
    return result.data;
  }

  async marketFreshness(ownerId: string) {
    const result = marketFreshnessSchema.safeParse(await this.request("/internal/v1/market/freshness", ownerId));
    if (!result.success || result.data.data_mode !== this.config.YTA_DATA_MODE) {
      throw new ApiError("UPSTREAM_INVALID", "Market status response is invalid", 502);
    }
    return result.data;
  }
}
