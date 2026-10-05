import json,os,urllib.request
from datetime import timedelta
from open_webui.utils.auth import create_token
owner=os.environ['HERMES_WORKBENCH_OWNER_ID']
token=create_token({'id':owner},expires_delta=timedelta(minutes=5))
checks={}
for path in ['/health','/api/version','/_app/version.json','/api/v1/auths/','/api/v1/chats/','/api/v1/hermes/config','/api/v1/hermes/sessions?limit=5','/api/v1/hermes/skills','/api/v1/hermes/jobs']:
 req=urllib.request.Request('http://127.0.0.1:8080'+path,headers={'Authorization':'Bearer '+token})
 with urllib.request.urlopen(req,timeout=30) as r:
  data=json.load(r);assert r.status==200
  checks[path]={'status':r.status}
  if path=='/_app/version.json':assert data['version']=='12298d1d128d45eb9177fb7a01657645253349fb'
  if path=='/api/v1/hermes/config':assert data['enabled']
print(json.dumps(checks))
