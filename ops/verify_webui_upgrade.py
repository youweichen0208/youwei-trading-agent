"""Upgrade a private WebUI backup in a disposable container; never mount live data."""
import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.openwebui.configure_assistant import configure


def docker(*args):
    return subprocess.check_output(['docker', *map(str,args)], text=True, stderr=subprocess.STDOUT).strip()


def state(path):
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        chats = {r[0]: hashlib.sha256(r[1].encode()).hexdigest() for r in db.execute('SELECT id,chat FROM chat')}
        users = db.execute('SELECT id,role FROM user').fetchall()
        auth = db.execute('SELECT id,password FROM auth ORDER BY id').fetchall()
        return chats, users, auth


def verify(archive, image):
    name = 'youwei-webui-upgrade-' + uuid.uuid4().hex[:8]
    image = docker('image','inspect','--format','{{.Id}}',image)
    with tempfile.TemporaryDirectory(prefix='private-webui-upgrade-') as work:
        root = Path(work); data = root/'data'; data.mkdir()
        with tarfile.open(archive, 'r:gz') as tar:
            tar.extractall(data, filter='data')
        database = data/'webui.db'
        before = state(database)
        assert len(before[1]) == 1 and before[1][0][1] == 'admin'
        owner = before[1][0][0]
        # Replace live connection credentials before this private copy can start.
        configure(database, owner_id=owner, assistant_key='offline-migration-assistant',
                  chat_key='offline-migration-chat', old_pipe_id='youwei_research_pipe')
        try:
            # Network none: migrations and health checks cannot contact a paid gateway.
            docker('run','-d','--name',name,'--network','none','--memory','1536m','--cpus','1.0',
                   '-v',str(data)+':/app/backend/data',
                   '-e','OFFLINE_MODE=true','-e','HF_HUB_OFFLINE=1','-e','ENABLE_OLLAMA_API=False',
                   '-e','RAG_EMBEDDING_MODEL_AUTO_UPDATE=False','-e','WEBUI_SECRET_KEY=isolated-migration-secret',image)
            for _ in range(180):
                ready = subprocess.run(['docker','exec',name,'python','-c',
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2)"],capture_output=True)
                if ready.returncode == 0: break
                time.sleep(1)
            else: raise RuntimeError('new WebUI did not finish migration/startup')
            docker('stop',name)
            after = state(database)
            assert before == after, 'chat JSON, account or password hash changed unexpectedly'
            configure(database, owner_id=owner, assistant_key='offline-migration-assistant',
                      chat_key='offline-migration-chat', old_pipe_id='youwei_research_pipe')
            with sqlite3.connect(database) as db:
                config = {k: json.loads(v) if isinstance(v, str) else v
                          for k,v in db.execute('SELECT key,value FROM config')}
                assert config['ui.enable_signup'] is False
                assert db.execute('SELECT count(*) FROM access_grant WHERE resource_id=? AND principal_id=?',
                                  ('Hermes 美股助手',owner)).fetchone()[0] == 2
            docker('start',name)
            for _ in range(90):
                ready = subprocess.run(['docker','exec',name,'python','-c',
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2)"],capture_output=True)
                if ready.returncode == 0: break
                time.sleep(1)
            else: raise RuntimeError('migrated WebUI failed restart')
            print(f'PASS network-isolated backup migration/restart: {len(before[0])} chats byte-preserved, owner/password preserved, private model grants and signup disabled')
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',required=True,type=Path)
    parser.add_argument('--webui-image',required=True)
    args=parser.parse_args()
    verify(args.archive,args.webui_image)
