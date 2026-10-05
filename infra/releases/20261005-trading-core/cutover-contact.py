"""Authorized two-service rollout; rollback on failed postflight checks."""
import hashlib,json,shutil,subprocess,time
from pathlib import Path
root=Path('/root/trading-finance-20261005');base=Path('/opt/youwei/chat')
image='ghcr.io/youweichen0208/trading-assistant@sha256:ffd1768a4eb036083c37f4d16627922db66dd4318b4d949336c815e9ba4dcdc1'
services=('hermes-assistant','hermes-workbench-skills')
def compose(*args):
    return subprocess.run(['docker','compose','-p','youwei-chat','--project-directory',str(base),'-f',str(base/'compose.json'),'--env-file',str(base/'secrets.env'),*args],check=True,capture_output=True,text=True)
def health():
    for _ in range(180):
        c=json.loads(subprocess.check_output(['docker','inspect','youwei-chat-hermes-assistant-1']))[0]
        if c['State'].get('Health',{}).get('Status')=='healthy':return c
        if c['State']['Status']=='exited':raise RuntimeError('assistant exited')
        time.sleep(1)
    raise RuntimeError('health deadline')
def exec_script(file,output,timeout=480):
    with (root/output).open('w') as stream, (root/(output+'.stderr')).open('w') as errors:
        subprocess.run(['docker','exec','youwei-chat-hermes-assistant-1','python','/tmp/trading-finance-verify/'+file,*(['--live'] if file.startswith('verify_finance') else [])],stdout=stream,stderr=errors,check=True,timeout=timeout)
assert 'ALL PASS' in (root/'contact-restore.log').read_text()
assert len(json.loads((root/'contact-candidate-restore.json').read_text())['databases'])==5
assert json.loads((root/'sec-filing-values.json').read_text())['matched_facts']==32
assert json.loads((root/'finance-live-contact.json').read_text())['passed']
assert (base/'compose.json').read_bytes()==(root/'rollout-rollback/compose.json').read_bytes()
assert (base/'secrets.env').read_bytes()==(root/'rollout-rollback/secrets.env').read_bytes()
release=root/'platform/infra/releases/20261005-trading-core'
subprocess.run(['python3',str(root/'platform/infra/validate_upstreams.py'),'--mode','deployment','--root',str(root/'platform'),'--lock',str(release/'chat.lock.json'),'--compose',str(root/'rendered-contact-private.json'),'--manifest',str(release/'chat.manifest.json')],check=True)
changed=False
try:
    changed=True
    shutil.copy2(root/'candidate-compose.json',base/'compose.json')
    shutil.copy2(root/'candidate-secrets.env',base/'secrets.env')
    compose('up','-d','--no-deps',*services)
    c=health();assert c['Config']['Image']==image
    print('PASS new assistant healthy',flush=True)
    subprocess.run(['python3',str(root/'postflight-contact.py')],check=True,timeout=120)
    files={name:(root/'source/ops'/name).read_text() for name in ('verify_finance_runtime.py','verify_eodhd_runtime.py','verify_eodhd_live.py')}
    files['verify_finance_runtime.py']=files['verify_finance_runtime.py'].replace("install_profile(Path(os.environ['HERMES_HOME']))","assert (Path(os.environ['HERMES_HOME'])/'config.yaml').exists()")
    setup='from pathlib import Path\nimport json\np=Path("/tmp/trading-finance-verify");p.mkdir(exist_ok=True)\nfiles=json.loads('+repr(json.dumps(files))+')\nfor name,body in files.items():(p/name).write_text(body)\n'
    subprocess.run(['docker','exec','-i','youwei-chat-hermes-assistant-1','python','-'],input=setup,text=True,check=True)
    exec_script('verify_finance_runtime.py','postflight-finance.json')
    assert json.loads((root/'postflight-finance.json').read_text())['passed']
    print('PASS production free prices indicators and SEC queries',flush=True)
    exec_script('verify_eodhd_runtime.py','postflight-eodhd.json')
    assert json.loads((root/'postflight-eodhd.json').read_text())['result']=='PASS'
    print('PASS production existing EODHD quote and Marketplace tools',flush=True)
    with (root/'production-rendered-contact-private.json').open('w') as stream:
        subprocess.run(['docker','compose','-p','youwei-chat','--project-directory',str(base),'-f',str(base/'compose.json'),'--env-file',str(base/'secrets.env'),'config','--format','json'],stdout=stream,check=True)
    assert (root/'production-rendered-contact-private.json').read_bytes()==(root/'rendered-contact-private.json').read_bytes()
    subprocess.run(['python3',str(root/'postflight-contact.py')],check=True,timeout=120)
    subprocess.run(['docker','exec','youwei-chat-hermes-assistant-1','python','-c','import shutil; shutil.rmtree("/tmp/trading-finance-verify")'],check=True)
    (root/'contact-cutover.success').write_text(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'\n')
    print('PASS rollout complete',flush=True)
except BaseException:
    if changed:
        shutil.copy2(root/'rollout-rollback/compose.json',base/'compose.json')
        shutil.copy2(root/'rollout-rollback/secrets.env',base/'secrets.env')
        compose('up','-d','--no-deps',*services);health()
        print('ROLLED BACK image and configuration; original data volumes retained',flush=True)
    raise
