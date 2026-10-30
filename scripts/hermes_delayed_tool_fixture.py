"""Private diagnostic MCP tool with a controllable completion barrier; no financial data."""

import asyncio
import json
import os

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from starlette.responses import JSONResponse
from starlette.routing import Route
from yta_tools.http import PrivateTransport


def main():
    assert os.environ.get("YTA_TOOL_CANCEL_PROBE") == "1"
    release = asyncio.Event()
    state = {"started": 0, "finished": 0, "cancelled": 0, "timed_out": 0}

    async def list_tools(context, params):
        return ListToolsResult(
            tools=[
                Tool(
                    name="delayed_diagnostic",
                    description=(
                        "Run a controlled cancellation diagnostic. "
                        "No arguments or financial side effects."
                    ),
                    inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
                )
            ]
        )

    async def call_tool(context, params):
        if params.name != "delayed_diagnostic" or params.arguments:
            return CallToolResult(isError=True, content=[TextContent(text="Unsupported probe")])
        state["started"] += 1
        try:
            await asyncio.wait_for(release.wait(), timeout=60)
        except asyncio.CancelledError:
            state["cancelled"] += 1
            raise
        except TimeoutError:
            state["timed_out"] += 1
            return CallToolResult(
                isError=True, content=[TextContent(text="Probe barrier timed out")]
            )
        state["finished"] += 1
        return CallToolResult(content=[TextContent(text=json.dumps({"diagnostic": "finished"}))])

    async def stats(request):
        return JSONResponse(state)

    async def unblock(request):
        release.set()
        return JSONResponse({"released": True})

    app = Server(
        "yta-cancel-probe", on_list_tools=list_tools, on_call_tool=call_tool
    ).streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["tool-fixture:8200", "127.0.0.1:*"],
            allowed_origins=[],
        ),
    )
    app.router.routes.extend([Route("/stats", stats), Route("/release", unblock, methods=["POST"])])
    app.add_middleware(PrivateTransport, token=os.environ["YTA_FINANCE_MCP_TOKEN"])
    uvicorn.run(app, host="0.0.0.0", port=8200, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
