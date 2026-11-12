"""Replace inventoried application sites; retain personal notes and TLS assets."""

import shutil
import subprocess
import sys
from pathlib import Path

kind = sys.argv[1]
assert kind in {"digitalocean", "aliyun"}
source = Path(__file__).parent / f"{kind}.nginx.conf"
if kind == "aliyun":
    shutil.copyfile(
        Path(__file__).parent / "alpha-lab-origin.nginx.conf",
        "/etc/nginx/snippets/alpha-lab-origin.conf",
    )
available = Path("/etc/nginx/sites-available")
enabled = Path("/etc/nginx/sites-enabled")
preserve = {"notes-webdav", "obsidian-webdav"} if kind == "digitalocean" else set()
aliases = (
    [("youwei-agent.com", "www.youwei-agent.com"), ("sg-dash.youwei-agent.com",)]
    if kind == "digitalocean"
    else [("market.youwei-agent.com", "www.market.youwei-agent.com"), ("dash.youwei-agent.com",)]
)
# A separate private rollback snapshot permits safe recovery from nginx -t failure.
backup = Path("/root/alpha-nginx-cutover-backup")
assert not backup.exists(), "Previous cutover state exists; reconcile before retrying"
backup.mkdir(mode=0o700)
for name in ("sites-available", "sites-enabled"):
    shutil.copytree("/etc/nginx/" + name, backup / name, symlinks=True)
try:
    for directory in (enabled, available):
        for path in directory.iterdir():
            if path.name not in preserve:
                assert path.is_file() or path.is_symlink()
                path.unlink()
    target = available / "hermes-alpha-lab"
    shutil.copyfile(source, target)
    target.chmod(0o644)
    (enabled / target.name).symlink_to(target)
    redirect = ""
    for hosts in aliases:
        domain = hosts[0]
        redirect += f"""server {{
    listen 80;
    server_name {" ".join(hosts)};
    location /.well-known/acme-challenge/ {{ root /var/www/html; }}
    location / {{ return 301 https://trading.youwei-agent.com$request_uri; }}
}}
server {{
    listen 443 ssl;
    server_name {" ".join(hosts)};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    return 301 https://trading.youwei-agent.com$request_uri;
}}
"""
    target = available / "alpha-domain-redirects"
    target.write_text(redirect)
    target.chmod(0o644)
    (enabled / target.name).symlink_to(target)
    subprocess.run(["nginx", "-t"], check=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
except BaseException:
    for name in ("sites-available", "sites-enabled"):
        shutil.rmtree("/etc/nginx/" + name)
        shutil.copytree(backup / name, "/etc/nginx/" + name, symlinks=True)
    subprocess.run(["nginx", "-t"], check=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    raise
print(f"{kind} entry replaced; verify HTTPS externally before removing the rollback snapshot")
