"""S09b deployment driver for the SG target host.

Establishes /opt/youwei with two isolated environments (acceptance and
production), generates/reuses per-environment secrets, runs the Alembic
migration via a one-shot container on the internal network, and drives
``docker compose``.

Design (project owner 2026-10-02):

- /opt/youwei/{production,acceptance}/ each hold a compose file, the
  postgres init script, and a 0600 .env. The two environments use
  distinct compose projects, volumes, networks, credentials and local
  ports.
- Secrets (PG owner/migrate/app passwords, admin bootstrap key,
  capability secret) are generated once per environment and reused; this
  script never overwrites an existing secret. Secret files are 0600 and
  the secrets directory is 0700; nothing is printed to stdout or logged.
- API/Worker connect as ``youwei_app`` (DML only); the migration step
  alone uses ``youwei_migrate`` (DDL), via a one-shot Core container on
  the ``core`` network (the compose does not publish the PG port).
- Images: production pins registry digests. Acceptance may use local
  image tags with ``pull_policy: never`` so it runs fully offline while
  GHCR push credentials are not configured.

This script runs on the target host (sg-prod) and shells out to the
local ``docker`` CLI. It is not a CI artifact.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(os.environ.get("YOUWEI_DEPLOY_BASE", "/opt/youwei"))
SECRETS_DIR = BASE / "secrets"
REPO = Path(os.environ.get("YOUWEI_DEPLOY_REPO", "/root/youwei-trading-agent"))

# Per-environment secret keys -> compose env var names.
SECRET_KEYS = [
    "YOUWEI_POSTGRES_OWNER_PASSWORD",
    "YOUWEI_POSTGRES_MIGRATE_PASSWORD",
    "YOUWEI_POSTGRES_APP_PASSWORD",
    "YOUWEI_ADMIN_API_KEY",
    "YOUWEI_CAPABILITY_SECRET",
]

# Local image tag used for offline acceptance (until GHCR push is restored).
LOCAL_CORE_TAG = "ghcr.io/youweichen0208/youwei-core:phase1a-s09b"


class DeployError(RuntimeError):
    pass


# Secrets known to this process; _sh redacts them from any command/output
# it ever prints, so a failed migration cannot leak a DB password.
_KNOWN_SECRETS: list[str] = []


def _register_secret(value: str) -> None:
    if value:
        _KNOWN_SECRETS.append(value)


def _redact(text: str) -> str:
    out = text
    for s in _KNOWN_SECRETS:
        out = out.replace(s, "<redacted>")
    return out


def _sh(cmd: list[str], *, env: dict[str, str] | None = None, check: bool = True,
        cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, text=True, capture_output=True, env=env, cwd=cwd)
    if check and result.returncode != 0:
        raise DeployError(
            f"command failed ({result.returncode}): {_redact(' '.join(cmd))}\n"
            f"stdout: {_redact(result.stdout[-2000:])}\nstderr: {_redact(result.stderr[-2000:])}"
        )
    return result


# --- secrets ----------------------------------------------------------------


def _gen() -> str:
    return secrets.token_hex(32)


def env_secret_file(env: str) -> Path:
    return SECRETS_DIR / f"{env}.env"


def ensure_secrets(env: str) -> Path:
    """Create/reuse the per-environment secret file. Never overwrites."""
    SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)
    path = env_secret_file(env)
    if path.exists():
        os.chmod(path, 0o600)
        return path
    lines = [f"{key}={_gen()}" for key in SECRET_KEYS]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return path


def read_secrets(env: str) -> dict[str, str]:
    path = env_secret_file(env)
    if not path.exists():
        raise DeployError(f"secrets for {env!r} missing; run init first")
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
            _register_secret(v)
    return out


# --- directory + compose rendering ----------------------------------------


def compose_file(env: str) -> Path:
    return BASE / env / "compose.json"


def project_name(env: str) -> str:
    return f"youwei-{env}"


def _load_production_compose() -> dict:
    src = REPO / "infra" / "compose" / "production.json"
    return json.loads(src.read_text())


def _fix_postgres_mount(compose: dict) -> None:
    """The repository compose uses ``../postgres/init`` (relative to
    infra/compose/). In the deploy directory the compose file sits next to
    ``postgres/init``, so the mount must be ``./postgres/init``."""
    for vol in compose["services"]["postgres"].get("volumes", []):
        if isinstance(vol, str) and vol.startswith("../postgres/init"):
            compose["services"]["postgres"]["volumes"].remove(vol)
            compose["services"]["postgres"]["volumes"].append(
                "./postgres/init:/docker-entrypoint-initdb.d:ro"
            )
            return


def init_dirs(env: str) -> None:
    """Initialize ONLY the selected environment. Existing compose files are
    never silently overwritten: if a file already exists and differs from
    what would be written, raise instead of clobbering the other
    environment's running configuration."""
    d = BASE / env
    d.mkdir(mode=0o755, parents=True, exist_ok=True)
    init_dir = d / "postgres" / "init"
    init_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    src = REPO / "infra" / "postgres" / "init" / "01-roles.sh"
    _write_if_different(init_dir / "01-roles.sh", src.read_text(), 0o755)

    if env == "production":
        prod = _load_production_compose()
        _fix_postgres_mount(prod)
        _write_if_different(compose_file(env), json.dumps(prod, indent=2) + "\n")
    else:
        prod = _load_production_compose()
        _fix_postgres_mount(prod)
        acc = render_acceptance_compose(prod)
        _write_if_different(compose_file(env), json.dumps(acc, indent=2) + "\n")


def _write_if_different(path: Path, content: str, mode: int | None = None) -> None:
    if path.exists():
        existing = path.read_text()
        if existing != content:
            raise DeployError(
                f"{path} already exists with different content; refusing to "
                f"overwrite a possibly-running environment. Review and remove "
                f"it explicitly if you intend to replace it."
            )
        if mode is not None:
            os.chmod(path, mode)
        return
    path.write_text(content)
    if mode is not None:
        os.chmod(path, mode)


def render_acceptance_compose(prod: dict) -> dict:
    """Acceptance compose: local core image (offline), a mock Tiingo
    sidecar, and a distinct local API port."""
    acc = copy.deepcopy(prod)
    acc["name"] = "youwei-acceptance"
    services = acc["services"]

    for name in ("core-api", "core-worker"):
        svc = services[name]
        svc["image"] = LOCAL_CORE_TAG
        svc["pull_policy"] = "never"

    worker = services["core-worker"]
    worker["environment"]["YOUWEI_TIINGO_BASE_URL"] = "http://mock-tiingo:8080"
    worker["environment"]["YOUWEI_TIINGO_TOKEN"] = "acceptance-synthetic"

    services["mock-tiingo"] = {
        "image": LOCAL_CORE_TAG,
        "pull_policy": "never",
        "command": ["python", "/mock/s09b_mock_tiingo.py", "--host", "0.0.0.0", "--port", "8080"],
        "volumes": [
            {"type": "bind", "source": str(REPO / "ops" / "s09b_mock_tiingo.py"),
             "target": "/mock/s09b_mock_tiingo.py", "read_only": True}
        ],
        "networks": ["core"],
        "restart": "unless-stopped",
        "read_only": True,
        "tmpfs": ["/tmp:rw,nosuid,noexec,size=32m"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "cpus": "0.25",
        "mem_limit": "128m",
        "pids_limit": 64,
    }

    services["core-api"]["ports"] = ["127.0.0.1:8001:8000"]

    return acc


# --- compose / docker helpers ----------------------------------------------


def compose_env(env: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    merged = os.environ.copy()
    for k, v in read_secrets(env).items():
        merged[k] = v
    # Real vendor credentials (Tiingo token) live in the repo .env, not in
    # the generated secrets. Merge them so compose interpolation succeeds;
    # register them for redaction too.
    repo_env = REPO / ".env"
    if repo_env.exists():
        for line in repo_env.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                merged.setdefault(k, v)
                _register_secret(v)
    # Collection stays off until explicitly configured (candidate import).
    merged.setdefault("YOUWEI_COLLECT_RELEASE_ID", "")
    merged.setdefault("YOUWEI_COLLECT_TENANT_ID", "")
    for k, v in (extra or {}).items():
        merged[k] = v
    return merged


def _dc(env: str, *args: str, check: bool = True,
        extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return _sh(
        ["docker", "compose", "-p", project_name(env), "-f", str(compose_file(env)), *args],
        env=compose_env(env, extra_env),
        check=check,
        cwd=BASE / env,
    )


def _image_for(env: str) -> str:
    # Migration/import run against the locally-built, already-accepted
    # image (tag LOCAL_CORE_TAG) for BOTH environments. The production
    # compose pins a registry digest that is only used once GHCR push is
    # restored; until then the digest reference cannot be pulled and the
    # local image is the accepted artifact for database preparation.
    return LOCAL_CORE_TAG


# --- commands ---------------------------------------------------------------


def _wait_pg(env: str) -> None:
    app_pw = read_secrets(env)["YOUWEI_POSTGRES_APP_PASSWORD"]
    for _ in range(90):
        r = _dc(env, "exec", "-T", "-e", f"PGPASSWORD={app_pw}",
                "postgres", "psql", "-U", "youwei_app", "-d", "youwei",
                "-Atc", "select 1", check=False)
        if r.returncode == 0:
            return
        time.sleep(1)
    raise DeployError("postgres did not become ready")


def migrate(env: str) -> None:
    _dc(env, "up", "-d", "postgres")
    _wait_pg(env)
    migrate_pw = read_secrets(env)["YOUWEI_POSTGRES_MIGRATE_PASSWORD"]
    migrate_url = f"postgresql+asyncpg://youwei_migrate:{migrate_pw}@postgres:5432/youwei"
    network = f"{project_name(env)}_core"
    _sh(
        ["docker", "run", "--rm", "--network", network,
         "-e", f"YOUWEI_DATABASE_URL={migrate_url}",
         _image_for(env), "alembic", "upgrade", "head"],
        check=True,
    )


def up(env: str) -> None:
    _dc(env, "up", "-d")


def down(env: str) -> None:
    _dc(env, "down")


def status(env: str) -> None:
    r = _dc(env, "ps", check=False)
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", choices=("acceptance", "production"), default="acceptance")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("migrate")
    sub.add_parser("up")
    sub.add_parser("down")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    try:
        if args.command == "init":
            init_dirs(args.env)
            ensure_secrets(args.env)
            print(f"initialized {args.env} under {BASE}")
        elif args.command == "migrate":
            ensure_secrets(args.env)
            migrate(args.env)
            print(f"migrated {args.env}")
        elif args.command == "up":
            ensure_secrets(args.env)
            up(args.env)
            print(f"started {args.env}")
        elif args.command == "down":
            down(args.env)
            print(f"stopped {args.env}")
        elif args.command == "status":
            status(args.env)
    except DeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
