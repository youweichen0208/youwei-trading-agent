"""Activate explicitly selected, contract-verified Profile directories.

Run as root on the deployment host with ALL existing and new owner directories.
No evidence is generated here. Preflight uses the real deployment loader before
publishing registries or restarting services; the same preflight rebinds each
Profile's deployment_hash to the sealed compose/runtime identity. Worker and
supervisor retain their current release and database/service credentials.
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
# The preflight also rebinds each Profile to the sealed compose hash. Image or
# compose changes alter that hash, and a stale binding makes the supervisor
# refuse management while capacity stays occupied.
database_env = PRIVATE / 'database-services' / 'worker.env'
assert database_env.is_file() and not database_env.is_symlink(), 'private_database_environment_required'
assert (database_env.stat().st_mode & 0o077) == 0, 'private_database_environment_required'
for line in database_env.read_text().splitlines():
    if line.startswith('DATABASE_URL='):
        value = line.split('=', 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env.setdefault('DATABASE_URL', value)
assert env.get('DATABASE_URL'), 'database_url_required'
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup = PRIVATE / ('activation-' + stamp)
backup.mkdir(mode=0o700)
candidate = backup / 'candidate-registry.json'
candidate.write_text(json.dumps(entries))
release = Path(subprocess.check_output(['systemctl','show','alpha-lab-supervisor','-p','WorkingDirectory','--value'], text=True).strip())
probe = '''
const {loadDeployments} = await import("./packages/hermes-adapter/src/lifecycle.ts");
const deployments = await loadDeployments(process.argv[1], process.argv.slice(2));
if (!process.env.DATABASE_URL) throw new Error("database_url_required");
const pg = (await import("pg")).default;
const pool = new pg.Pool({connectionString: process.env.DATABASE_URL});
const client = await pool.connect();
try {
  await client.query("BEGIN");
  for (const d of deployments) {
    const updated = await client.query(
      "UPDATE profile_instances SET deployment_hash=$2 WHERE id=$1 AND owner_id=$3 AND role=$4",
      [d.profileId, d.deploymentHash, d.ownerId, d.role]);
    if (updated.rowCount !== 1) throw new Error("profile_instance_missing");
  }
  await client.query("COMMIT");
  console.log(JSON.stringify({verifiedProfiles: deployments.length, hashesSynced: deployments.length}));
} catch (error) {
  await client.query("ROLLBACK").catch(() => undefined);
  throw error;
} finally {
  client.release();
  await pool.end();
}
'''
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
