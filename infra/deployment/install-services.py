"""Install isolated systemd services after the in-place contract check passes."""

import json
import os
import pwd
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/opt/hermes-alpha-lab")
RELEASE = ROOT / "releases/20261107T0800Z"
NODE = ROOT / "runtime/node-v26.0.0-linux-x64/bin/node"
PRIVATE = ROOT / "private"
OWNER = json.loads((PRIVATE / "initial-owner.json").read_text())["ownerId"]
PREPARED = PRIVATE / "profiles" / OWNER
REGISTRY = PREPARED / "profiles.verified.json"
assert os.getuid() == 0 and REGISTRY.is_file(), "Actual Profile contract checks required"
os.umask(0o077)


def private_file(path, content):
    path.write_text(content)
    path.chmod(0o600)


for service in ("web", "worker", "tools"):
    user = "alpha-" + service
    try:
        pwd.getpwnam(user)
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--system",
                "--user-group",
                "--no-create-home",
                "--home-dir",
                "/var/lib/hermes-alpha-lab/" + service,
                "--shell",
                "/usr/sbin/nologin",
                user,
            ],
            check=True,
        )
    home = Path("/var/lib/hermes-alpha-lab") / service
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    shutil.chown(home, user=user, group=user)
Path("/var/lib/hermes-alpha-lab").chmod(0o755)
worker_home = Path("/var/lib/hermes-alpha-lab/worker")
(worker_home / "evidence").mkdir(exist_ok=True, mode=0o700)
shutil.chown(worker_home / "evidence", user="alpha-worker", group="alpha-worker")
entries = json.loads(REGISTRY.read_text())
for entry in entries:
    report = PREPARED / entry["evidenceFile"]
    target = worker_home / "evidence" / report.name
    private_file(target, report.read_text())
    shutil.chown(target, user="alpha-worker", group="alpha-worker")
private_file(worker_home / "registry.json", json.dumps(entries) + "\n")
shutil.chown(worker_home / "registry.json", user="alpha-worker", group="alpha-worker")
key_path = PRIVATE / "capability.env"
if not key_path.exists():
    private_file(key_path, "TOOL_CAPABILITY_KEY=" + secrets.token_hex(32) + "\n")
private_file(
    PRIVATE / "web.env", "NODE_ENV=production\nAPP_ORIGIN=https://trading.youwei-agent.com\n"
)
private_file(
    PRIVATE / "worker.env",
    "NODE_ENV=production\nHERMES_PROFILE_REGISTRY=/var/lib/hermes-alpha-lab/worker/registry.json\n",
)
private_file(
    PRIVATE / "tools.env",
    f"NODE_ENV=production\nTOOL_GATEWAY_HOST=172.17.0.1\nTOOL_GATEWAY_PORT=3410\nQUANT_PYTHON={RELEASE}/.venv/bin/python\n",
)
cache = RELEASE / "apps/web/.next/cache"
cache.mkdir(parents=True, exist_ok=True)
subprocess.run(["chown", "-R", "alpha-web:alpha-web", str(cache)], check=True)


def unit(name, user, command, envs, writable, after=""):
    content = f"""[Unit]
Description=Hermes Alpha Lab {name}
After=network-online.target docker.service {after}
Wants=network-online.target
Requires=docker.service
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User={user}
Group={user}
WorkingDirectory={RELEASE}
Environment=PATH={NODE.parent}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
"""
    content += "".join(f"EnvironmentFile={env}\n" for env in envs)
    content += f"""ExecStart={NODE} {command}
Restart=on-failure
RestartSec=5
TimeoutStopSec=90
KillSignal=SIGTERM
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={writable}
RestrictSUIDSGID=true
LimitNOFILE=8192
TasksMax=256
MemoryMax={"2G" if name == "web" else "1G"}

[Install]
WantedBy=multi-user.target
"""
    path = Path("/etc/systemd/system") / f"alpha-lab-{name}.service"
    path.write_text(content)
    path.chmod(0o644)


unit(
    "web",
    "alpha-web",
    "node_modules/next/dist/bin/next start apps/web -H 127.0.0.1 -p 3400",
    [PRIVATE / "database-services/web.env", PRIVATE / "web.env"],
    str(cache),
)
unit(
    "tools",
    "alpha-tools",
    "--import tsx apps/tools/main.ts",
    [
        PRIVATE / "database-services/tools.env",
        PRIVATE / "tools.env",
        PRIVATE / "provider/alpaca.env",
    ],
    "/var/lib/hermes-alpha-lab/tools",
)
unit(
    "worker",
    "alpha-worker",
    "--import tsx apps/worker/main.ts",
    [
        PRIVATE / "database-services/worker.env",
        PRIVATE / "worker.env",
        key_path,
        PREPARED / "worker.env",
    ],
    str(worker_home),
    "alpha-lab-tools.service",
)
unit(
    "supervisor",
    "root",
    f"--import tsx scripts/supervise-profiles.ts {REGISTRY} {PREPARED}",
    [PRIVATE / "database-services/worker.env", key_path, PREPARED / "worker.env"],
    str(PRIVATE / "profiles"),
)
unit(
    "preparer",
    "root",
    (
        f"--import tsx scripts/prepare-profiles-auto.ts {PRIVATE}/profile-settings.json "
        f"{PRIVATE}/profiles 19999"
    ),
    [PRIVATE / "database-services/worker.env"],
    str(PRIVATE / "profiles"),
)
subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(
    [
        "systemctl",
        "enable",
        "--now",
        *[f"alpha-lab-{s}.service" for s in ["tools", "supervisor", "worker", "preparer", "web"]],
    ],
    check=True,
)
print("Services installed; verify status and HTTP health before public cutover")
