"""Real loopback sockets; synthetic credentials/data, no supplier account or model calls."""

import asyncio
import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from pydantic import SecretStr
from yta_alpha.errors import DomainError
from yta_alpha.market_cache import MarketCache
from yta_alpha.models import DataMode, MarketSnapshot

TOKEN = "synthetic-http-provider-token"
START, END = date(2024, 1, 1), date(2026, 1, 1)
BARS = (
    b'{"status":"OK","ticker":"NVDA","adjusted":false,"results":['
    b'{"o":123,"h":124,"l":122,"c":123.123456789123456789,"v":100,"t":1704153600000}]}'
)


@pytest.fixture
def endpoint(monkeypatch):
    import yta_alpha.massive as module

    release = threading.Event()
    state = {"calls": [], "respond": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state["calls"].append((self.path, self.headers.get("Authorization")))
            state["respond"](self)

        def reply(self, status, body=b"{}", *, length=None, location=None):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) if length is None else length))
            self.send_header("Connection", "close")
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(module, "ORIGIN", origin)
    monkeypatch.setattr(module, "REQUEST_INTERVAL", 0)
    try:
        yield state, release, origin, module
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def acquire(module):
    return asyncio.run(module.MassiveCandidate(SecretStr(TOKEN)).acquire(START, END))


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "PROVIDER_AUTH_FAILED", False),
        (403, "PROVIDER_ACCESS_DENIED", False),
        (429, "PROVIDER_RATE_LIMITED", True),
        (500, "PROVIDER_UNAVAILABLE", True),
        (302, "PROVIDER_UNAVAILABLE", False),
    ],
)
def test_real_http_errors_are_redacted_and_not_automatically_retried(
    endpoint, status, code, retryable
):
    state, _, origin, module = endpoint
    state["respond"] = lambda request: request.reply(
        status, TOKEN.encode(), location=origin + "/credential-target"
    )
    with pytest.raises(DomainError) as error:
        acquire(module)
    assert error.value.code == code and error.value.retryable is retryable
    assert TOKEN not in str(error.value)
    assert len(state["calls"]) == 1
    assert state["calls"][0][1] == "Bearer " + TOKEN
    assert "/credential-target" not in state["calls"][0][0]


def test_truncated_socket_body_is_unavailable_not_partial_data(endpoint):
    state, _, _, module = endpoint
    state["respond"] = lambda request: request.reply(200, b"{", length=1000)
    with pytest.raises(DomainError) as error:
        acquire(module)
    assert error.value.code == "PROVIDER_UNAVAILABLE" and error.value.retryable
    assert len(state["calls"]) == 1


def test_real_socket_total_deadline_does_not_retry(endpoint, monkeypatch):
    state, release, _, module = endpoint
    state["respond"] = lambda _request: release.wait(2)
    monkeypatch.setattr(module, "ACQUISITION_TIMEOUT", 0.15)
    with pytest.raises(DomainError) as error:
        acquire(module)
    assert error.value.code == "PROVIDER_UNAVAILABLE" and error.value.retryable
    assert len(state["calls"]) == 1


def test_split_failure_does_not_return_a_partial_acquisition(endpoint):
    state, _, _, module = endpoint

    def respond(request):
        request.reply(429 if request.path.startswith("/stocks/") else 200, BARS)

    state["respond"] = respond
    with pytest.raises(DomainError) as error:
        acquire(module)
    assert error.value.code == "PROVIDER_RATE_LIMITED"
    assert len(state["calls"]) == 2
    assert state["calls"][-1][0].startswith("/stocks/v1/splits?")


def test_real_socket_success_preserves_decimal_evidence_and_unverified_status(endpoint):
    state, _, _, module = endpoint

    def respond(request):
        request.reply(
            200, b'{"status":"OK","results":[]}' if request.path.startswith("/stocks/") else BARS
        )

    state["respond"] = respond
    result = acquire(module)
    assert result.qualification_status == "UNVERIFIED"
    assert result.bars[0].body == BARS
    assert str(result.bars[0].payload()["results"][0]["c"]) == "123.123456789123456789"
    assert len(state["calls"]) == 2


def test_snapshot_cache_socket_429_preserves_only_stale_metadata(endpoint, snapshot, calendar):
    # This is a trusted synthetic snapshot endpoint, not qualification of Massive data.
    state, _, origin, _ = endpoint
    state["respond"] = lambda request: request.reply(200, snapshot.model_dump_json().encode())
    ticks = [0]

    def load():
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=1) as client:
            response = client.get(origin + "/synthetic-snapshot")
            response.raise_for_status()
            return MarketSnapshot.model_validate(response.json())

    cache = MarketCache(
        load,
        DataMode.REPLAY,
        snapshot.provider,
        calendar,
        lambda: snapshot.as_of,
        ttl=1,
        monotonic=lambda: ticks[0],
    )
    initial = cache.read()
    assert initial.status == "FRESH"
    state["respond"] = lambda request: request.reply(429, json.dumps({"error": TOKEN}).encode())
    ticks[0] = 1
    stale = cache.read()
    assert stale.status == "STALE" and stale.data_snapshot_hash == initial.data_snapshot_hash
    assert TOKEN not in stale.model_dump_json()
    with pytest.raises(DomainError) as error:
        cache.read(require_fresh=True)
    assert error.value.code == "STALE_DATA"
    assert len(state["calls"]) == 2
    state["respond"] = lambda request: request.reply(200, snapshot.model_dump_json().encode())
    ticks[0] = 2
    assert cache.read().status == "FRESH"
    assert len(state["calls"]) == 3


def test_personal_collector_waits_before_every_real_transport_request(endpoint, monkeypatch):
    state, _, _, module = endpoint
    waits = []
    original_sleep = asyncio.sleep

    async def simulated_sleep(delay):
        if delay == 15.1:
            waits.append(delay)
            await original_sleep(0)
        else:
            await original_sleep(delay)

    monkeypatch.setattr(module, "REQUEST_INTERVAL", 15.1)
    monkeypatch.setattr(module.asyncio, "sleep", simulated_sleep)
    state["respond"] = lambda request: request.reply(
        200, b'{"status":"OK","results":[]}' if request.path.startswith("/stocks/") else BARS
    )
    acquire(module)
    assert waits == [15.1, 15.1]
    assert len(state["calls"]) == 2
