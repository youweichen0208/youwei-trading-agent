"""Run the fixed assistant acceptance through WebUI's Responses delegation route.

Only the test client changes; installed runtime/source equality checks remain.
Run inside the candidate with assistant source mounted at /verification.
"""
from pathlib import Path
import sys

source = Path('/verification/ops/verify_assistant_native.py')
sys.path.insert(0, str(source.parent))
text = source.read_text()
old = """                    response = client.post('/v1/chat/completions', json={'model':'Hermes 美股助手', 'messages':[
                        {'role':'user','content':'VERIFY:'+json.dumps({'tool':tool,'args':args})}]})
                    assert response.status_code == 200, response.text
                    content = response.json()['choices'][0]['message']['content']"""
new = """                    if tool == 'delegate_task':
                        response = client.post('/v1/responses', headers={'X-Hermes-Session-Key':'webui:isolated-parallel-acceptance'}, json={
                            'model':'Hermes 美股助手','store':False,'stream':False,
                            'input':[{'role':'user','content':'VERIFY:'+json.dumps({'tool':tool,'args':args})}]})
                        assert response.status_code == 200, response.text
                        content = '\\n'.join(part.get('text','') for item in response.json().get('output',[]) if item.get('type')=='message' for part in item.get('content',[]))
                    else:
                        response = client.post('/v1/chat/completions', json={'model':'Hermes 美股助手', 'messages':[
                            {'role':'user','content':'VERIFY:'+json.dumps({'tool':tool,'args':args})}]})
                        assert response.status_code == 200, response.text
                        content = response.json()['choices'][0]['message']['content']"""
assert text.count(old) == 1, 'fixed acceptance source changed'
namespace = {'__file__':str(source),'__name__':'parallel_responses_acceptance'}
exec(compile(text.replace(old,new),str(source),'exec'),namespace)
namespace['verify'](Path('/opt/hermes'))
print('PASS Responses store=false: parallel children joined, restricted discovery and rejected platform submit')
