"""Exercise the immutable location using an isolated Nginx 1.18 instance.

Run on the edge host: python3 ops/verify_chat_edge.py --config <candidate site>
Uses loopback ephemeral ports and a temporary cache; never reloads production.
"""
import argparse
import gzip
import json
import re
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Assets(BaseHTTPRequestHandler):
    def do_GET(self):
        status = int(self.path.rsplit('/', 1)[-1])
        body = b'fixed hashed asset'
        compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
        if compressed:
            body = gzip.compress(body)
        self.send_response(status)
        self.send_header('Vary', 'Accept-Encoding')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body) if status != 304 else 0))
        self.end_headers()
        if status != 304:
            self.wfile.write(body)

    def log_message(self, *_):
        pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def verify(source):
    assert 'listen 443 ssl http2;' in source
    cache_map = re.search(r'map \$status \$chat_immutable_cache_control \{.*?\n\}', source, re.S).group()
    location = source.split('    location /_app/immutable/ {', 1)[1].split('\n    }', 1)[0]
    backend = ThreadingHTTPServer(('127.0.0.1', 0), Assets)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    location = location.replace('https://sg_chat_backend', f'http://127.0.0.1:{backend.server_port}')
    opener = urllib.request.build_opener(NoRedirect())
    evidence = []
    with tempfile.TemporaryDirectory(prefix='youwei-edge-verify-') as work:
        root = Path(work)
        config = root/'nginx.conf'
        config.write_text(f'''user root;
worker_processes 1;
pid {root}/nginx.pid;
error_log {root}/error.log;
events {{ worker_connections 128; }}
http {{
    access_log off;
    proxy_temp_path {root}/proxy;
    proxy_cache_path {root}/cache keys_zone=chat_static:1m;
    {cache_map}
    server {{
        listen 127.0.0.1:{port};
        location /_app/immutable/ {{{location}
        }}
    }}
}}
''')
        subprocess.run(['nginx', '-t', '-c', str(config)], check=True, capture_output=True)
        nginx = subprocess.Popen(['nginx', '-c', str(config), '-g', 'daemon off;'])
        def request(status, encoding='identity'):
            req = urllib.request.Request(f'http://127.0.0.1:{port}/_app/immutable/{status}', headers={'Accept-Encoding': encoding})
            try:
                result = opener.open(req, timeout=3)
            except urllib.error.HTTPError as error:
                result = error
            with result:
                body = result.read()
                return result.status, dict(result.headers), body
        try:
            for _ in range(50):
                try:
                    request(200)
                    break
                except urllib.error.URLError:
                    time.sleep(.1)
            else:
                raise RuntimeError('isolated nginx not ready')
            for status in (200, 206, 304, 302, 404, 500):
                actual, headers, _ = request(status)
                assert actual == status, (actual, status)
                cache = headers.get('Cache-Control', '')
                assert ('max-age=31536000' in cache and 'immutable' in cache) == (status in (200, 206, 304)), (status, headers)
                evidence.append({'status': status, 'cache_control': cache})
            for encoding in ('gzip', 'identity', 'gzip', 'identity'):
                _, headers, body = request(200, encoding)
                assert (headers.get('Content-Encoding') == 'gzip') == (encoding == 'gzip')
                assert (gzip.decompress(body) if encoding == 'gzip' else body) == b'fixed hashed asset'
                assert 'Accept-Encoding' in headers['Vary']
            assert headers['X-Cache-Status'] == 'HIT', headers
            print(json.dumps({'passed': True, 'status_cases': evidence, 'proxy_hit_and_encoding_variants': True}, indent=2))
        finally:
            nginx.terminate()
            nginx.wait(timeout=10)
            backend.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    verify(parser.parse_args().config.read_text())
