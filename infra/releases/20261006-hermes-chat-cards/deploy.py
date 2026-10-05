"""Owner-authorized SG rollout; fixed paths, image and rollback, no model calls."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path('/root/hermes-cards-20261006')
BASE = Path('/opt/youwei/chat')
PLATFORM = ROOT / 'platform'
RELEASE = PLATFORM / 'infra/releases/20261006-hermes-chat-cards'
IMAGE = 'ghcr.io/youweichen0208/youwei-webui@sha256:203a484b5dfa6237a98536b2fbeb650f910fa7635bdb34a771cf785237b8e345'
REVISION = '7a868661e41563f1bae33ddbfdcdd061a66cc5a5'
NAME = 'youwei-chat-openwebui-1'
sys.path.insert(0, str(PLATFORM))
from integrations.openwebui.configure_hermes_chat import configure_rich_chat
from ops.verify_webui_upgrade import state


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def containers():
    names = run('docker', 'ps', '--format', '{{.Names}}').splitlines()
    return {c['Name'].lstrip('/'): {'id': c['Id'], 'started': c['State']['StartedAt'],
            'image': c['Config']['Image']} for c in json.loads(run('docker', 'inspect', *names))}


def compose(*args):
    return run('docker', 'compose', '-p', 'youwei-chat', '--project-directory', str(BASE),
               '-f', str(BASE/'compose.json'), '--env-file', str(BASE/'secrets.env'), *args)


def healthy():
    for _ in range(240):
        c = json.loads(run('docker', 'inspect', NAME))[0]
        if c['State'].get('Health', {}).get('Status') == 'healthy':
            return c
        if c['State']['Status'] == 'exited':
            raise RuntimeError('WebUI exited')
        time.sleep(1)
    raise RuntimeError('WebUI health deadline')


def main():
    os.umask(0o077)
    assert 'ALL PASS' in (ROOT/'restore-preflight.log').read_text()
    assert 'PASS network-isolated' in (ROOT/'candidate-restore.log').read_text()
    image = json.loads(run('docker', 'image', 'inspect', IMAGE))[0]
    assert image['Architecture'] == 'amd64'
    assert image['Config']['Labels']['org.opencontainers.image.revision'] == REVISION
    c = json.loads(run('docker', 'inspect', NAME))[0]
    env = dict(v.split('=', 1) for v in c['Config']['Env'])
    owner = env['HERMES_WORKBENCH_OWNER_ID']
    database = Path(next(m['Source'] for m in c['Mounts'] if m['Destination'] == '/app/backend/data'))/'webui.db'
    rollback = ROOT/'rollback'
    rollback.mkdir(exist_ok=False)
    for file in ('compose.json', 'secrets.env'):
        shutil.copy2(BASE/file, rollback/file)
    old = json.loads((BASE/'compose.json').read_text())
    new = json.loads(json.dumps(old))
    new['services']['openwebui']['image'] = IMAGE
    (ROOT/'candidate-compose.json').write_text(json.dumps(new, indent=2)+'\n')
    with (ROOT/'candidate-rendered-private.json').open('w') as output:
        subprocess.run(['docker', 'compose', '-p', 'youwei-chat', '--project-directory', str(BASE),
                        '-f', str(ROOT/'candidate-compose.json'), '--env-file', str(BASE/'secrets.env'),
                        'config', '--format', 'json'], stdout=output, check=True)
    evidence = {'passed': True, 'revision': REVISION, 'image': IMAGE,
                'backup_restore': 'ALL PASS', 'candidate_restore': (ROOT/'candidate-restore.log').read_text().strip(),
                'previous_mock_verification': 'candidate.json', 'paid_models_called': False}
    (RELEASE/'preflight.json').write_text(json.dumps(evidence, indent=2)+'\n')
    lock = json.loads((PLATFORM/'infra/releases/20261005-webui-warm-loading/chat.lock.json').read_text())
    ui = lock['components']['openwebui']
    ui['source']['revision'] = REVISION
    ui['deployment']['image'] = IMAGE
    ui['verification']['evidence'] = {'path': 'infra/releases/20261006-hermes-chat-cards/preflight.json',
                                     'sha256': hashlib.sha256((RELEASE/'preflight.json').read_bytes()).hexdigest()}
    ui['notes'] = 'Owner-scoped Hermes Responses presentation; persisted full tool results and lazy financial cards.'
    (RELEASE/'chat.lock.json').write_text(json.dumps(lock, indent=2)+'\n')
    manifest = {'schema_version': 1, 'lock_sha256': hashlib.sha256((RELEASE/'chat.lock.json').read_bytes()).hexdigest(),
                'compose_sha256': hashlib.sha256((ROOT/'candidate-rendered-private.json').read_bytes()).hexdigest(),
                'components': {k: v['deployment'] for k, v in lock['components'].items() if v.get('enabled')}}
    (RELEASE/'chat.manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    run('python3', str(PLATFORM/'infra/validate_upstreams.py'), '--mode', 'deployment', '--root', str(PLATFORM),
        '--lock', str(RELEASE/'chat.lock.json'), '--compose', str(ROOT/'candidate-rendered-private.json'),
        '--manifest', str(RELEASE/'chat.manifest.json'))
    before = containers()
    (ROOT/'before-cutover.json').write_text(json.dumps(before, indent=2))
    try:
        compose('stop', 'openwebui')
        previous = state(database)
        with sqlite3.connect(database) as src, sqlite3.connect(rollback/'webui.db') as dst:
            src.backup(dst)
        configure_rich_chat(database, owner_id=owner, enabled=True)
        assert state(database) == previous
        shutil.copy2(ROOT/'candidate-compose.json', BASE/'compose.json')
        compose('up', '-d', '--no-deps', 'openwebui')
        assert healthy()['Config']['Image'] == IMAGE
        checks = json.loads(run('docker', 'exec', NAME, 'python', '-c', (ROOT/'postflight.py').read_text()).splitlines()[-1])
        after = state(database)
        assert after[1:] == previous[1:]
        assert all(after[0].get(k) == v for k, v in previous[0].items())
        current = containers()
        assert all(current.get(k) == v for k, v in before.items() if k != NAME)
        evidence = {'passed': True, 'revision': REVISION, 'image': IMAGE, 'checks': checks,
                    'chats_preserved': len(previous[0]), 'account_password_preserved': True,
                    'other_containers_unchanged': len(before)-1, 'rollback_executed': False,
                    'paid_models_called': False}
        (ROOT/'postflight.json').write_text(json.dumps(evidence, indent=2)+'\n')
        print(json.dumps(evidence), flush=True)
    except BaseException:
        compose('stop', 'openwebui')
        configure_rich_chat(database, owner_id=owner, enabled=False)
        shutil.copy2(rollback/'compose.json', BASE/'compose.json')
        compose('up', '-d', '--no-deps', 'openwebui')
        healthy()
        (ROOT/'rollback-executed.txt').write_text('Restored previous image and disabled rich chat. Research and chat data retained.\n')
        raise


if __name__ == '__main__':
    main()
