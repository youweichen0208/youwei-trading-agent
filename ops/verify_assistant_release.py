"""Verify a candidate Hermes image against a private backup without network/live mounts."""
import argparse
import hashlib
import json
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops.backup.assistant_image import restore


def docker(*args):
    return subprocess.check_output(['docker', *map(str, args)], text=True, stderr=subprocess.STDOUT).strip()


def data_state(root):
    rows = {}
    with sqlite3.connect(f'file:{root}/profile/state.db?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        for table in ('sessions', 'messages'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                values = sorted(json.dumps(list(row), default=str) for row in db.execute(f'SELECT * FROM "{table}"'))
                rows[table] = {'count': len(values), 'sha256': hashlib.sha256('\n'.join(values).encode()).hexdigest()}
    files = {}
    for folder in ('profile/memories', 'profile/sessions', 'knowledge'):
        for path in (root / folder).rglob('*'):
            if path.is_file() and path.name != '.lock':
                files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows, files


def verify(archive, metadata, candidate_image):
    # First validate archive identity with the original recorded image.
    json.loads(restore(archive, metadata))
    image = docker('image', 'inspect', '--format', '{{.Id}}', candidate_image)
    name = 'youwei-hermes-release-' + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix='private-hermes-release-') as work:
        root = Path(work)
        result = json.loads(docker('run', '--rm', '--network', 'none', '--read-only', '--user', '0:0',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '-v', str(archive.resolve())+':/backup.tar.gz:ro', '-v', str(root)+':/restore',
            '--entrypoint', 'python', image, '/opt/youwei-assistant/backup.py',
            'verify', '/backup.tar.gz', '--destination', '/restore/data'))
        data = root / 'data'
        before = data_state(data)
        for path in [data, *data.rglob('*')]:
            os.chown(path, 10001, 10001)
        try:
            docker('run', '-d', '--name', name, '--network', 'none', '--read-only', '--init',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--memory', '768m',
                '--pids-limit', '128', '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m',
                '-v', str(data/'profile')+':/var/lib/hermes/profile',
                '-v', str(data/'knowledge')+':/var/lib/hermes/knowledge',
                '-e', 'API_SERVER_KEY=isolated-release-api-key-123456',
                '-e', 'YOUWEI_ASSISTANT_LLM_KEY=isolated-release-model-key-123456',
                '-e', 'YOUWEI_ASSISTANT_CORE_KEY=isolated-release-core-key-123456', image)
            for cycle in range(2):
                for _ in range(120):
                    ready = subprocess.run(['docker', 'exec', name, 'python', '-c',
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8642/health',timeout=2)"], capture_output=True)
                    if ready.returncode == 0:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError('candidate failed startup with restored backup')
                docker('exec', name, 'python', '-c',
                    "import json,os,urllib.request; req=urllib.request.Request('http://127.0.0.1:8642/v1/models',headers={'Authorization':'Bearer '+os.environ['API_SERVER_KEY']}); assert json.load(urllib.request.urlopen(req))['data'][0]['id']=='Hermes 美股助手'")
                docker('stop', name)
                assert data_state(data) == before, 'existing session/message/memory/knowledge data changed'
                if cycle == 0:
                    docker('start', name)
            print(json.dumps({'result': 'PASS', 'network': 'none', 'startup_and_restart': True,
                              'preserved_rows': {k: v['count'] for k, v in before[0].items()},
                              'preserved_files': len(before[1]), 'restored_databases': result['databases']}))
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--candidate-image', required=True)
    args = parser.parse_args()
    verify(args.archive, args.metadata, args.candidate_image)
