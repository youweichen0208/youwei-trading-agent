"""Same-origin read-only proxy for the youwei dashboard (S10 slice 3).

Browser -> HTTPS Basic Auth -> this proxy -> Core.

Security shape (owner decision 2026-10-02):

- the tenant API key lives ONLY in this process's environment: it is
  injected here per request and never reaches the page, the URL or
  any browser storage
- only the explicitly whitelisted dashboard GET paths are forwarded;
  everything else — including every Core write path — is a 404 before
  it can reach Core
- the client's own Authorization header is never forwarded; Basic
  Auth credentials are verified in constant time
- static files and /api/config sit behind the same auth

Run (production): configure the env vars below and start with
uvicorn, or `python proxy.py hash-password` to generate the password
hash for DASH_PASSWORD_SCRYPT.
"""

import base64
import hashlib
import hmac
import re
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# The complete forwarding whitelist. A request outside these shapes
# never reaches Core.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ALLOWED_GET = (
    re.compile(rf"^/v1/campaigns/{_UUID}/status$"),
    re.compile(rf"^/v1/campaigns/{_UUID}/cases$"),
    re.compile(rf"^/v1/campaigns/{_UUID}/batches/{_UUID}/reports/(1|20|60)$"),
    re.compile(rf"^/v1/campaigns/{_UUID}/monthly-reports/\d{{4}}-\d{{2}}-\d{{2}}$"),
    # S12b: exploratory research viewing (read-only; submission and
    # cancellation stay off the dashboard proxy — they belong to the
    # authorized research entry, S12c)
    re.compile(r"^/v1/research$"),
    re.compile(rf"^/v1/research/{_UUID}$"),
    re.compile(rf"^/v1/research/{_UUID}/report$"),
)

_REALM = 'Basic realm="youwei-dashboard"'


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of `password` against a stored
    `scrypt:<salt hex>:<digest hex>` string. Fails closed on any
    malformed stored value."""
    try:
        scheme, salt_hex, digest_hex = stored.split(":")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    calculated = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=len(digest) or 32
    )
    return hmac.compare_digest(calculated, digest)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os_urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1
    )
    return f"scrypt:{salt.hex()}:{digest.hex()}"


def os_urandom(n: int) -> bytes:
    import os

    return os.urandom(n)


def create_app(
    *,
    core_url: str,
    tenant_key: str,
    username: str,
    password_hash: str,
    campaign_id: str,
    static_dir: str | Path,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.core_url = core_url
    app.state.tenant_key = tenant_key
    app.state.campaign_id = campaign_id
    app.state.username = username
    app.state.password_hash = password_hash
    app.state.core_client = httpx.AsyncClient(base_url=core_url, timeout=30.0)

    def _authorized(request: Request) -> bool:
        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            return False
        user, _, password = decoded.partition(":")
        return hmac.compare_digest(
            user.encode(), username.encode()
        ) and verify_password(password, password_hash)

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if not _authorized(request):
            return JSONResponse(
                {"detail": "authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": _REALM},
            )
        return await call_next(request)

    @app.get("/api/config")
    async def config():
        return {"campaign_id": app.state.campaign_id}

    @app.api_route("/api/{core_path:path}", methods=["GET"])
    async def forward(core_path: str, request: Request):
        target = f"/{core_path}"  # /api/v1/... -> /v1/...
        if not any(p.match(target) for p in ALLOWED_GET):
            raise HTTPException(status_code=404, detail="not found")
        client: httpx.AsyncClient = app.state.core_client
        # the client's Authorization never travels; the tenant key is
        # attached here and nowhere else
        upstream = await client.get(
            target,
            params=dict(request.query_params),
            headers={"Authorization": f"Bearer {app.state.tenant_key}"},
        )
        content_type = upstream.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            return JSONResponse(status_code=upstream.status_code, content=upstream.json())
        return JSONResponse(
            status_code=502, content={"detail": "unexpected upstream content type"}
        )

    app.mount(
        "/",
        StaticFiles(directory=str(static_dir), html=True),
        name="static",
    )
    return app


def main() -> None:
    import os

    if len(sys.argv) > 1 and sys.argv[1] == "hash-password":
        import getpass

        password = getpass.getpass("dashboard password: ")
        if password != getpass.getpass("repeat: "):
            sys.exit("passwords do not match")
        print(hash_password(password))
        return

    required = (
        "DASH_CORE_URL",
        "DASH_TENANT_KEY",
        "DASH_USERNAME",
        "DASH_PASSWORD_SCRYPT",
        "DASH_CAMPAIGN_ID",
    )
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing env: {', '.join(missing)}")

    app = create_app(
        core_url=os.environ["DASH_CORE_URL"],
        tenant_key=os.environ["DASH_TENANT_KEY"],
        username=os.environ["DASH_USERNAME"],
        password_hash=os.environ["DASH_PASSWORD_SCRYPT"],
        campaign_id=os.environ["DASH_CAMPAIGN_ID"],
        static_dir=Path(__file__).parent / "static",
    )
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("DASH_BIND", "127.0.0.1"),
        port=int(os.environ.get("DASH_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
