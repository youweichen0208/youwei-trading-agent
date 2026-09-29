"""agent-runtime entrypoint (S07h).

The agent-runtime is an independent Python 3.14 process with no database or
supplier credentials. The Controller launches it as a short-lived subprocess
for one ``llm_adjusted`` research turn:

    youwei-agent-runtime research-once \
        --capability-secret-env YOUWEI_CAPABILITY_SECRET

The request is a single JSON object on stdin:

    {
      "capability_token": "<Controller-signed ywc_ token>",
      "evidence": { ... FrozenEvidence ... },
      "config": { "base_url": "...", "api_key": "...", "model": "..." }
    }

and the result is a single JSON object on stdout:

    {"ok": true, "proposal": { ... ResearchProposal ... }}
    {"ok": false, "error": "<Type>: <message>"}

The capability secret is shared with the Controller's signing secret; it is
read from the environment so it never appears on the command line or in the
request body. The capability token is verified before any gateway call.
"""

from __future__ import annotations

import argparse
import asyncio
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


def _capability_secret(args) -> str:
    env = args.capability_secret_env
    secret = os.environ.get(env, "")
    if not secret:
        raise SystemExit(f"research-once requires {env} to be set")
    return secret


async def _research_once(args) -> int:
    secret = _capability_secret(args)
    # Register the platform research tools into Hermes's global registry once
    # per process, before any turn. Hermes has no per-instance tool injection;
    # tools live in the global registry and are selected via enabled_toolsets
    # (see tools.py / runtime._register_research_tools).
    _register_research_tools()
    raw = sys.stdin.read()
    try:
        payload = decode_request(raw)
        result = await honor_request(
            payload,
            capability_secret=secret,
            run_research=run_research,
            config_factory=ResearchConfig,
        )
    except InvocationError as exc:
        sys.stdout.write(encode_error(exc) + "\n")
        return 2
    except Exception as exc:  # noqa: BLE001 — surface any research failure
        sys.stdout.write(encode_error(exc) + "\n")
        return 1
    sys.stdout.write(result + "\n")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="youwei-agent-runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("research-once", help="run one research turn over stdin/stdout")
    once.add_argument(
        "--capability-secret-env",
        default="YOUWEI_CAPABILITY_SECRET",
        help="env var holding the capability verification secret",
    )
    once.set_defaults(handler=_research_once)

    args = parser.parse_args(argv)
    code = asyncio.run(args.handler(args))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
