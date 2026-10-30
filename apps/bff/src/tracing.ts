import { context, trace, isSpanContextValid } from "@opentelemetry/api";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import type { Config } from "./config.js";

export function startTracing(config: Config): (() => Promise<void>) {
  if (!config.YTA_OTLP_TRACES_URL) return async () => {};
  const exporter = new OTLPTraceExporter({url: config.YTA_OTLP_TRACES_URL, headers: {}, timeoutMillis: 3000});
  const provider = new NodeTracerProvider({
    resource: resourceFromAttributes({"service.name": "yta-bff", "deployment.environment.name": config.YTA_ENV}),
    spanProcessors: [new BatchSpanProcessor(exporter, {maxQueueSize: 512, maxExportBatchSize: 64, scheduledDelayMillis: 1000, exportTimeoutMillis: 3000})],
  });
  provider.register();
  return async () => {
    try { await provider.shutdown(); }
    catch {
      // Telemetry loss must not turn an otherwise clean service shutdown into a failure.
      // Exporter errors can contain remote response text; emit only a fixed diagnostic.
      console.warn(JSON.stringify({event: "bff.tracing_flush_failed"}));
    }
  };
}

export function activeTraceId(): string | undefined {
  const value = trace.getSpan(context.active())?.spanContext();
  return value && isSpanContextValid(value) ? value.traceId : undefined;
}

export function traceHeaders(): Record<string, string> {
  const value = trace.getSpan(context.active())?.spanContext();
  if (!value || !isSpanContextValid(value)) return {};
  // Only W3C traceparent crosses the private boundary; never baggage or arbitrary headers.
  return {traceparent: `00-${value.traceId}-${value.spanId}-${(value.traceFlags & 1).toString(16).padStart(2, "0")}`};
}
