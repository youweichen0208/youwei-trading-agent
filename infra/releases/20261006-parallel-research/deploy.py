"""Fixed-path authorized assistant update, preserving all data and other services."""
import hashlib,json,os,shutil,subprocess,time
from pathlib import Path
ROOT=Path('/root/parallel-research-20261006'); BASE=Path('/opt/youwei/chat')
PLATFORM=ROOT/'platform'; RELEASE=PLATFORM/'infra/releases/20261006-parallel-research'
SERVICES=('hermes-assistant','hermes-workbench-skills','openwebui')
NAMES={'youwei-chat-'+s+'-1' for s in SERVICES}
def run(*args): return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT)
def compose(*args): return run('docker','compose','-p','youwei-chat','--project-directory',str(BASE),'-f',str(BASE/'compose.json'),'--env-file',str(BASE/'secrets.env'),*args)
def inventory():
 names=run('docker','ps','--format','{{.Names}}').splitlines()
 return {c['Name'].lstrip('/'):{'id':c['Id'],'started':c['State']['StartedAt'],'image':c['Config']['Image']} for c in json.loads(run('docker','inspect',*names))}
def healthy():
 for _ in range(180):
  rows=json.loads(run('docker','inspect','youwei-chat-hermes-assistant-1','youwei-chat-openwebui-1'))
  if all(c['State'].get('Health',{}).get('Status')=='healthy' for c in rows): return
  if any(c['State']['Status']=='exited' for c in rows): raise RuntimeError('service exited')
  time.sleep(1)
 raise RuntimeError('assistant health deadline')
def main():
 os.umask(0o077);RELEASE.mkdir(parents=True,exist_ok=True)
 info=json.loads((ROOT/'image.json').read_text()); image=info['image']
 assert '@sha256:' in image
 assert 'ALL PASS' in (ROOT/'restore.log').read_text()
 assert json.loads((ROOT/'live-preflight.json').read_text())['passed']
 assert json.loads((ROOT/'candidate-restore.log').read_text())['databases']['state.db'] > 0
 assert 'PASS' in (ROOT/'webui-restore.log').read_text()
 assert 'PASS Responses store=false' in (ROOT/'responses-acceptance.log').read_text()
 assert 'PASS pinned WebUI' in (ROOT/'cross-service.log').read_text()
 ci=json.loads(run('docker','image','inspect',image))[0]
 assert ci['Architecture']=='amd64' and ci['Config']['Labels']['org.opencontainers.image.revision']==info['revision']
 wi=json.loads(run('docker','image','inspect',info['webui_image']))[0]
 assert wi['Architecture']=='amd64' and wi['Config']['Labels']['org.opencontainers.image.revision']==info['webui_revision']
 rollback=ROOT/'rollback';rollback.mkdir(exist_ok=False)
 for name in ('compose.json','secrets.env'): shutil.copy2(BASE/name,rollback/name)
 config=json.loads((BASE/'compose.json').read_text())
 for service in SERVICES:config['services'][service]['image']=info['webui_image'] if service=='openwebui' else image
 (ROOT/'candidate-compose.json').write_text(json.dumps(config,indent=2)+'\n')
 with (ROOT/'candidate-rendered-private.json').open('w') as out:
  subprocess.run(['docker','compose','-p','youwei-chat','--project-directory',str(BASE),'-f',str(ROOT/'candidate-compose.json'),'--env-file',str(BASE/'secrets.env'),'config','--format','json'],stdout=out,check=True)
 preflight={'passed':True,**info,'webui_restore':(ROOT/'webui-restore.log').read_text().strip(),'live':json.loads((ROOT/'live-preflight.json').read_text()),'backup_restore':'ALL PASS','candidate_restore':(ROOT/'candidate-restore.log').read_text().strip(),'responses':'PASS Responses store=false', 'cross_service':'PASS pinned WebUI','paid_models_called':False}
 (RELEASE/'preflight.json').write_text(json.dumps(preflight,indent=2)+'\n')
 lock=json.loads((PLATFORM/'infra/releases/20261006-finance-date-fix/chat.lock.json').read_text())
 a=lock['components']['hermes-assistant'];a['source']['revision']=info['revision'];a['deployment']['image']=image
 a['trading_core']=json.loads((ROOT/'assistant/upstreams.lock.json').read_text())['trading_core']
 a['verification']['evidence']={'path':'infra/releases/20261006-parallel-research/preflight.json','sha256':hashlib.sha256((RELEASE/'preflight.json').read_bytes()).hexdigest()}
 a['notes']='Bounded parallel web research and combined trend analysis; no paid model verification or formal research release change.'
 w=lock['components']['openwebui']
 w['source']['revision']=info['webui_revision'];w['deployment']['image']=info['webui_image']
 w['verification']['evidence']=a['verification']['evidence']
 w['notes']='Combined trend card with explicit warmup history; owner-scoped Hermes Responses presentation retained.'
 (RELEASE/'chat.lock.json').write_text(json.dumps(lock,indent=2)+'\n')
 manifest={'schema_version':1,'lock_sha256':hashlib.sha256((RELEASE/'chat.lock.json').read_bytes()).hexdigest(),'compose_sha256':hashlib.sha256((ROOT/'candidate-rendered-private.json').read_bytes()).hexdigest(),'components':{k:v['deployment'] for k,v in lock['components'].items() if v.get('enabled')}}
 (RELEASE/'chat.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 run('python3',str(PLATFORM/'infra/validate_upstreams.py'),'--mode','deployment','--root',str(PLATFORM),'--lock',str(RELEASE/'chat.lock.json'),'--manifest',str(RELEASE/'chat.manifest.json'),'--compose',str(ROOT/'candidate-rendered-private.json'))
 before=inventory();(ROOT/'before.json').write_text(json.dumps(before,indent=2))
 try:
  shutil.copy2(ROOT/'candidate-compose.json',BASE/'compose.json')
  compose('up','-d','--no-deps',*SERVICES);healthy()
  checks=json.loads(run('docker','exec','youwei-chat-openwebui-1','python','-c',(ROOT/'postflight.py').read_text()).splitlines()[-1])
  current=inventory()
  assert all(current.get(k)==v for k,v in before.items() if k not in NAMES)
  assert all(current['youwei-chat-'+s+'-1']['image']==(info['webui_image'] if s=='openwebui' else image) for s in SERVICES)
  result={'passed':True,**info,'checks':checks,'other_containers_unchanged':len(before)-3,'rollback_executed':False,'paid_models_called':False}
  (ROOT/'postflight.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
 except BaseException:
  shutil.copy2(rollback/'compose.json',BASE/'compose.json');compose('up','-d','--no-deps',*SERVICES);healthy()
  (ROOT/'rollback-executed.txt').write_text('Previous images and matching Compose restored; data retained.\n');raise
if __name__=='__main__': main()
