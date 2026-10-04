"""Release preparation must preserve deployment-owned configuration and data paths."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ops.render_chat_release import render_release

DIGEST = 'sha256:' + 'a' * 64


def compose():
    return {'services': {name: {'image': f'registry/{name}@{DIGEST}',
        'environment': {'PRIVATE_VALUE': 'do-not-print'},
        'volumes': [f'{name}_existing:/data']}
        for name in ('postgres', 'litellm', 'openwebui', 'hermes-assistant')},
        'volumes': {'existing': {'external': True}}, 'networks': {'chat': {'internal': True}}}


def test_only_requested_images_change():
    source = compose()
    expected = copy.deepcopy(source)
    expected['services']['openwebui']['image'] = f'webui@{DIGEST}'
    expected['services']['hermes-assistant']['image'] = f'assistant@{DIGEST}'
    assert render_release(source, f'assistant@{DIGEST}', f'webui@{DIGEST}') == expected
    assert source == compose()


@pytest.mark.parametrize('image', ['latest', 'registry/assistant:main', 'image@sha256:abc'])
def test_reject_floating_or_invalid_images(image):
    with pytest.raises(ValueError):
        render_release(compose(), image, f'webui@{DIGEST}')


def test_reject_old_topology_and_build():
    old = compose()
    del old['services']['hermes-assistant']
    with pytest.raises(ValueError):
        render_release(old, f'assistant@{DIGEST}', f'webui@{DIGEST}')
    old = compose()
    old['services']['postgres']['build'] = '.'
    with pytest.raises(ValueError):
        render_release(old, f'assistant@{DIGEST}', f'webui@{DIGEST}')


def test_cli_private_output_and_no_overwrite(tmp_path):
    source, output = tmp_path/'base.json', tmp_path/'candidate.json'
    source.write_text(json.dumps(compose()))
    cmd = [sys.executable, 'ops/render_chat_release.py', '--base', str(source),
           '--output', str(output), '--assistant-image', f'assistant@{DIGEST}',
           '--webui-image', f'webui@{DIGEST}']
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'do-not-print' not in result.stdout + result.stderr
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    assert subprocess.run(cmd, capture_output=True).returncode != 0
    assert output.read_bytes() == before
    assert json.loads(source.read_text()) == compose()


@pytest.mark.parametrize('source', [[], {}, {'services': []}, {'services': {
    'postgres': {}, 'litellm': {}, 'openwebui': {}, 'hermes-assistant': None}}])
def test_reject_malformed_compose(source):
    with pytest.raises(ValueError):
        render_release(source, f'assistant@{DIGEST}', f'webui@{DIGEST}')
