"""Isolated Gateway context probe: synthetic model, two profiles, no external services."""

import argparse
import json
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MARKERS = {"left": "YTA_SOUL_LEFT_647120", "right": "YTA_SOUL_RIGHT_982310"}
SKILL_MARKER = "YTA_SKILL_MUST_NOT_AUTOLOAD_348215"
PROFILES = {"left": ("default", ""), "right": ("alpha-orchestrator", "/p/alpha-orchestrator")}


def answer(body):
    messages = body.get("messages", [])
    user = next(m["content"] for m in reversed(messages) if m.get("role") == "user")
    side = next(side for side in MARKERS if user == f"Check isolated context for {side}.")
    system = json.dumps([m for m in messages if m.get("role") == "system"])
    all_messages = json.dumps(messages)
    if MARKERS[side] not in system:
        raise ValueError("Missing own identity")
    if any(marker in all_messages for name, marker in MARKERS.items() if name != side):
        raise ValueError("Foreign identity")
    if SKILL_MARKER in all_messages or body.get("tools"):
        raise ValueError("Unexpected skill or tool")
    return {"role": "assistant", "content": "CONTEXT_OK_" + side}, "stop"


def bootstrap():
    root = Path("/opt/data")
    for side, (name, _) in PROFILES.items():
        home = root if name == "default" else root / "profiles" / name
        skill = home / "skills" / "blocked-context"
        skill.mkdir(parents=True, exist_ok=True)
        (home / "SOUL.md").write_text("Synthetic identity: " + MARKERS[side] + "\n")
        (skill / "SKILL.md").write_text(
            "---\nname: blocked-context\ndescription: Synthetic context probe\n---\n" + SKILL_MARKER
        )
        config = {
            "gateway": {"multiplex_profiles": True},
            "model": {
                "default": "yta-protocol-fixture",
                "provider": "custom",
                "base_url": "http://model-fixture:8300/v1",
                "api_mode": "chat_completions",
            },
            "platform_toolsets": {"api_server": ["no_mcp"], "cli": ["no_mcp"]},
            "mcp_servers": {},
            "tools": {"tool_search": {"enabled": "off"}},
            "skills": {"external_dirs": [], "auto_load": ["blocked-context"]},
            "agent": {"disabled_toolsets": ["x_search"], "max_turns": 2, "gateway_timeout": 30},
            "memory": {"memory_enabled": False, "user_profile_enabled": False},
        }
        (home / "config.yaml").write_text(json.dumps(config))
        (home / ".env").write_text(
            "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=synthetic-context-"
            + side
            + "\nOPENAI_API_KEY=synthetic-context-model\n"
            + "OPENAI_BASE_URL=http://model-fixture:8300/v1\n"
        )
        for path in (home / "config.yaml", home / ".env"):
            path.chmod(0o600)
    os.execv(
        "/opt/hermes/bin/hermes",
        ["hermes", "gateway", "run", "--no-supervise", "--external-supervisor"],
    )


def probe(revision):
    import httpx
    from probe_hermes_failure import request, require

    def run(index):
        side = "left" if index % 2 == 0 else "right"
        prefix = PROFILES[side][1]
        suffix = "" if revision == "original" else "-paired-env"
        path = Path(f"/opt/data/context-run-{index}{suffix}.json")
        created = False
        if not path.exists():
            receipt = {"key": secrets.token_hex(16)}
            with path.open("x") as file:
                path.chmod(0o600)
                json.dump(receipt, file)
                file.flush()
                os.fsync(file.fileno())
            created = True
        receipt = json.loads(path.read_text())
        headers = {
            "Authorization": "Bearer synthetic-context-" + side,
            "Idempotency-Key": receipt["key"],
            "X-Hermes-Session-Key": "yta-context-" + receipt["key"],
        }
        with httpx.Client(timeout=5, trust_env=False, headers=headers) as client:
            if "run_id" not in receipt:
                require(created, "unknown_admission_no_resubmit")
                status, raw, _ = request(
                    client,
                    "POST",
                    prefix + "/v1/runs",
                    body={"input": f"Check isolated context for {side}."},
                )
                require(status == 202, "admission")
                receipt["run_id"] = json.loads(raw)["run_id"]
                pending = path.with_suffix(".pending")
                with pending.open("x") as file:
                    pending.chmod(0o600)
                    json.dump(receipt, file)
                    file.flush()
                    os.fsync(file.fileno())
                pending.replace(path)
            deadline = time.monotonic() + 40
            while True:
                status, raw, _ = request(client, "GET", prefix + "/v1/runs/" + receipt["run_id"])
                require(status == 200, "status")
                outcome = json.loads(raw)
                if outcome["status"] in ("completed", "failed", "cancelled", "unknown"):
                    break
                require(time.monotonic() < deadline, "observation_deadline")
                time.sleep(0.2)
            require(outcome["status"] == "completed", "completed")
            require(outcome["output"] == "CONTEXT_OK_" + side, "context_validated_by_endpoint")
        return side

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(8)))
    return {
        "gateway_runs": len(results),
        "profiles": 2,
        "concurrent_clients": 2,
        "own_soul_in_model_system_messages": True,
        "foreign_soul_absent": True,
        "configured_skill_not_auto_loaded": True,
        "advertised_tools_empty": True,
        "synthetic_model": True,
        "external_model_requests": 0,
        "qualification_status": "UNVERIFIED",
        "scope": "Actual Gateway prompt assembly; not OS isolation or real inference",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["bootstrap", "model", "probe"])
    parser.add_argument("--revision", choices=["original", "paired-env"], default="original")
    args = parser.parse_args()
    if os.environ.get("YTA_CONTEXT_GATEWAY_PROBE") != "1":
        raise RuntimeError("Explicit isolated probe required")
    if args.phase == "bootstrap":
        bootstrap()
    elif args.phase == "model":
        import hermes_model_fixture as fixture

        fixture.answer = answer
        fixture.main()
    else:
        print(json.dumps(probe(args.revision)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": "GATEWAY_CONTEXT_PROBE_FAILED", "type": type(error).__name__}))
        raise SystemExit(1) from None
