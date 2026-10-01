"""S09b acceptance mock Tiingo server (synthetic data loop).

Serves the Tiingo daily-prices endpoint shape that ``TiingoClient``
expects, returning deterministic synthetic bars. This lets the real
API -> Worker -> collect_tick -> TiingoClient -> ingest chain run
end-to-end against a fixed vendor response, without touching the real
Tiingo API or spending quota.

Endpoints:
    GET /daily/{TICKER}/prices?token=...&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
        -> JSON array of daily bars for that ticker.

Behaviour:
    * Any ticker returns the same synthetic bars for the requested window
      (one bar per weekday in [startDate, endDate]).
    * Ticker ``FAIL`` (case-insensitive) returns HTTP 500 to exercise the
      failure/retry path.
    * Ticker ``EMPTY`` returns HTTP 200 with an empty list (no rows).

This is acceptance tooling only; it is never wired into a production
compose. Run it directly (stdlib http.server, no deps).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


def _weekdays(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_bars(start: date, end: date) -> list[dict]:
    bars = []
    base = 100.0
    for i, d in enumerate(_weekdays(start, end)):
        base = round(base + 0.5 * (i + 1), 4)
        bars.append(
            {
                "date": d.isoformat() + "T00:00:00.000Z",
                "open": base - 0.25,
                "high": base + 0.50,
                "low": base - 0.50,
                "close": base,
                "volume": 100000 + i * 100,
                "adjClose": base,
                "divCash": 0.0,
                "splitFactor": 1.0,
            }
        )
    return bars


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        parsed = urlparse(self.path)
        ticker = parsed.path.strip("/").split("/")
        if len(ticker) != 3 or ticker[0] != "daily" or ticker[2] != "prices":
            self._send_json(404, {"error": "not found"})
            return
        symbol = ticker[1].upper()
        qs = {}
        for pair in parsed.query.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                qs[k] = v
        start = qs.get("startDate", "2026-01-01")
        end = qs.get("endDate", "2026-01-01")

        if symbol == "FAIL":
            self._send_json(500, {"error": "synthetic failure"})
            return
        if symbol == "EMPTY":
            self._send_json(200, [])
            return
        try:
            s = date.fromisoformat(start[:10])
            e = date.fromisoformat(end[:10])
        except ValueError:
            self._send_json(400, {"error": "bad date"})
            return
        self._send_json(200, synthetic_bars(s, e))

    def log_message(self, fmt, *args):  # keep the log quiet but visible
        import sys

        sys.stderr.write("mock-tiingo: %s\n" % (fmt % args))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    server = HTTPServer((args.host, args.port), Handler)
    print(f"mock-tiingo listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
