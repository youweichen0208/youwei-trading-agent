"""Adversarial synthetic model for locked Gateway tool rejection; no real credentials."""

import json
import os
import sys
from pathlib import Path

import probe_hermes_gateway_context as context

MARKER = "/tmp/yta-forbidden-tool-effect"
CALLS = {
    "terminal": {"command": "touch " + MARKER},
    "read_file": {"path": "/opt/data/profiles/alpha-orchestrator/SOUL.md"},
    "write_file": {"path": MARKER, "content": "unexpected execution"},
    "delegate_task": {"task": "Write a file at " + MARKER},
}


def answer(body):
    # Retain the existing identity, foreign-context and advertised-catalog assertions.
    final, finish = context.answer(body)
    results = [m for m in body["messages"] if m.get("role") == "tool"]
    if not results:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_forbidden_" + name,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for name, args in CALLS.items()
            ],
        }, "tool_calls"
    by_id = {m["tool_call_id"]: m["content"] for m in results}
    assert len(results) == len(CALLS)
    for name in CALLS:
        expected = f"Tool '{name}' does not exist. Available tools: "
        if by_id["call_forbidden_" + name].strip() != expected.strip():
            print(json.dumps({"rejection": by_id["call_forbidden_" + name]}), flush=True)
            raise ValueError("Unexpected rejection format")
    return final, finish


def main():
    if os.environ.get("YTA_FORBIDDEN_TOOL_PROBE") != "1":
        raise RuntimeError("Explicit isolated probe required")
    phase = sys.argv[1]
    if phase == "bootstrap":
        assert not Path(MARKER).exists()
        context.bootstrap()
    elif phase == "model":
        import hermes_model_fixture as fixture

        fixture.answer = answer
        fixture.main()
    elif phase == "probe":
        result = context.probe(sys.argv[2] if len(sys.argv) > 2 else "original")
        assert not Path(MARKER).exists()
        print(
            json.dumps(
                {
                    **result,
                    "forbidden_tools": list(CALLS),
                    "explicit_rejections": result["gateway_runs"] * len(CALLS),
                    "filesystem_side_effect_absent": True,
                    "scope": "Actual Gateway rejects an all-forbidden batch with empty catalog; "
                    "not finance mixed-batch qualification or OS sandbox",
                }
            )
        )
    else:
        raise ValueError("Unknown phase")


if __name__ == "__main__":
    main()
