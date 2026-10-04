"""Synthetic OpenAI endpoint for isolated compatibility checks; no paid calls."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class MockModel(BaseHTTPRequestHandler):
    histories = []
    audits = []
    slow_started = threading.Event()

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/audit':
            data=json.dumps(self.audits).encode()
            self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if os.environ.get('MOCK_MODELS_UNAVAILABLE') == '1':
            self.send_response(503); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(json.dumps({'object': 'list', 'data': [{'id': 'glm-5.3', 'object': 'model'}]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if 'messages' not in body:
            self.send_response(404); self.end_headers(); return
        messages = body['messages']; self.histories.append(messages)
        self.audits.append({'model':body.get('model'), 'tools': [t['function']['name'] for t in body.get('tools',[])], 'assistant_key':self.headers.get('Authorization') == 'Bearer mock-model-key-123456789'})
        last_user = max(i for i, m in enumerate(messages) if m['role'] == 'user')
        query = messages[last_user]['content']
        if isinstance(query, list):
            query = '\n'.join(p.get('text', '') for p in query)
        if query == 'SLOW':
            self.slow_started.set(); time.sleep(3)
        if query.startswith('VERIFY:') and not any(m['role'] == 'tool' for m in messages[last_user + 1:]):
            command = json.loads(query[7:])
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'call-' + str(len(self.histories)), 'type': 'function',
                'function': {'name': command['tool'], 'arguments': json.dumps(command['args'])}}]}
            finish = 'tool_calls'
        else:
            content = messages[-1].get('content', '') if messages[-1]['role'] == 'tool' else 'Follow-up received'
            message = {'role': 'assistant', 'content': str(content)}; finish = 'stop'
        common = dict(id='mock', object='chat.completion', created=int(time.time()), model='glm-5.3')
        self.send_response(200)
        if body.get('stream'):
            self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            delta = dict(message)
            if 'tool_calls' in delta:
                delta['tool_calls'][0]['index'] = 0
            for item in [dict(common, choices=[{'index': 0, 'delta': delta, 'finish_reason': None}]),
                         dict(common, choices=[{'index': 0, 'delta': {}, 'finish_reason': finish}],
                              usage={'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20})]:
                self.wfile.write(('data: ' + json.dumps(item) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(dict(common, choices=[{'index': 0, 'message': message, 'finish_reason': finish}],
                                            usage={'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20})).encode())


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 4000), MockModel).serve_forever()
