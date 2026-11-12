"""Activate explicitly selected, contract-verified Profile directories.

Run as root on the deployment host with ALL existing and new owner directories.
No evidence is generated here. Preflight uses the real deployment loader before
publishing registries or restarting services. Worker and supervisor retain their
current release and database/service credentials.
Use --no-start during a drained runtime upgrade to publish verified registries
without resuming tasks before the remaining services have passed health checks.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/opt/hermes-alpha-lab')
PRIVATE = ROOT / 'private'
WORKER = Path('/var/lib/hermes-alpha-lab/worker')
NODE = ROOT / 'runtime/node-v26.0.0-linux-x64/bin/node'
assert os.getuid() == 0 and len(sys.argv) > 1
os.umask(0o077)
defer_start = '--no-start' in sys.argv[1:]
directories = [Path(p).resolve(strict=True) for p in sys.argv[1:] if p != '--no-start']
assert directories
assert len(set(directories)) == len(directories)
entries = []
env = os.environ.copy()
for directory in directories:
    assert directory.parent == PRIVATE / 'profiles'
    for line in (directory / 'worker.env').read_text().splitlines():
        key, value = line.split('=', 1)
        assert re.fullmatch(r'HERMES_PROFILE_[A-F0-9]+', key)
        assert re.fullmatch(r'[a-f0-9]{64}', value)
        env[key] = value
    for entry in json.loads((directory / 'profiles.verified.json').read_text()):
        entry['evidenceFile'] = str((directory / entry['evidenceFile']).resolve(strict=True))
        assert Path(entry['evidenceFile']).parent == directory / 'evidence'
        entries.append(entry)
ids = [e['profileId'] for e in entries]
assert len(ids) == len(set(ids)) == 3 * len(directories)
old = json.loads((WORKER / 'registry.json').read_text())
assert {e['profileId'] for e in old} <= set(ids), 'Existing Profiles must be retained'
for line in (PRIVATE / 'capability.env').read_text().splitlines():
    key, value = line.split('=', 1)
    env[key] = value
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup = PRIVATE / ('activation-' + stamp)
backup.mkdir(mode=0o700)
candidate = backup / 'candidate-registry.json'
candidate.write_text(json.dumps(entries))
release = Path(subprocess.check_output(['systemctl','show','alpha-lab-supervisor','-p','WorkingDirectory','--value'], text=True).strip())
probe = 'const {loadDeployments}=await import("./packages/hermes-adapter/src/lifecycle.ts"); const p=await loadDeployments(process.argv[1],process.argv.slice(2)); console.log(JSON.stringify({verifiedProfiles:p.length}));'
subprocess.run([str(NODE),'--import','tsx','--input-type=module','-e',probe,str(candidate),*[str(p) for p in directories]], cwd=release, env=env, check=True)
registry = PRIVATE / 'profiles.verified.json'
units = {}
for name in ('worker','supervisor'):
    path = Path('/etc/systemd/system') / f'alpha-lab-{name}.service'
    original = path.read_text()
    (backup / path.name).write_text(original)
    updated = original
    for directory in directories:
        line = f'EnvironmentFile={directory}/worker.env\n'
        if line not in updated:
            updated = updated.replace('[Service]\n', '[Service]\n' + line)
    if name == 'supervisor':
        command = ' '.join([str(NODE),'--import','tsx','scripts/supervise-profiles.ts',str(registry),*[str(p) for p in directories]])
        updated, count = re.subn(r'^ExecStart=.*$', 'ExecStart=' + command, updated, flags=re.M)
        assert count == 1
    units[path] = updated
shutil.copy2(WORKER / 'registry.json', backup / 'previous-worker-registry.json')
if registry.exists(): shutil.copy2(registry, backup / 'previous-supervisor-registry.json')
# Each evidence file is immutable input to the preflight above; copy it privately.
worker_entries = []
for entry in entries:
    name = entry['profileId'] + '.json'
    target = WORKER / 'evidence' / name
    target.write_text(Path(entry['evidenceFile']).read_text())
    target.chmod(0o600)
    shutil.chown(target, user='alpha-worker', group='alpha-worker')
    worker_entries.append({**entry, 'evidenceFile': 'evidence/' + name})
for path, data in ((registry, entries),(WORKER / 'registry.json', worker_entries)):
    stage = path.with_suffix('.pending')
    stage.write_text(json.dumps(data) + '\n')
    stage.chmod(0o600)
    if path.parent == WORKER: shutil.chown(stage,user='alpha-worker',group='alpha-worker')
    stage.replace(path)
for path, content in units.items():
    path.write_text(content)
    path.chmod(0o644)
subprocess.run(['systemctl','daemon-reload'],check=True)
if not defer_start:
    subprocess.run(['systemctl','restart','alpha-lab-supervisor','alpha-lab-worker'],check=True)
    subprocess.run(['systemctl','is-active','alpha-lab-supervisor','alpha-lab-worker'],check=True)
print(json.dumps({'activatedProfiles':len(entries),'backup':str(backup)}))
