"""Platform backup orchestration never imports the assistant implementation."""
import hashlib
import json
import subprocess

import pytest

from ops.backup import assistant_image


IMAGE = 'sha256:' + 'a' * 64
DIGEST = 'registry.example/assistant@sha256:' + 'b' * 64


def test_capture_and_restore_use_recorded_identity_and_network_isolation(tmp_path, monkeypatch):
    archive = tmp_path/'data.tar.gz'; archive.write_bytes(b'backup')
    metadata = tmp_path/'image.json'
    calls = []
    def docker(*args):
        calls.append(args)
        if '{{json .RepoDigests}}' in args:
            return json.dumps([DIGEST])
        if args[0] == 'run':
            return '{"databases":{"state.db":1}}'
        return IMAGE
    monkeypatch.setattr(assistant_image, 'docker', docker)
    assistant_image.capture('running', archive, metadata)
    record = json.loads(metadata.read_text())
    assert record['image_id'] == IMAGE
    assert record['archive_sha256'] == hashlib.sha256(b'backup').hexdigest()
    assert json.loads(assistant_image.restore(archive, metadata))['databases']['state.db'] == 1
    run = calls[-1]
    assert run[run.index('--network') + 1] == 'none'
    assert '--read-only' in run and '--cap-drop' in run
    assert run[run.index('--mount') + 1] == f'type=bind,src={archive},dst=/backup.tar.gz,readonly'
    assert DIGEST in run and '/opt/youwei-assistant/backup.py' in run


@pytest.mark.parametrize('image', [None, 'latest', 'assistant:verified'])
def test_legacy_requires_explicit_immutable_image(tmp_path, image):
    archive = tmp_path/'old.tar.gz'; archive.write_bytes(b'backup')
    with pytest.raises(ValueError, match='explicit verified immutable'):
        assistant_image.restore(archive, image=image)


def test_mismatch_fails_before_container_start(tmp_path, monkeypatch):
    archive = tmp_path/'data.tar.gz'; archive.write_bytes(b'backup')
    metadata = tmp_path/'image.json'
    record = dict(schema_version=1, image_id=IMAGE, repo_digests=[], archive_sha256='bad')
    metadata.write_text(json.dumps(record))
    calls = []
    monkeypatch.setattr(assistant_image, 'docker', lambda *args: calls.append(args) or 'sha256:'+'c'*64)
    with pytest.raises(ValueError, match='hash mismatch'):
        assistant_image.restore(archive, metadata)
    assert not calls
    record['archive_sha256'] = hashlib.sha256(b'backup').hexdigest()
    metadata.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='image identity'):
        assistant_image.restore(archive, metadata)
    assert not any(args[0] == 'run' for args in calls)


def test_legacy_explicit_digest_pulls_then_restores(tmp_path, monkeypatch):
    archive = tmp_path/'old.tar.gz'; archive.write_bytes(b'backup')
    calls = []
    def docker(*args):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, 'docker')
        return '{}'
    monkeypatch.setattr(assistant_image, 'docker', docker)
    assistant_image.restore(archive, image=DIGEST)
    assert calls[1] == ('pull', DIGEST)
    assert calls[-1][0] == 'run'
