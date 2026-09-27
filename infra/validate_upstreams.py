#!/usr/bin/env python3
"""Validate upstream inventory or a rendered deployment using only the stdlib.

Input .yaml files deliberately use JSON syntax, a YAML 1.2 subset. A Compose
deployment input is JSON produced by `docker compose config --format json`.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DIGEST_IMAGE = re.compile(r"[^\s@$]+@sha256:[0-9a-f]{64}\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class InvalidConfiguration(ValueError):
    pass


def load_document(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidConfiguration(f"cannot read JSON document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InvalidConfiguration(f"{path}: expected an object")
    return value


def file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise InvalidConfiguration(f"cannot hash {path}: {exc}") from exc


def validate(lock, mode, *, lock_path, root, compose=None, manifest=None):
    if lock.get("schema_version") != 1:
        raise InvalidConfiguration("unsupported lock schema_version")
    components = lock.get("components")
    if not isinstance(components, dict) or not components:
        raise InvalidConfiguration("components must be a nonempty object")
    enabled = {}
    for name, component in components.items():
        if not isinstance(component, dict):
            raise InvalidConfiguration(f"{name}: component must be an object")
        if type(component.get("enabled")) is not bool:
            raise InvalidConfiguration(f"{name}: enabled must be a boolean")
        source = component.get("source")
        if not isinstance(source, dict) or source.get("kind") not in {"git", "npm", "pypi", "local", "image"}:
            raise InvalidConfiguration(f"{name}: unsupported source kind")
        revision = source.get("revision")
        if revision is not None and (not isinstance(revision, str) or not GIT_SHA.fullmatch(revision)):
            raise InvalidConfiguration(f"{name}: git revision must be a full 40-character SHA")
        version = source.get("version")
        if version is not None and (not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise InvalidConfiguration(f"{name}: version must be exact, not a range or tag")
        if component.get("integration_status") not in {"not_integrated", "integrated"}:
            raise InvalidConfiguration(f"{name}: invalid integration_status")
        verification = component.get("verification")
        if not isinstance(verification, dict) or verification.get("status") not in {"not_run", "smoke_only", "passed"}:
            raise InvalidConfiguration(f"{name}: invalid verification status")
        deployment = component.get("deployment")
        if not isinstance(deployment, dict):
            raise InvalidConfiguration(f"{name}: deployment must be an object")
        if not isinstance(deployment.get("bindings"), list):
            raise InvalidConfiguration(f"{name}: deployment bindings must be an array")
        if mode == "deployment" and component.get("enabled"):
            image = deployment.get("image")
            if not isinstance(image, str) or not DIGEST_IMAGE.fullmatch(image):
                raise InvalidConfiguration(f"{name}: deployment image requires an exact sha256 digest")
            if source["kind"] == "git" and revision is None:
                raise InvalidConfiguration(f"{name}: enabled git source requires a revision")
            if source["kind"] in {"npm", "pypi", "local"} and version is None:
                raise InvalidConfiguration(f"{name}: enabled package requires an exact version")
            if component["integration_status"] != "integrated" or verification["status"] != "passed":
                raise InvalidConfiguration(f"{name}: enabled component requires passed integration verification")
            verify_evidence(name, verification.get("evidence"), root)
            if not deployment["bindings"]:
                raise InvalidConfiguration(f"{name}: enabled component requires Compose bindings")
            enabled[name] = deployment
    if mode == "catalog":
        return
    if not enabled:
        raise InvalidConfiguration("deployment requires at least one enabled component")
    if compose is None or manifest is None:
        raise InvalidConfiguration("deployment requires --compose and --manifest")
    validate_deployment(enabled, lock_path, compose, manifest)


def verify_evidence(name, evidence, root):
    if not isinstance(evidence, dict) or not isinstance(evidence.get("path"), str):
        raise InvalidConfiguration(f"{name}: verification evidence requires a path and sha256")
    expected = evidence.get("sha256")
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise InvalidConfiguration(f"{name}: verification evidence requires an exact sha256")
    relative = Path(evidence["path"])
    target = (root / relative).resolve()
    if relative.is_absolute() or not target.is_relative_to(root.resolve()):
        raise InvalidConfiguration(f"{name}: evidence path must remain inside --root")
    if file_sha256(target) != expected:
        raise InvalidConfiguration(f"{name}: verification evidence sha256 mismatch")


def validate_deployment(enabled, lock_path, compose_path, manifest_path):
    compose = load_document(compose_path)
    services = compose.get("services")
    if not isinstance(services, dict) or not services:
        raise InvalidConfiguration("Compose services must be a nonempty object")
    for service_name, service in services.items():
        if not isinstance(service, dict):
            raise InvalidConfiguration(f"{service_name}: Compose service must be an object")
        if "build" in service:
            raise InvalidConfiguration(f"{service_name}: deployment cannot rebuild an image; publish and pin its digest first")
        image = service.get("image")
        if not isinstance(image, str) or not DIGEST_IMAGE.fullmatch(image):
            raise InvalidConfiguration(f"{service_name}: Compose image requires an exact digest")
    covered_services = set()
    claimed_bindings = set()
    for name, deployment in enabled.items():
        for binding in deployment["bindings"]:
            if not isinstance(binding, dict):
                raise InvalidConfiguration(f"{name}: invalid Compose binding")
            service_name = binding.get("service")
            if not isinstance(service_name, str) or service_name not in services:
                raise InvalidConfiguration(f"{name}: bound Compose service is missing")
            field = binding.get("field")
            service = services[service_name]
            if field == "image" and set(binding) == {"service", "field"}:
                actual = service["image"]
                covered_services.add(service_name)
                key = (service_name, field)
            elif field == "environment" and set(binding) == {"service", "field", "name"}:
                env_name = binding["name"]
                environment = service.get("environment")
                if not isinstance(env_name, str) or not isinstance(environment, dict):
                    raise InvalidConfiguration(f"{name}: use resolved Compose environment mappings")
                actual = environment.get(env_name)
                key = (service_name, field, env_name)
            else:
                raise InvalidConfiguration(f"{name}: binding field must be image or a named environment entry")
            if key in claimed_bindings:
                raise InvalidConfiguration(f"{name}: duplicate Compose binding {key}")
            claimed_bindings.add(key)
            if actual != deployment["image"]:
                raise InvalidConfiguration(f"{name}: Compose binding image mismatch for {service_name}")
    if covered_services != set(services):
        raise InvalidConfiguration("all Compose service images must be bound to enabled components")
    manifest = load_document(manifest_path)
    if manifest.get("schema_version") != 1:
        raise InvalidConfiguration("unsupported deployment manifest schema_version")
    if manifest.get("lock_sha256") != file_sha256(lock_path):
        raise InvalidConfiguration("manifest lock_sha256 mismatch")
    if manifest.get("compose_sha256") != file_sha256(compose_path):
        raise InvalidConfiguration("manifest compose_sha256 mismatch")
    if manifest.get("components") != enabled:
        raise InvalidConfiguration("manifest components must exactly match enabled image and binding records")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("catalog", "deployment"), default="catalog")
    parser.add_argument("--lock", type=Path, default=REPO_ROOT / "infra/upstreams.lock.yaml")
    parser.add_argument("--compose", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="root for verification evidence paths")
    args = parser.parse_args(argv)
    try:
        validate(load_document(args.lock), args.mode, lock_path=args.lock, root=args.root,
                 compose=args.compose, manifest=args.manifest)
    except InvalidConfiguration as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID {args.mode}: {args.lock}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
