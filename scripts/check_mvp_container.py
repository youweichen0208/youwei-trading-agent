"""Run sealed candidate images against a fresh local test DB; no source mounts."""

import json
import os
import secrets
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def main():
    state = ROOT / ".local/mvp-container-state.json"
    if state.exists():
        raise RuntimeError("Reconcile existing candidate run before starting another")
    candidate = json.loads((ROOT / "infra/verification/mvp-candidate-observation.json").read_text())
    images = candidate["images"]
    name = "yta-candidate-" + secrets.token_hex(6)
    database = "yta_candidate_" + secrets.token_hex(12) + "_test"
    token = secrets.token_hex(32)
    override = ROOT / ".local" / f"{name}.json"
    env = {
        "YTA_DATABASE_URL": f"postgresql://yta_app:local-yta-app@postgres:5432/{database}",
        "YTA_ALPHA_TOKEN": token,
    }
    services = {
        service: {
            "image": images[image],
            "pull_policy": "never",
            **({"environment": env} if service != "yta-web" else {}),
        }
        for service, image in [("alpha-engine", "alpha"), ("yta-bff", "bff"), ("yta-web", "web")]
    }
    with os.fdopen(os.open(override, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump({"services": services}, f)
    compose = [
        "docker",
        "compose",
        "-p",
        name,
        "-f",
        str(ROOT / "infra/compose/compose.app-local.yaml"),
        "-f",
        str(override),
    ]
    with os.fdopen(os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump({"database": database, "project": name, "compose": compose}, f)
    local = "postgresql://yta_owner:local-yta-owner@127.0.0.1:54329/"
    with psycopg.connect(local + "postgres", autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    migration = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "yta-dev_default",
            "--read-only",
            "-e",
            "YTA_MIGRATION_DATABASE_URL",
            images["migrator"],
        ],
        env={
            **os.environ,
            "YTA_MIGRATION_DATABASE_URL": f"postgresql://yta_owner:local-yta-owner@postgres:5432/{database}",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    if migration.returncode:
        raise RuntimeError("Candidate migration failed")
    with psycopg.connect(local + database) as c:
        c.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
        c.execute("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
        c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
    try:
        subprocess.run(
            compose + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "90"],
            check=True,
            capture_output=True,
            timeout=120,
        )
        inspections = {}
        for service in services:
            identity = subprocess.check_output(compose + ["ps", "-q", service], text=True).strip()
            info = json.loads(subprocess.check_output(["docker", "inspect", identity], text=True))[
                0
            ]
            assert not any(m["Type"] == "bind" for m in info["Mounts"])
            assert info["HostConfig"]["ReadonlyRootfs"] is True
            if service != "yta-web":
                assert not any(info["NetworkSettings"]["Ports"].values())
            inspections[service] = {
                "image": info["Image"],
                "no_source_mounts": True,
                "read_only_root": True,
                "health": info["State"]["Health"]["Status"],
            }
        output = subprocess.run(
            ["node", "scripts/probe_mvp_container_browser.mjs"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        (ROOT / ".local/mvp-container-browser-error.log").write_text(output.stderr)
        if output.returncode:
            raise RuntimeError("Candidate browser check failed; inspect private log")
        result = {
            "images": inspections,
            "browser": json.loads(output.stdout),
            "database": database,
            "qualification": "LOCAL_REPLAY_ONLY",
            "migration_count": len(migration.stdout.splitlines()),
        }
        (ROOT / "infra/verification/mvp-container-observation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(result))
    finally:
        subprocess.run(compose + ["down"], check=True, capture_output=True, timeout=60)


if __name__ == "__main__":
    main()
