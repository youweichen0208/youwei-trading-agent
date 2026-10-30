"""Explicit, bounded tracing. No automatic request, exception or header capture."""

import secrets

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from yta_alpha.settings import Settings


def create_provider(service: str, environment: str, endpoint: str) -> TracerProvider:
    provider = TracerProvider(
        resource=Resource({"service.name": service, "deployment.environment.name": environment}),
        shutdown_on_exit=False,
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, headers={}, timeout=3),
            max_queue_size=512,
            max_export_batch_size=64,
            schedule_delay_millis=1000,
            export_timeout_millis=3000,
        )
    )
    return provider


class AlphaTracing:
    def __init__(self, settings: Settings):
        self.provider = None
        if settings.otlp_traces_url:
            self.provider = create_provider("yta-alpha", settings.env, settings.otlp_traces_url)
        provider = self.provider or trace.NoOpTracerProvider()
        self.tracer = provider.get_tracer("yta-alpha")
        self.tokens = [settings.alpha_token.get_secret_value()]
        if settings.alpha_tools_token:
            self.tokens.append(settings.alpha_tools_token.get_secret_value())

    def parent(self, headers):
        authorization = headers.get("authorization", "").encode()
        trusted = any(
            secrets.compare_digest(authorization, ("Bearer " + token).encode())
            for token in self.tokens
        )
        if not trusted:
            return Context()
        return TraceContextTextMapPropagator().extract(
            {"traceparent": headers.get("traceparent", "")}, context=Context()
        )

    def shutdown(self):
        if self.provider:
            self.provider.shutdown()
