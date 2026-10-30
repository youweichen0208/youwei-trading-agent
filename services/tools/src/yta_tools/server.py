import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from opentelemetry import trace
from opentelemetry.context import Context
from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from yta_alpha.models import DataMode, ToolMarketSnapshot
from yta_alpha.tracing import create_provider

from yta_tools.contracts import IndicatorResult, ScopedResult

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_DEADLINE = 15
PATHS = {
    "market_snapshot": "/internal/v1/tools/market-snapshot",
    "indicators": "/internal/v1/tools/indicators",
}
EMPTY_INPUT = {"type": "object", "properties": {}, "additionalProperties": False}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YTA_TOOLS_", env_file=None, hide_input_in_errors=True
    )
    alpha_url: str
    alpha_token: SecretStr = Field(min_length=32)
    data_mode: DataMode
    env: Literal["development", "test", "staging", "production"] = "development"
    otlp_traces_url: str | None = None
    scoped_receipts: bool = False

    @model_validator(mode="after")
    def boundaries(self):
        if self.otlp_traces_url is not None:
            target = urlsplit(self.otlp_traces_url)
            if (
                target.scheme not in ("http", "https")
                or not target.hostname
                or target.username is not None
                or target.password is not None
                or target.query
                or target.fragment
                or target.path != "/v1/traces"
            ):
                raise ValueError(
                    "Tracing requires an HTTP(S) /v1/traces endpoint without credentials or query"
                )
        url = urlsplit(self.alpha_url)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ValueError("Alpha requires a configured HTTP(S) origin")
        token = self.alpha_token.get_secret_value()
        if (
            not token.isascii()
            or any(c.isspace() for c in token)
            or token.startswith("replace-with")
        ):
            raise ValueError("A non-placeholder private tool credential is required")
        return self


class ToolFailure(Exception):
    pass


def validate_result(name: str, body: dict, mode: DataMode) -> dict:
    if name == "market_snapshot":
        value = ToolMarketSnapshot.model_validate(body)
        if value.snapshot.data_mode != mode:
            raise ToolFailure("DATA_MODE_MISMATCH")
        return value.model_dump(mode="json")
    value = IndicatorResult.model_validate(body)
    if value.data_mode != mode:
        raise ToolFailure("DATA_MODE_MISMATCH")
    return body


async def fetch(name: str, settings: Settings, transport=None, context_id=None) -> dict:
    receipt_id = uuid4()
    scoped = context_id is not None
    path = "/internal/v1/tools/observations" if scoped else PATHS[name]
    options = (
        {"json": {"context_id": str(context_id), "request_id": str(receipt_id), "tool": name}}
        if scoped
        else {}
    )
    span = trace.get_current_span()
    span_context = span.get_span_context()
    propagation = {}
    if span_context.is_valid:
        propagation["traceparent"] = (
            f"00-{span_context.trace_id:032x}-{span_context.span_id:016x}-"
            f"{span_context.trace_flags & 1:02x}"
        )
    try:
        async with (
            asyncio.timeout(REQUEST_DEADLINE),
            httpx.AsyncClient(
                timeout=5, trust_env=False, follow_redirects=False, transport=transport
            ) as client,
        ):
            async with client.stream(
                "POST" if scoped else "GET",
                settings.alpha_url.rstrip("/") + path,
                **options,
                headers={
                    "Authorization": f"Bearer {settings.alpha_token.get_secret_value()}",
                    "Accept-Encoding": "identity",
                    **propagation,
                },
            ) as response:
                span.set_attribute("http.response.status_code", response.status_code)
                upstream_id = response.headers.get("x-request-id", "")
                if re.fullmatch(r"req_[a-f0-9]{32}", upstream_id):
                    span.set_attribute("yta.upstream_request_id", upstream_id)
                if response.status_code != 200:
                    # Never expose upstream text, URLs, credentials or internal diagnostics.
                    raise ToolFailure(
                        {401: "ALPHA_AUTH_FAILED", 503: "DATA_UNAVAILABLE"}.get(
                            response.status_code, "ALPHA_REQUEST_FAILED"
                        )
                    )
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ToolFailure("INVALID_ALPHA_RESPONSE")
                if response.headers.get("content-type", "").split(";")[0] != "application/json":
                    raise ToolFailure("INVALID_ALPHA_RESPONSE")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ToolFailure("ALPHA_RESPONSE_TOO_LARGE")
                body = json.loads(data)
                if not isinstance(body, dict):
                    raise ToolFailure("INVALID_ALPHA_RESPONSE")
                if scoped:
                    envelope = ScopedResult.model_validate(body)
                    if (
                        envelope.context_id != context_id
                        or envelope.tool_call_id != receipt_id
                        or envelope.tool != name
                        or envelope.data_mode != settings.data_mode
                    ):
                        raise ToolFailure("INVALID_ALPHA_RESPONSE")
                    result = validate_result(name, envelope.result, settings.data_mode)
                    if result["data_snapshot_hash"] != envelope.data_snapshot_hash:
                        raise ToolFailure("INVALID_ALPHA_RESPONSE")
                    if name == "market_snapshot":
                        snapshot = ToolMarketSnapshot.model_validate(result).snapshot
                        if (
                            snapshot.as_of != envelope.as_of
                            or snapshot.source_id != envelope.source_id
                            or max(x.available_at for x in (*snapshot.bars, *snapshot.splits))
                            != envelope.available_at
                        ):
                            raise ToolFailure("INVALID_ALPHA_RESPONSE")
                    elif IndicatorResult.model_validate(result).as_of != envelope.as_of:
                        raise ToolFailure("INVALID_ALPHA_RESPONSE")
                    return body
                return validate_result(name, body, settings.data_mode)
    except httpx.DecodingError:
        raise ToolFailure("INVALID_ALPHA_RESPONSE") from None
    except (httpx.HTTPError, TimeoutError):
        raise ToolFailure("ALPHA_UNAVAILABLE") from None
    except (ValueError, ValidationError, TypeError, KeyError):
        raise ToolFailure("INVALID_ALPHA_RESPONSE") from None


def create_server(settings: Settings, transport=None) -> Server:
    @asynccontextmanager
    async def lifespan(_server):
        provider = (
            create_provider("yta-finance-tools", settings.env, settings.otlp_traces_url)
            if settings.otlp_traces_url
            else trace.NoOpTracerProvider()
        )
        try:
            yield provider.get_tracer("yta-finance-tools")
        finally:
            if settings.otlp_traces_url:
                await asyncio.to_thread(provider.shutdown)

    async def list_tools(context, params):
        return ListToolsResult(
            tools=[
                Tool(
                    name=name,
                    description=(
                        f"Read NVDA daily {name} from Alpha. Preserve source, as_of and data_mode; "
                        "REPLAY is synthetic research data, not live performance. "
                        + (
                            "Use the server-provided context_id. "
                            "Receipt IDs identify Alpha evidence, "
                            "not Hermes event calls."
                            if settings.scoped_receipts
                            else "No arguments."
                        )
                    ),
                    inputSchema=(
                        {
                            "type": "object",
                            "properties": {"context_id": {"type": "string", "format": "uuid"}},
                            "required": ["context_id"],
                            "additionalProperties": False,
                        }
                        if settings.scoped_receipts
                        else EMPTY_INPUT
                    ),
                    annotations=ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                        openWorldHint=False,
                    ),
                )
                for name in PATHS
            ]
        )

    async def call_tool(context, params):
        tracer = context.lifespan_context
        name = params.name if params.name in PATHS else "UNKNOWN_TOOL"
        # Model-supplied MCP metadata/arguments cannot choose our trace identity.
        with tracer.start_as_current_span(
            "tool." + name,
            context=Context(),
            kind=trace.SpanKind.SERVER,
            attributes={"yta.tool": name, "yta.request_id": "req_" + uuid4().hex},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                if params.name not in PATHS:
                    raise ToolFailure("UNKNOWN_TOOL")
                context_id = None
                if settings.scoped_receipts:
                    arguments = params.arguments
                    if not isinstance(arguments, dict) or set(arguments) != {"context_id"}:
                        raise ToolFailure("INVALID_ARGUMENTS")
                    try:
                        context_id = UUID(arguments["context_id"])
                    except (ValueError, TypeError, AttributeError):
                        raise ToolFailure("INVALID_ARGUMENTS") from None
                elif params.arguments:
                    raise ToolFailure("INVALID_ARGUMENTS")
                with tracer.start_as_current_span(
                    "alpha.request",
                    kind=trace.SpanKind.CLIENT,
                    attributes={
                        "http.route": "/internal/v1/tools/observations"
                        if settings.scoped_receipts
                        else PATHS[name],
                        "http.request.method": "POST" if settings.scoped_receipts else "GET",
                    },
                    record_exception=False,
                    set_status_on_exception=False,
                ) as upstream:
                    try:
                        result = await fetch(params.name, settings, transport, context_id)
                    except BaseException:
                        upstream.set_status(trace.StatusCode.ERROR)
                        raise
                return CallToolResult(
                    content=[TextContent(text=json.dumps(result))], structuredContent=result
                )
            except ToolFailure as error:
                span.set_status(trace.StatusCode.ERROR)
                span.set_attribute("yta.failure_code", str(error))
                return CallToolResult(isError=True, content=[TextContent(text=str(error))])
            except BaseException:
                span.set_status(trace.StatusCode.ERROR)
                raise

    server = Server(
        "yta-finance",
        version="0.1.0",
        lifespan=lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    # Only the explicitly allowlisted spans above may capture tool observations.
    server.middleware.clear()
    return server


async def main():
    server = create_server(Settings())
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        raise SystemExit("Finance tools failed; check private server configuration") from None
