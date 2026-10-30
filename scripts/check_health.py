"""Probe the public BFF readiness endpoint; nonzero exit is suitable for a monitor/cron runner."""

import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url: str, mode: str) -> dict:
    started = time.monotonic()
    try:
        try:
            response = build_opener(NoRedirect).open(url, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(8193)
            if len(raw) > 8192:
                raise ValueError("Probe response too large")
            body = json.loads(raw)
            ready = (
                response.status == 200
                and body.get("status") == "ready"
                and body.get("service") == "yta-bff"
                and body.get("data_mode") == mode
            )
        reason = "ready" if ready else "dependency_not_ready"
    except Exception:
        ready, reason = False, "probe_failed"
    # Do not echo arbitrary server bodies, URLs, exception messages, or credentials.
    return {
        "status": "ready" if ready else "not_ready",
        "reason": reason,
        "expected_mode": mode,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:3000/api/yta/v1/ready")
    parser.add_argument("--expect-mode", choices=["REPLAY", "LIVE_RESEARCH"], default="REPLAY")
    args = parser.parse_args()
    result = probe(args.url, args.expect_mode)
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "ready" else 1)


if __name__ == "__main__":
    main()
