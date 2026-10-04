"""Record and verify assistant backups using the exact runtime image, without source imports."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def docker(*args):
    return subprocess.check_output(['docker', *map(str, args)], text=True).strip()


def immutable(image):
    return bool(re.fullmatch(r'sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64}', image))


def archive_hash(archive):
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def capture(container, archive, metadata):
    image_id = docker('inspect', '--format', '{{.Image}}', container)
    # Query only image identity, never container environment or credentials.
    digests = json.loads(docker('image', 'inspect', '--format', '{{json .RepoDigests}}', image_id)) or []
    if not immutable(image_id):
        raise ValueError('container has no immutable image identity')
    metadata.write_text(json.dumps({'schema_version': 1, 'image_id': image_id,
                                   'repo_digests': digests, 'archive_sha256': archive_hash(archive)}, indent=2)+'\n')


def restore(archive, metadata=None, image=None):
    record = json.loads(metadata.read_text()) if metadata and metadata.exists() else None
    if record is not None:
        if record['schema_version'] != 1 or archive_hash(archive) != record['archive_sha256']:
            raise ValueError('assistant backup metadata/hash mismatch')
        image = image or next(iter(record['repo_digests']), record['image_id'])
    if not image or not immutable(image):
        raise ValueError('explicit verified immutable assistant image required for legacy backup')
    try:
        identity = docker('image', 'inspect', '--format', '{{.Id}}', image)
    except subprocess.CalledProcessError:
        if '@sha256:' not in image:
            raise ValueError('recorded local image unavailable; load the saved image or provide its registry digest')
        docker('pull', image)
        identity = docker('image', 'inspect', '--format', '{{.Id}}', image)
    if record is not None and identity != record['image_id']:
        raise ValueError('restore image does not match recorded image identity')
    return docker('run', '--rm', '--network', 'none', '--read-only', '--user', '0:0',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                  '--memory', '768m', '--pids-limit', '128',
                  '--tmpfs', '/restore:rw,noexec,nosuid,size=512m',
                  '--mount', f'type=bind,src={archive.resolve()},dst=/backup.tar.gz,readonly',
                  '--entrypoint', 'python', image, '/opt/youwei-assistant/backup.py',
                  'verify', '/backup.tar.gz', '--destination', '/restore/data')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('capture')
    create.add_argument('--container', required=True)
    create.add_argument('--archive', required=True, type=Path)
    create.add_argument('--metadata', required=True, type=Path)
    verify = commands.add_parser('verify')
    verify.add_argument('--archive', required=True, type=Path)
    verify.add_argument('--metadata', type=Path)
    verify.add_argument('--image', help='Verified immutable image; mandatory for old backups without metadata')
    args = vars(parser.parse_args())
    command = args.pop('command')
    result = capture(**args) if command == 'capture' else restore(**args)
    if result:
        print(result)


if __name__ == '__main__':
    main()
