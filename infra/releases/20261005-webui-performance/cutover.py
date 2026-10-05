"""Change only WebUI; automatically restore matching configuration on failure."""
import hashlib,json,shutil,subprocess,time
from pathlib import Path
root=Path('/root/webui-perf-20261005');base=Path('/opt/youwei/chat')
image='ghcr.io/youweichen0208/youwei-webui@sha256:52783da9334e73bc33c483873143fd9be4053861b6216dbf9959053305272e6f'
def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT)
def compose():return run('docker','compose','-p','youwei-chat','--project-directory',str(base),'-f',str(base/'compose.json'),'--env-file',str(base/'secrets.env'),'up','-d','--no-deps','openwebui')
def inspect():
 names=run('docker','ps','--format','{{.Names}}').splitlines()
 return {c['Name'].lstrip('/'):{'id':c['Id'],'started':c['State']['StartedAt'],'image':c['Config']['Image']} for c in json.loads(run('docker','inspect',*names))}
def healthy():
 for _ in range(240):
  c=json.loads(run('docker','inspect','youwei-chat-openwebui-1'))[0]
  if c['State'].get('Health',{}).get('Status')=='healthy':return c
  if c['State']['Status']=='exited':raise RuntimeError('WebUI exited')
  time.sleep(1)
 raise RuntimeError('WebUI health deadline')
assert 'PASS network-isolated' in (root/'candidate-restore.log').read_text()
assert 'PASS pinned WebUI' in (root/'cross-service.log').read_text()
assert (root/'browser-done').exists()
assert (root/'backup-refreshed.ok').exists()
old=json.loads((base/'compose.json').read_text());new=json.loads((root/'candidate-compose.json').read_text())
expected=json.loads(json.dumps(old));expected['services']['openwebui']['image']=image
assert new==expected
assert (base/'compose.json').read_bytes()==(root/'rollback/compose.json').read_bytes()
assert (base/'secrets.env').read_bytes()==(root/'rollback/secrets.env').read_bytes()
release=root/'platform/infra/releases/20261005-webui-performance'
subprocess.run(['python3',str(root/'platform/infra/validate_upstreams.py'),'--mode','deployment','--root',str(root/'platform'),'--lock',str(release/'chat.lock.json'),'--compose',str(root/'candidate-rendered-private.json'),'--manifest',str(release/'chat.manifest.json')],check=True)
before=inspect();(root/'before-cutover.json').write_text(json.dumps(before,indent=2))
try:
 shutil.copy2(root/'candidate-compose.json',base/'compose.json');compose();c=healthy()
 assert c['Config']['Image']==image
 checks=json.loads(run('docker','exec','-i','youwei-chat-openwebui-1','python','-c',(root/'postflight-webui.py').read_text()).splitlines()[-1])
 after=inspect()
 assert all(after.get(k)==v for k,v in before.items() if k!='youwei-chat-openwebui-1'), 'another running container changed'
 evidence={'passed':True,'source':'b8fc503f3c725518d25cedeb4387b34434de45be','image':image,'checks':checks,'other_containers_unchanged':len(before)-1,'rollback_executed':False}
 (root/'postflight.json').write_text(json.dumps(evidence,indent=2)+'\n')
 print(json.dumps(evidence,indent=2),flush=True)
except BaseException:
 shutil.copy2(root/'rollback/compose.json',base/'compose.json');compose();healthy()
 (root/'rollback-executed.txt').write_text('Restored original WebUI image and configuration; same data retained.\n')
 print('ROLLED BACK: image/configuration restored; data preserved',flush=True)
 raise
