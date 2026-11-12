"""Publish only an already-built, public Next static shell and hashed assets.

Run on the entry server with an extracted .next subset as argument. Private APIs
remain upstream. Old chunks are retained so open tabs survive a web deployment.
"""
import gzip
import re
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
build = (source / "BUILD_ID").read_text().strip()
assert re.fullmatch(r"[A-Za-z0-9_-]{1,100}", build)
root = Path("/var/www/hermes-alpha-lab")
release = root / "releases" / build
assets = root / "assets/_next/static"
release.mkdir(parents=True, exist_ok=True)
assets.mkdir(parents=True, exist_ok=True)
shutil.copytree(source / "static", assets, dirs_exist_ok=True)
shutil.copyfile(source / "server/app/index.html", release / "index.html")
for directory in (root, *root.rglob("*")):
    if directory.is_dir():
        directory.chmod(0o755)
for file in [release / "index.html", *assets.rglob("*")]:
    if file.is_file() and not file.is_symlink():
        file.chmod(0o644)
        if file.suffix in {".html", ".js", ".css", ".json", ".svg"}:
            compressed = file.with_name(file.name + ".gz")
            compressed.write_bytes(gzip.compress(file.read_bytes(), compresslevel=6, mtime=0))
            compressed.chmod(0o644)
temporary = root / ".next-current"
temporary.unlink(missing_ok=True)
temporary.symlink_to(release)
temporary.replace(root / "current")
print(f"Public shell and static assets published: {build}")
