"""Private Streamable HTTP transport with a distinct service credential."""

import secrets

from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, SecretStr, model_validator
from starlette.responses import JSONResponse

from yta_tools.server import Settings, create_server


class HTTPSettings(Settings):
    mcp_token: SecretStr = Field(min_length=32)

    @model_validator(mode="after")
    def distinct_transport_credential(self):
        token = self.mcp_token.get_secret_value()
        if (
            token == self.alpha_token.get_secret_value()
            or not token.isascii()
            or any(c.isspace() for c in token)
            or token.startswith("replace-with")
        ):
            raise ValueError("MCP requires a distinct private transport credential")
        return self


class PrivateTransport:
    def __init__(self, app, token: str):
        self.app = app
        self.expected = b"Bearer " + token.encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        credentials = [value for key, value in headers if key.lower() == b"authorization"]
        if len(credentials) != 1 or not secrets.compare_digest(credentials[0], self.expected):
            return await JSONResponse(
                {"error": "UNAUTHORIZED"},
                401,
                headers={"Cache-Control": "no-store", "WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
        if any(key.lower() == b"origin" for key, _ in headers):
            return await JSONResponse(
                {"error": "BROWSER_ORIGIN_FORBIDDEN"},
                403,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, private_send)


def create_http_app(settings: HTTPSettings | None = None, transport=None):
    settings = settings or HTTPSettings()
    app = create_server(settings, transport).streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=32768,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["finance-tools:8200", "127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=[],
        ),
    )
    app.add_middleware(PrivateTransport, token=settings.mcp_token.get_secret_value())
    return app
