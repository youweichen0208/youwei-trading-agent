import json,os,urllib.request,urllib.error,sqlite3
from datetime import timedelta
from open_webui.utils.auth import create_token
owner=os.environ['HERMES_WORKBENCH_OWNER_ID']
token=create_token({'id':owner},expires_delta=timedelta(minutes=5))
checks={}
for path in ['/health','/api/version','/_app/version.json','/api/v1/auths/','/api/v1/chats/','/api/v1/hermes/config','/api/v1/hermes/sessions?limit=5','/api/v1/hermes/skills','/api/v1/hermes/jobs']:
 req=urllib.request.Request('http://127.0.0.1:8080'+path,headers={'Authorization':'Bearer '+token})
 with urllib.request.urlopen(req,timeout=30) as r:
  data=json.load(r); assert r.status==200; checks[path]=r.status
  if path=='/_app/version.json': assert data['version']=='7a868661e41563f1bae33ddbfdcdd061a66cc5a5'
  if path=='/api/v1/hermes/config': assert data['enabled']
try:
 urllib.request.urlopen('http://127.0.0.1:8080/api/v1/hermes/config',timeout=10)
 raise AssertionError('anonymous access allowed')
except urllib.error.HTTPError as e:
 assert e.code==401; checks['anonymous_workbench']=401
with sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True) as db:
 configs=json.loads(db.execute("SELECT value FROM config WHERE key='openai.api_configs'").fetchone()[0])
 matches=[v for v in configs.values() if v.get('hermes_chat')]
 assert len(matches)==1 and matches[0]['hermes_owner_id']==owner
checks['hermes_chat_enabled']=True
req=urllib.request.Request('http://127.0.0.1:8080/openai/responses',data=json.dumps({'model':'Hermes 美股助手','input':'must not reach a model'}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
try:
 urllib.request.urlopen(req,timeout=10)
 raise AssertionError('raw Responses bypass allowed')
except urllib.error.HTTPError as e:
 assert e.code==403; checks['raw_responses_blocked']=403
print(json.dumps(checks))
