"""agent-runtime entrypoint (S07m).

The agent-runtime is an independent Python 3.14 process with no database or
supplier credentials. The Runner launches it as a short-lived container for
one ``llm_adjusted`` research turn:

    youwei-agent-runtime research-once --public-keys-env YOUWEI_RESEARCH_PUBLIC_KEYS

The request is a single JSON object on stdin:

    {
      "capability_token": "<Controller-signed ywr_ Ed25519 grant>",
      "evidence": { ... FrozenEvidence ... },
      "config": { "base_url": "...", "api_key": "...", "model": "..." }
    }

and the result is a single JSON object on stdout:

    {"ok": true, "proposal": { ... ResearchProposal ... }}
    {"ok": false, "error": "<Type>: <message>"}

The research container holds only the PUBLIC key (``kid -> PEM``, read from
the environment) and VERIFIES the Controller's grant — it can never sign one.
The gateway endpoint/key are injected by the Runner, never read from the
request. stdin/stdout are byte-capped; a result over the cap fails rather than
truncating JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from youwei_agent_runtime.invoke import (
    InvocationError,
    decode_request,
    encode_error,
    honor_request,
)
from youwei_agent_runtime.runtime import (
    ResearchConfig,
    _register_research_tools,
    run_research,
)

# Byte caps for the single-line JSON wire contract (S07m). The research
# container is a short-lived subprocess; these bound stdin and stdout so a
# runaway or oversized result fails cleanly instead of truncating JSON.
MAX_STDIN_BYTES = 16 * 1024 * 1024   # frozen evidence bundle + config
MAX_STDOUT_BYTES = 1_000_000          # one result object (proposal + usage)


def _public_keys(args) -> dict[str, str]:
    env = args.public_keys_env
    raw = os.environ.get(env, "")
    if not raw:
        raise SystemExit(f"research-once requires {env} to be set")
    try:
        keys = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{env} is not valid JSON") from exc
    if not isinstance(keys, dict) or not keys:
        raise SystemExit(f"{env} must be a non-empty JSON object of {{kid: pem}}")
    return keys


def _read_stdin_bounded() -> str:
    """Read stdin up to MAX_STDIN_BYTES; over the cap fails the invocation
    rather than silently truncating the request JSON."""
    data = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(data) > MAX_STDIN_BYTES:
        raise InvocationError("request exceeds stdin byte cap")
    return data.decode("utf-8", errors="replace")


async def _research_once(args) -> int:
    public_keys = _public_keys(args)
    # Hermes writes its init banner / API-call progress / conversation log via
    # bare print() to stdout (agent/agent_init.py, turn_facade.py). The
    # research-once wire contract is a single JSON object on stdout, so divert
    # stdout to stderr for the duration of the turn and restore it only to
    # emit the result. This keeps the response parseable by the Controller.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        # Register the platform research tools into Hermes's global registry
        # once per process, before any turn. Hermes has no per-instance tool
        # injection; tools live in the global registry and are selected via
        # enabled_toolsets (see tools.py / runtime._register_research_tools).
        _register_research_tools()
        raw = _read_stdin_bounded()
        payload = decode_request(raw)
        result = await honor_request(
            payload,
            public_keys=public_keys,
            run_research=run_research,
            config_factory=ResearchConfig,
        )
    except InvocationError as exc:
        real_stdout.write(encode_error(exc) + "\n")
        return 2
    except Exception as exc:  # noqa: BLE001 — surface any research failure
        real_stdout.write(encode_error(exc) + "\n")
        return 1
    finally:
        sys.stdout = real_stdout
    # A result over the stdout cap is a failure, never a truncated JSON blob.
    if len(result.encode("utf-8")) > MAX_STDOUT_BYTES:
        real_stdout.write(encode_error(
            InvocationError("research result exceeds stdout byte cap")
        ) + "\n")
        return 1
    real_stdout.write(result + "\n")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="youwei-agent-runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("research-once", help="run one research turn over stdin/stdout")
    once.add_argument(
        "--public-keys-env",
        default="YOUWEI_RESEARCH_PUBLIC_KEYS",
        help="env var holding the JSON map of trusted research public keys (kid -> PEM)",
    )
    once.set_defaults(handler=_research_once)

    args = parser.parse_args(argv)
    code = asyncio.run(args.handler(args))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
