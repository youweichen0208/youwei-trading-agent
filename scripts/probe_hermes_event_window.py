"""Exercise locked Hermes SSE handlers with seeded events, without model requests."""

import asyncio
import hashlib
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace


async def main():
    assert os.environ.get("YTA_EVENT_WINDOW_PROBE") == "1"
    assert {
        name
        for _, name in socket.if_nameindex()
        if int(Path("/sys/class/net", name, "flags").read_text(), 16) & 1
    } == {"lo"}
    os.environ["HERMES_HOME"] = "/tmp/yta-event-window"
    Path(os.environ["HERMES_HOME"]).mkdir(mode=0o700)
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms import api_server_runs as runs
    from gateway.platforms.api_server import APIServerAdapter

    assert hashlib.sha256(Path(runs.__file__).read_bytes()).hexdigest() == (
        "459d1745759108b0b07775d04682a6b166cee72559fdffb749f5166135874132"
    )
    token = "isolated-event-window-probe-token"
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": token}))
    stream = runs._RunStream()
    assert stream.BACKLOG_LIMIT == 1000
    for i in range(1100):
        stream.put_nowait({"event": "message.delta", "run_id": "window", "delta": str(i)})
    stream.put_nowait({"event": "run.completed", "run_id": "window"})
    stream.put_nowait(None)
    adapter._run_streams["window"] = stream
    adapter._run_owners["window"] = adapter._run_idempotency_scope(SimpleNamespace(headers={}))
    app = web.Application()
    app.router.add_get("/v1/runs/{run_id}/events", adapter._handle_run_events)
    cases = []
    async with TestClient(TestServer(app)) as client:
        for cursor, first, truncated in [
            (None, 102, True),
            ("0", 102, True),
            ("101", 102, False),
            ("1099", 1100, False),
            ("1100", None, False),
            ("9999", None, False),
            ("invalid", 102, True),
        ]:
            headers = {"Authorization": "Bearer " + token}
            if cursor is not None:
                headers["Last-Event-ID"] = cursor
            response = await client.get("/v1/runs/window/events", headers=headers)
            assert response.status == 200
            raw = await response.text()
            events = [
                json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")
            ]
            gaps = [e for e in events if e["event"] == "replay.truncated"]
            assert bool(gaps) == truncated
            if gaps:
                assert gaps[0]["oldest_retained_seq"] == 102
                assert "seq" not in gaps[0]
            seqs = [e["seq"] for e in events if "seq" in e]
            assert seqs == (list(range(first, 1101)) if first is not None else [])
            assert ": stream closed" in raw
            assert not stream.subscribers
            cases.append(
                {"cursor": cursor, "first_seq": first, "truncated": truncated, "events": len(seqs)}
            )
        denied = await client.get("/v1/runs/window/events")
        assert denied.status == 401
    # Saturate an attached queue deterministically; this is a stream component check,
    # not a claim that the HTTP socket write timeout was exercised.
    live = runs._RunStream()
    queue, replay = live.attach()
    assert not replay
    for i in range(257):
        live.put_nowait({"event": "message.delta", "delta": str(i)})
    assert queue not in live.subscribers
    assert queue.qsize() == 1
    seq, event = queue.get_nowait()
    assert seq == 256 and event is runs._RUN_STREAM_SUBSCRIBER_OVERFLOW
    recovered, replay = live.attach(0)
    assert [seq for seq, _ in replay] == list(range(1, 257))
    live.detach(recovered)
    print(
        json.dumps(
            {
                "seeded_event_http_cases": cases,
                "anonymous_status": 401,
                "subscriber_overflow_detached": True,
                "overflow_replay_events": len(replay),
                "external_model_requests": 0,
                "network": "none",
                "qualification_status": "UNVERIFIED",
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
