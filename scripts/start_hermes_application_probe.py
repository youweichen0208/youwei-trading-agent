"""Prepare and start one isolated real-pi application run; never automatically retry it."""

import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path

import psycopg
from prepare_pi_model_probe import selected_route
from psycopg import sql
from yta_alpha.migrate import migrate

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:67f82b05c5c083f2795cff9c361b4f5c464116fcc4301671709376e6f64e3226"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    parser.add_argument(
        "--configured", action="store_true", help="Use sealed BFF main and Web images"
    )
    parser.add_argument(
        "--massive", action="store_true", help="Personal local LIVE stack on port 3301"
    )
    args = parser.parse_args()
    if args.massive:
        args.configured = True
    state = ROOT / (
        ".local/massive-application-state.json"
        if args.massive
        else ".local/hermes-configured-state.json"
        if args.configured
        else ".local/hermes-browser-state.json"
        if args.browser
        else ".local/hermes-application-state.json"
    )
    if state.exists():
        raise RuntimeError("Existing application probe state must be reconciled first")
    route = selected_route(Path.home() / ".pi/agent")
    name = "yta-pi-app-" + secrets.token_hex(6)
    volume = name + "-data"
    database = "yta_pi_app_" + secrets.token_hex(6) + "_test"
    if args.massive:
        database = json.loads((ROOT / ".local/massive-live-state.json").read_text())["database"]
    env_path = ROOT / ".local" / (name + ".env")
    env = {
        key: secrets.token_hex(32)
        for key in (
            "YTA_HERMES_PROBE_TOKEN",
            "YTA_FINANCE_SERVICE_TOKEN",
            "YTA_FINANCE_ALPHA_TOKEN",
            "YTA_FINANCE_MCP_TOKEN",
        )
    }
    env.update(
        YTA_HERMES_PROBE_IMAGE=IMAGE,
        YTA_PI_MODEL=route["model"],
        YTA_PI_APPLICATION_VOLUME=volume,
        YTA_PI_APPLICATION_DATABASE=database,
    )
    if args.massive:
        env["YTA_LOCAL_DATABASE_NETWORK"] = name + "-database"
        env["YTA_LOCAL_GIT_SHA"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    for key, tag in [
        ("YTA_FINANCE_ALPHA_IMAGE", "yta-alpha:mvp"),
        ("YTA_FINANCE_TOOLS_IMAGE", "yta-finance-tools:mvp"),
        ("YTA_APPLICATION_IMAGE", "yta-bff:mvp" if args.configured else "yta-bff:local"),
        ("YTA_WEB_IMAGE", "yta-web:mvp"),
    ]:
        if args.massive:
            tag = tag.replace(":mvp", ":massive")
        env[key] = subprocess.check_output(
            ["docker", "image", "inspect", tag, "--format", "{{.Id}}"], text=True
        ).strip()
    with os.fdopen(os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        for key, value in env.items():
            if "\n" in value or "\r" in value:
                raise ValueError("Invalid environment value")
            f.write(f"{key}={value}\n")
    compose = [
        "docker",
        "compose",
        "-p",
        name,
        "--env-file",
        str(env_path),
        "-f",
        str(ROOT / "infra/compose/compose.hermes-pi-probe.yaml"),
        "-f",
        str(ROOT / "infra/compose/compose.hermes-application-probe.yaml"),
    ]
    if args.configured:
        compose += ["-f", str(ROOT / "infra/compose/compose.hermes-configured-probe.yaml")]
    elif args.browser:
        compose += ["-f", str(ROOT / "infra/compose/compose.hermes-browser-probe.yaml")]
    if args.massive:
        compose += ["-f", str(ROOT / "infra/compose/compose.massive-local.yaml")]
    with os.fdopen(os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump(
            {
                "project": name,
                "database": database,
                "volume": volume,
                "compose": compose,
                "provider": route["source_provider"],
                "model": route["model"],
            },
            f,
        )
    admin_url = "postgresql://yta_owner:local-yta-owner@127.0.0.1:54329/"
    if not args.massive:
        with psycopg.connect(admin_url + "yta_test", autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    migrate(admin_url + database, ROOT / "infra/migrations")
    with psycopg.connect(admin_url + database) as c:
        c.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
        c.execute("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
        c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    bootstrap = (
        'import os,sys; f=os.fdopen(os.open("/opt/data/pi-route.json",'
        'os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"wb"); '
        "f.write(sys.stdin.buffer.read()); f.close()"
    )
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--user",
            "10000:10000",
            "-v",
            volume + ":/opt/data",
            "--entrypoint",
            "python",
            IMAGE,
            "-c",
            bootstrap,
        ],
        input=json.dumps(route).encode(),
        check=True,
        capture_output=True,
    )
    if args.massive:
        network = env["YTA_LOCAL_DATABASE_NETWORK"]
        postgres = subprocess.check_output(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.project=yta-dev",
                "--filter",
                "label=com.docker.compose.service=postgres",
                "--format",
                "{{.ID}}",
            ],
            text=True,
        ).split()
        if len(postgres) != 1:
            raise RuntimeError("Expected one local PostgreSQL container")
        subprocess.run(
            ["docker", "network", "create", "--internal", network], check=True, capture_output=True
        )
        subprocess.run(
            ["docker", "network", "connect", "--alias", "postgres", network, postgres[0]],
            check=True,
            capture_output=True,
        )
    subprocess.run(compose + ["up", "-d"], check=True, capture_output=True)
    print(
        json.dumps(
            {
                "project": name,
                "database": database,
                "provider": route["source_provider"],
                "model": route["model"],
                "state_file": str(state),
                "started": True,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Application probe setup failed; inspect saved state, never blindly retry"
        ) from None
