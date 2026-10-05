"""Disposable pinned WebUI -> native Hermes -> mock model acceptance.

No production network, keys or volumes. Requires the locally built assistant image.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import tempfile
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.STDOUT).strip()


def verify(assistant_image, webui_image):
    assistant_image = docker("image", "inspect", "--format", "{{.Id}}", assistant_image)
    webui_image = docker("image", "inspect", "--format", "{{.Id}}", webui_image)
    prefix = 'youwei-native-' + uuid.uuid4().hex[:8]
    containers, volumes = [], []
    network = docker('network', 'create', prefix)
    def run(name, image, *args, command=()):
        name = prefix + '-' + name; containers.append(name)
        docker('run', '-d', '--name', name, '--network', network, *args, image, *command)
        return name
    def volume(name):
        name=prefix+'-'+name; volumes.append(name); docker('volume','create',name); return name
    def port(container, number):
        return docker('port', container, str(number)).rsplit(':',1)[-1]
    def ready(url, seconds=180):
        for _ in range(seconds):
            try:
                if httpx.get(url, timeout=2).status_code == 200: return
            except httpx.HTTPError: pass
            time.sleep(1)
        raise RuntimeError('service failed to become healthy: '+url)
    try:
        mock = run('model', assistant_image, '--network-alias','litellm', '-p','127.0.0.1::4000',
                   '-v',str(ROOT/'ops/assistant_mock_model.py')+':/mock.py:ro','-e','MOCK_MODELS_UNAVAILABLE=1','--entrypoint','python',
                   command=('/mock.py',))
        profile, notes, webdata = volume('profile'), volume('notes'), volume('webui')
        gateway = run('hermes', assistant_image,'--network-alias','hermes-assistant','-p','127.0.0.1::8642',
            '--init','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=128m','--cap-drop','ALL','--security-opt','no-new-privileges:true',
            '--memory','768m','--pids-limit','128',
            '-v',profile+':/var/lib/hermes/profile','-v',notes+':/var/lib/hermes/knowledge',
            '-e','API_SERVER_KEY=native-smoke-test-key-123456','-e','YOUWEI_ASSISTANT_LLM_KEY=mock-model-key-123456789',
            '-e','YOUWEI_ASSISTANT_CORE_KEY=mock-core-key-123456789','-e','YOUWEI_ASSISTANT_CORE_URL=http://unused:8000')
        ready('http://127.0.0.1:'+port(gateway,8642)+'/health',60)
        web = run('webui',webui_image,'-p','127.0.0.1::8080','-v',webdata+':/app/backend/data',
            '-e','OFFLINE_MODE=true','-e','HF_HUB_OFFLINE=1','-e','RAG_EMBEDDING_MODEL_AUTO_UPDATE=False',
            '-e','ENABLE_OLLAMA_API=False','-e','ENABLE_SIGNUP=True','-e','WEBUI_SECRET_KEY=isolated-webui-secret',
            '-e','OPENAI_API_BASE_URL=http://litellm:4000/v1','-e','OPENAI_API_KEY=ordinary-chat-key')
        url='http://127.0.0.1:'+port(web,8080)
        ready(url+'/health')
        with httpx.Client(base_url=url,timeout=90) as client:
            signup=client.post('/api/v1/auths/signup',json={'name':'Owner','email':'owner@example.com','password':'local-smoke-password-123'})
            assert signup.status_code == 200, signup.text
            owner=signup.json()['id']; token=signup.json()['token']
            client.headers['Authorization']='Bearer '+token
            chat=client.post('/api/v1/chats/new',json={'chat':{'title':'preserve marker','messages':[]}})
            assert chat.status_code == 200, chat.text
            chat_id=chat.json()['id']
            docker('stop',web)
            docker('run','--rm','--network','none','-v',webdata+':/app/backend/data','-v',str(ROOT)+':/workspace:ro',
                '-e','YOUWEI_WEBUI_OWNER_ID='+owner,'-e','YOUWEI_ASSISTANT_API_KEY=native-smoke-test-key-123456',
                '-e','YOUWEI_CHAT_KEY=ordinary-chat-key','-e','YOUWEI_OLD_PIPE_ID=youwei_research',
                '--entrypoint','python',webui_image,'/workspace/integrations/openwebui/configure_assistant.py')
            docker('start',web); url='http://127.0.0.1:'+port(web,8080); client.base_url=url; ready(url+'/health')
            models=client.get('/api/models'); assert models.status_code == 200, models.text
            assert 'Hermes 美股助手' in {m['id'] for m in models.json()['data']}
            assert client.get('/api/v1/chats/'+chat_id).json()['title'] == 'preserve marker'
            assert client.post('/api/v1/auths/signup',json={'name':'Second','email':'second@example.com','password':'local-smoke-password-123'}).status_code == 403
            messages=[{'role':'user','content':'你好，简单介绍自己。'}]
            reply=client.post('/api/chat/completions',json={'model':'Hermes 美股助手','messages':messages,'stream':False})
            assert reply.status_code == 200, reply.text
            messages += [{'role':'assistant','content':reply.json()['choices'][0]['message']['content']},
                         {'role':'user','content':'接着解释刚才的回答。'}]
            follow=client.post('/api/chat/completions',json={'model':'Hermes 美股助手','messages':messages,'stream':True})
            assert follow.status_code == 200 and '[DONE]' in follow.text, follow.text
            for name in ('trading_price_history', 'trading_indicators', 'trading_financials'):
                financial = client.post('/api/chat/completions',json={'model':'Hermes 美股助手','stream':False,
                    'messages':[{'role':'user','content':'VERIFY:'+json.dumps({'tool':name,'args':{'symbol':'../bad','api_key':'forbidden'}})}]})
                assert financial.status_code == 200, financial.text
                assert json.loads(financial.json()['choices'][0]['message']['content']) == {'error':'invalid_financial_arguments'}
            def note_call(action, arguments):
                result=client.post('/api/chat/completions',json={'model':'Hermes 美股助手','stream':False,
                    'messages':[{'role':'user','content':'VERIFY:'+json.dumps({'tool':'youwei_knowledge','args':{'action':action,'arguments':arguments}})}]})
                assert result.status_code == 200, result.text
                return json.loads(result.json()['choices'][0]['message']['content'])
            saved=note_call('save',{'note_id':'webui-note','title':'WebUI source','content':'Across chats marker',
                                    'sources':[{'url':'https://www.apple.com/','accessed_at':'2026-10-04'}]})
            assert note_call('read',{'note_id':'webui-note'}) == saved
            docker('exec',gateway,'python','/opt/youwei-assistant/backup.py','create','/tmp/assistant-backup.tar.gz')
            with tempfile.TemporaryDirectory(prefix='assistant-restore-') as backup_dir:
                archive = Path(backup_dir)/'hermes-data.tar.gz'
                metadata = Path(backup_dir)/'hermes-image.json'
                # docker cp cannot read tmpfs files on some daemons; stream exact bytes.
                with archive.open('wb') as output:
                    subprocess.check_call(['docker', 'exec', gateway, 'cat', '/tmp/assistant-backup.tar.gz'], stdout=output)
                helper = ROOT/'ops/backup/assistant_image.py'
                subprocess.check_call(['python3', str(helper), 'capture', '--container', gateway,
                                       '--archive', str(archive), '--metadata', str(metadata)])
                restored = json.loads(subprocess.check_output(['python3', str(helper), 'verify',
                    '--archive', str(archive), '--metadata', str(metadata), '--image', assistant_image], text=True))
            assert restored['databases']['state.db'] > 0 and restored['notes_checked_up_to_100'] == 1
            docker('restart',gateway)
            ready('http://127.0.0.1:'+port(gateway,8642)+'/health',60)
            assert note_call('read',{'note_id':'webui-note'}) == saved
            def audit():
                return json.loads(docker('exec',mock,'python','-c',"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:4000/audit').read().decode())"))
            before=audit()
            title=client.post('/api/v1/tasks/title/completions',json={'model':'Hermes 美股助手','messages':messages})
            assert title.status_code == 200, title.text
            after=audit()
            background=after[len(before):]
            assert background and all(not item['assistant_key'] and not item['tools'] for item in background), background
            assert any(item['assistant_key'] and 'youwei_knowledge' in item['tools'] for item in before), before
            print('PASS pinned WebUI cutover/model discovery/chat/full-history follow-up/SSE/knowledge/restart/backup restore/background routing with discovery unavailable/signup disabled/history preserved')
    except Exception:
        for container in containers:
            print(container, docker('logs','--tail','35',container))
        raise
    finally:
        for container in reversed(containers):
            subprocess.run(['docker','rm','-f',container],capture_output=True)
        for name in volumes:
            subprocess.run(['docker','volume','rm',name],capture_output=True)
        subprocess.run(['docker','network','rm',network],capture_output=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--assistant-image", required=True, help="Explicit locally available image to verify")
    parser.add_argument("--webui-image", required=True, help="Explicit locally available WebUI image to verify")
    args = parser.parse_args()
    verify(args.assistant_image, args.webui_image)
