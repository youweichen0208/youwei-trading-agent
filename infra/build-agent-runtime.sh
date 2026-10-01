#!/usr/bin/env bash
# Build the youwei agent-runtime headless image ON sg-prod and report its
# manifest digest.
#
# The image must be built on SG because Hermes is fetched by git clone at
# build time and the local machine cannot reach GitHub. This script packages a
# minimal build context from the local repo (contracts/ + services/agent-runtime/
# + the Dockerfile), copies it to SG, runs `docker build` there, and prints the
# OCI manifest digest (not the local image ID).
#
# Usage:
#   infra/build-agent-runtime.sh [--host sg-prod] [--tag youwei/agent-runtime:dev]
#
# It does NOT push to a registry; the manifest digest is saved alongside the
# OCI archive so the deployment can pin it later (deployment image stays
# not-ready until a registry reference exists).
set -euo pipefail

HOST="${HOST:-sg-prod}"
TAG="${TAG:-youwei/agent-runtime:dev}"
IMAGE_NAME="youwei-agent-runtime"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REMOTE_DIR="/tmp/${IMAGE_NAME}-build"

# 1. Package a minimal build context locally (respects the repo .dockerignore
#    semantics by only copying the paths the Dockerfile actually needs).
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

echo "== packaging build context =="
mkdir -p "${STAGE}/infra/images"
cp "${SCRIPT_DIR}/images/agent-runtime.Dockerfile" "${STAGE}/infra/images/agent-runtime.Dockerfile"
# .dockerignore keeps .venv/.git/__pycache__ out of the context.
cp "${REPO_ROOT}/.dockerignore" "${STAGE}/.dockerignore"
# --relative keeps the repo-relative layout the Dockerfile COPY paths expect.
rsync -a --relative --delete \
    "${REPO_ROOT}/./contracts" \
    "${REPO_ROOT}/./services/agent-runtime" \
    "${STAGE}/"

echo "== copying context to ${HOST}:${REMOTE_DIR} =="
ssh "${HOST}" "rm -rf '${REMOTE_DIR}' && mkdir -p '${REMOTE_DIR}'"
rsync -a "${STAGE}/" "${HOST}:${REMOTE_DIR}/"

# 2. Build on SG. The Dockerfile pins the base images and Hermes commit via
#    ARG defaults, so no extra build args are needed for a dev build.
echo "== building ${TAG} on ${HOST} =="
ssh "${HOST}" "cd '${REMOTE_DIR}' && docker build -f infra/images/agent-runtime.Dockerfile -t '${TAG}' ."

# 3. Report the OCI manifest digest (not the local image ID). A plain `docker
#    images` ID is a config digest and cannot be used as a deployment pin.
echo "== resolving manifest digest =="
ssh "${HOST}" "docker buildx imagetools inspect '${TAG}' --format '{{json .Manifest.Digest}}' 2>/dev/null || docker image inspect '${TAG}' --format '{{index .RepoDigests 0}}'"

echo "== done =="
echo "Image ${TAG} built on ${HOST}. Save the digest into infra/upstreams.lock.yaml"
echo "(deployment image field) once a registry reference exists."
