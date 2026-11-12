"""Run-bound gateway authorization, separate from prompts, arguments and session memory.

Each isolated Profile owns this private SQLite file. Only an authenticated Runs
request can create a binding; canonical admission JSON stores the opaque run ID.
There is deliberately no 'current task for this profile' lookup or fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_RUN = re.compile(r"run_[a-f0-9]{32}")
_CAP = re.compile(r"[a-f0-9]{64}")
_call: ContextVar[tuple[str, str] | None] = ContextVar("alpha_tool_call", default=None)
MAX_RESPONSE = 4 * 1024 * 1024


class BridgeError(Exception):
    """Only fixed codes may cross the model-facing boundary."""


@dataclass(frozen=True)
class Binding:
    run_id: str
    session_id: str
    capability: str = field(repr=False)


class BindingStore:
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # The launcher supplies a dedicated private directory, never a workspace.
        if path.is_symlink() or path.parent.is_symlink():
            raise BridgeError("invalid_binding_store")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.path = path
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS bindings (
                run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                capability TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def bind(self, run_id: str, session_id: str, capability: str) -> None:
        if not _RUN.fullmatch(run_id) or not _CAP.fullmatch(capability) or not session_id:
            raise BridgeError("invalid_binding")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO bindings(run_id,session_id,capability) VALUES(?,?,?)",
                       (run_id, session_id, capability))
            row = db.execute("SELECT session_id,capability FROM bindings WHERE run_id=?",
                             (run_id,)).fetchone()
            if row != (session_id, capability):
                raise BridgeError("binding_conflict")

    def get(self, run_id: str) -> Binding:
        if not isinstance(run_id, str) or not _RUN.fullmatch(run_id):
            raise BridgeError("missing_run_identity")
        with self.connect() as db:
            row = db.execute("SELECT session_id,capability FROM bindings WHERE run_id=?",
                             (run_id,)).fetchone()
        if row is None:
            raise BridgeError("missing_run_binding")
        return Binding(run_id, *row)


def store() -> BindingStore:
    path = os.environ.get("ALPHA_BINDING_DB")
    if not path or not Path(path).is_absolute():
        raise BridgeError("binding_store_not_configured")
    return BindingStore(Path(path))


def validate_request(body, headers, canonical_authority) -> str:
    # A dedicated Alpha runtime exposes only this narrow request shape. No
    # alternate route, room, profile, history injection or desktop handoff.
    cap = headers.get("X-Alpha-Tool-Capability", "")
    key = headers.get("Idempotency-Key", "")
    if not _CAP.fullmatch(cap):
        raise BridgeError("missing_execution_capability")
    if not canonical_authority:
        raise BridgeError("canonical_authority_required")
    if (not isinstance(body, dict) or set(body) != {"input", "session_id", "instructions"}
            or any(not isinstance(body[k], str) or not body[k] for k in body)
            or not re.fullmatch(r"[!-~]{1,255}", key)):
        raise BridgeError("invalid_alpha_run_request")
    return cap


def fingerprint(original: str, capability: str) -> str:
    # Same native Idempotency-Key with another capability must conflict, even
    # when the original run is already running or has survived a restart.
    return hashlib.sha256((original + ":" + capability).encode()).hexdigest()


def bind_admission(run_id: str, session_id: str, capability: str) -> None:
    store().bind(run_id, session_id, capability)


def admission_identity(run_id: str | None) -> str | None:
    if not run_id:
        return None
    return store().get(run_id).run_id


@contextmanager
def tool_scope(call_id: str):
    # Patched core invokes this inside each actual tool dispatch thread. The
    # canonical execution context is restored from the durable admission.
    from gateway.session_api_turn import api_execution

    current = api_execution.get()
    run_id = current.get("alpha_run_id") if isinstance(current, dict) else None
    if not run_id or not isinstance(call_id, str) or not 1 <= len(call_id) <= 512:
        raise BridgeError("missing_tool_identity")
    binding = store().get(run_id)
    token = _call.set((binding.run_id, call_id))
    try:
        yield
    finally:
        _call.reset(token)


def operation_id(run_id: str, call_id: str) -> str:
    return "hermes:" + hashlib.sha256(json.dumps([run_id, call_id]).encode()).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeError("gateway_redirect_refused")


def gateway_origin() -> str:
    origin = os.environ.get("ALPHA_TOOL_ORIGIN", "")
    url = urlsplit(origin)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.path not in {"", "/"} or url.query or url.fragment):
        raise BridgeError("invalid_gateway_origin")
    return origin.rstrip("/")


def catalog() -> dict:
    return json.loads(Path(__file__).with_name("tools.json").read_text())


def role_tools() -> list[dict]:
    role = os.environ.get("ALPHA_ROLE", "")
    if role not in {"research", "prediction", "portfolio"}:
        raise BridgeError("invalid_profile_role")
    return catalog()[role]


def guard_dispatch(function):
    """Core boundary, outside Hermes' deliberately fail-open plugin middleware."""
    @wraps(function)
    def guarded(*args, **kwargs):
        with ExitStack() as scope:
            try:
                if kwargs["function_name"] not in {t["name"] for t in role_tools()}:
                    raise BridgeError("tool_not_allowed")
                scope.enter_context(tool_scope(kwargs["tool_call_id"]))
            except Exception as exc:
                from agent.tool_executor import _ManagedToolResult

                # Native concurrent dispatch serializes authorization starts.
                # A refusal must release its position without executing a tool.
                begin = kwargs.get("begin_execution")
                if begin is not None:
                    begin()
                code = str(exc) if isinstance(exc, BridgeError) else "tool_identity_unavailable"
                return _ManagedToolResult(
                    result=json.dumps({"error": code}), args=kwargs["function_args"],
                    middleware_trace=[], blocked=True, dispatched=False,
                )
            return function(*args, **kwargs)

    return guarded


def invoke(name: str, arguments: dict) -> str:
    """Registered handler. Missing core context always denies, including if a
    middleware fails open or another plugin tries to dispatch this directly.
    """
    try:
        context = _call.get()
        if context is None:
            raise BridgeError("missing_tool_identity")
        if name not in {t["name"] for t in role_tools()}:
            raise BridgeError("tool_not_allowed")
        binding = store().get(context[0])
        body = json.dumps({"tool": name, "arguments": arguments}, allow_nan=False).encode()
        if len(body) > 65536:
            raise BridgeError("tool_request_too_large")
        req = Request(gateway_origin() + "/call", data=body, method="POST", headers={
            "Authorization": "Bearer " + binding.capability,
            "Content-Type": "application/json",
            "X-Alpha-Operation-Id": operation_id(*context),
        })
        # No environment proxy and no automatic retry of a possible mutation.
        opener = build_opener(ProxyHandler({}), NoRedirect())
        try:
            response = opener.open(req, timeout=30)
        except HTTPError as exc:
            exc.close()
            raise BridgeError("gateway_call_rejected") from None
        with response:
            data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise BridgeError("gateway_response_too_large")
        result = json.loads(data)
        # A broken/misconfigured upstream must not echo the bearer into history.
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if binding.capability in encoded:
            raise BridgeError("gateway_response_contains_credential")
        return encoded
    except BridgeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception:
        # urllib/JSON/SQLite exceptions can contain addresses, payloads or headers.
        return json.dumps({"error": "gateway_dependency_unavailable"})


def register(ctx):
    for tool in role_tools():
        name = tool["name"]

        def handler(args, _name=name):
            return invoke(_name, args)

        ctx.register_tool(name=name, toolset="alpha_lab", handler=handler, schema={
            "name": name, "description": tool["description"], "parameters": tool["inputSchema"],
        })
