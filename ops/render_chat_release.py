"""Prepare a private candidate from the actual chat Compose; never deploy it."""
import argparse
import copy
import json
import os
from pathlib import Path
import re

IMAGE = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}')


def validate_compose(compose):
    if not isinstance(compose, dict) or not isinstance(compose.get('services'), dict):
        raise ValueError('Compose services must be an object')
    services = compose['services']
    if not {'postgres', 'litellm', 'openwebui', 'hermes-assistant'} <= services.keys():
        raise ValueError('base must contain the current four-service chat topology')
    for service in services.values():
        if (not isinstance(service, dict) or 'build' in service
                or not isinstance(service.get('image'), str)
                or not IMAGE.fullmatch(service['image'])):
            raise ValueError('all services require digest-pinned images without build directives')


def render_release(base, assistant_image, webui_image):
    if not all(IMAGE.fullmatch(image) for image in (assistant_image, webui_image)):
        raise ValueError('candidate images must use full sha256 digests')
    candidate = copy.deepcopy(base)
    # Check the original first: a stale bootstrap config is not a release base.
    validate_compose(candidate)
    candidate['services']['hermes-assistant']['image'] = assistant_image
    candidate['services']['openwebui']['image'] = webui_image
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', type=Path, help='validate an existing Compose without writing')
    parser.add_argument('--base', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--assistant-image')
    parser.add_argument('--webui-image')
    args = parser.parse_args()
    try:
        if args.check:
            if any((args.base, args.output, args.assistant_image, args.webui_image)):
                parser.error('--check cannot be combined with candidate options')
            validate_compose(json.loads(args.check.read_text()))
        else:
            if not all((args.base, args.output, args.assistant_image, args.webui_image)):
                parser.error('candidate preparation requires base, output and both images')
            candidate = render_release(json.loads(args.base.read_text()), args.assistant_image, args.webui_image)
            # Exclusive creation also prevents accidentally overwriting the base.
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(candidate, stream, indent=2)
                stream.write('\n')
    except (OSError, ValueError):
        # Configs can contain secrets. Never print input values or decoder context.
        parser.exit(1, 'release configuration rejected; check pinned images, topology and output path\n')
    print('chat release configuration valid; no deployment performed')


if __name__ == '__main__':
    main()
