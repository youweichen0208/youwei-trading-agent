import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";
import type { RequestHandler } from "express";
import { context, trace, ROOT_CONTEXT, SpanKind, SpanStatusCode, isSpanContextValid } from "@opentelemetry/api";
import { activeTraceId } from "./tracing.js";

export type Observation = {
  event: "bff.request" | "bff.alpha_request" | "bff.research_admitted";
  request_id: string | null;
  trace_id?: string;
  timestamp: string;
  duration_ms: number;
  status_code: number | null;
  route: string;
  method: string;
  run_id?: string;
  completed?: boolean;
  upstream_request_id?: string | null;
  outcome?: "success" | "failed" | "canceled";
};
export type LogSink = (entry: Observation) => void;
const defaultSink: LogSink = entry => console.info(JSON.stringify(entry));
export const requestContext = new AsyncLocalStorage<{ requestId: string; sink: LogSink }>();
export function emitObservation(sink: LogSink, entry: Observation) {
  try { sink(entry); } catch { /* A logging failure must not change a fact transaction. */ }
}

// Only known endpoint shapes enter logs; query values and actual resource IDs are omitted.
export function safeRoute(path: unknown): string {
  if (typeof path !== "string") return "UNMATCHED";
  const normalized = path.split("?")[0]!.replace(
    /\/(?:model-evaluation|lightgbm)-experimental-[a-f0-9]{64}(?=\/|$)/g, "/:id").replace(
    /\/(?:backtest-report|training-evidence)-[a-f0-9]{64}(?=\/|$)/g, "/:id").replace(
    /\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=\/|$)/gi, "/:id");
  const prefixes = ["/api/yta/v1", "/internal/v1"];
  const prefix = prefixes.find(value => normalized.startsWith(value + "/"));
  if (!prefix) return "UNMATCHED";
  const suffix = normalized.slice(prefix.length);
  const routes = new Set([
    "/model-review-quality-gates", "/model-review-quality-gates/:id", "/model-quality-gates", "/model-quality-gates/:id", "/probability-quality-policies", "/probability-quality-policies/:id", "/probability-quality-assessments", "/probability-quality-assessments/:id", "/training-evidence/:id", "/backtest-reports", "/backtest-reports/:id", "/experimental-calibrations/:id", "/model-review-proposals", "/model-review-proposals/:id", "/model-proposals/:id/reviews", "/model-reviews", "/model-reviews/:id", "/historical-quality-policies", "/historical-quality-policies/:id", "/historical-quality-assessments/:id", "/historical-quality-assessments", "/model-proposals", "/model-proposals/:id", "/experimental-evaluations/compare", "/experimental-models/:id/evaluations", "/experimental-models/:id/training-evidence", "/experimental-evaluations/:id", "/experimental-models", "/health", "/ready", "/me", "/profiles", "/auth/login", "/auth/logout",
    "/docs", "/docs/openapi.json", "/docs/init.js", "/docs/swagger-ui.css", "/docs/swagger-ui-bundle.js",
    "/sessions", "/sessions/:id", "/sessions/:id/messages", "/runs/:id",
    "/runs/:id/events", "/runs/:id/cancel", "/stocks/NVDA/snapshot",
    "/stocks/NVDA/bars", "/stocks/NVDA/indicators", "/stocks/:symbol/freshness", "/stocks/:symbol/bars",
    "/stocks/:symbol/indicators", "/market/snapshot", "/market/indicators",
    "/prediction-drafts", "/prediction-drafts/:id", "/prediction-drafts/:id/evidence", "/predictions", "/predictions/:id",
    "/predictions/:id/settle", "/predictions/:id/replay-settle", "/predictions/:id/corrections",
    "/replay/predictions/:id/settle", "/evaluation-reports", "/evaluation-reports/:id",
  ]);
  if (!routes.has(suffix)) return "UNMATCHED";
  return prefix + suffix;
}
export const safeUpstreamId = (value: string | null) => value && /^req_(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/.test(value) ? value : null;

export function observeRequests(sink: LogSink = defaultSink): RequestHandler {
  return (request, response, next) => {
    const requestId = `req_${randomUUID()}`;
    response.locals.requestId = requestId;
    response.setHeader("X-Request-ID", requestId);
    // The public boundary always starts its own trace, ignoring untrusted propagation headers.
    const span = trace.getTracer("yta-bff").startSpan("bff.http", {kind: SpanKind.SERVER,
      attributes: {"yta.request_id": requestId}}, ROOT_CONTEXT);
    const spanContext = span.spanContext();
    const traceId = isSpanContextValid(spanContext) ? spanContext.traceId : undefined;
    const started = performance.now();
    let written = false;
    const done = () => {
      if (written) return;
      written = true;
      const route = safeRoute(request.route?.path);
      const method = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].includes(request.method) ? request.method : "OTHER";
      span.updateName(`${method} ${route}`);
      span.setAttributes({"http.route": route, "http.request.method": method,
        "http.response.status_code": response.statusCode, "yta.response.completed": response.writableFinished});
      if (!response.writableFinished || response.statusCode >= 500) span.setStatus({code: SpanStatusCode.ERROR});
      span.end();
      emitObservation(sink, {
        event: "bff.request", request_id: requestId, timestamp: new Date().toISOString(),
        trace_id: traceId,
        duration_ms: Math.max(0, Math.round(performance.now() - started)),
        status_code: response.headersSent ? response.statusCode : null,
        method: ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].includes(request.method) ? request.method : "OTHER",
        route: safeRoute(request.route?.path), completed: response.writableFinished,
      });
    };
    response.once("finish", done); response.once("close", done);
    context.with(trace.setSpan(ROOT_CONTEXT, span), () => requestContext.run({requestId, sink}, next));
  };
}

export function upstreamObservation(entry: Omit<Observation, "event" | "request_id" | "timestamp">) {
  const context = requestContext.getStore();
  emitObservation(context?.sink ?? defaultSink, {
    ...entry, event: "bff.alpha_request", request_id: context?.requestId ?? null,
    trace_id: activeTraceId(),
    timestamp: new Date().toISOString(),
  });
}

export function recordResearchAdmission(runId: string) {
  const context = requestContext.getStore();
  if (!context || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(runId)) return;
  emitObservation(context.sink, {event: "bff.research_admitted", request_id: context.requestId,
    trace_id: activeTraceId(),
    run_id: runId, timestamp: new Date().toISOString(), duration_ms: 0, status_code: null,
    method: "POST", route: "/api/yta/v1/sessions/:id/messages"});
}
