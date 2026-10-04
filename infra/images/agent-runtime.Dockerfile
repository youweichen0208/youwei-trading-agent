# youwei agent-runtime: headless Hermes research subprocess (S07).
#
# This is NOT the upstream Hermes product image (s6-overlay + TUI/web +
# Chromium + desktop). It is the minimal Python 3.14 image that runs the
# one-shot research entrypoint `youwei-agent-runtime research-once`, which
# constructs an isolated Hermes AIAgent (run_agent.AIAgent + tools.registry),
# calls the OpenAI-compatible gateway, and returns a ResearchProposal over
# stdin/stdout. No TUI, no browser, no dashboard, no supervisor.
#
# Dependency strategy (single venv, Hermes lock is authoritative):
#   - Hermes is source-installed from a pinned commit with `uv sync --frozen`
#     (its uv.lock pins pydantic ==2.13.4 among the full dependency set).
#   - youwei-contracts and youwei-agent-runtime are then installed INTO that
#     same venv with `--no-deps`, because their pydantic requirement
#     (>=2.10,<3) is already satisfied by Hermes's pin. This avoids a second
#     `uv sync` from a second lock silently upgrading pydantic and breaking
#     Hermes's exact pin.
#
# Build args are development defaults; release builds override the base
# images with their pinned digests (recorded in infra/upstreams.lock.yaml).

# The multi-arch index digest for python:3.14-slim, resolved on sg-prod.
ARG PYTHON_IMAGE=python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
# uv pinned to the same revision family as Core (uv.lock revision = 3).
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.16@sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d
# Hermes commit pinned in infra/upstreams.lock.yaml.
ARG HERMES_REVISION=7fa45eb349a1a6f1eebc010b3fef0a9d996f386a

FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS build

# ARGs are scoped per-stage; re-declare them so the build stage can use the
# values pinned above.
ARG PYTHON_IMAGE
ARG UV_IMAGE
ARG HERMES_REVISION

COPY --from=uv /uv /usr/local/bin/uv
# copy link mode so the venv is relocatable to the runtime stage; never let uv
# fetch a Python interpreter — the base image already provides Python 3.14.
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /opt

# Fetch the pinned Hermes source and verify the commit. python:*-slim images
# do not ship git, so install it (with ca-certificates for the clone) first.
# Fetch only the pinned SHA (shallow) rather than the full history.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    git init /opt/hermes && \
    cd /opt/hermes && \
    git remote add origin https://github.com/NousResearch/hermes-agent && \
    git fetch --quiet --depth 1 origin "${HERMES_REVISION}" && \
    git checkout --quiet FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = "${HERMES_REVISION}"

# youwei local patch (minimal fork, recorded in infra/upstreams.lock.yaml):
# stash the provider-returned model id on the agent so the research adapter
# can record actual model attribution per report (owner decision D2,
# 2026-10-03). The upstream exposes no stable surface for the response's
# model id (chat() returns a string; the turn result dict and history
# messages carry no model). --check first: an upstream bump that breaks the
# patch fails the build instead of silently dropping the attribution.
COPY infra/images/hermes-last-turn-model.patch /tmp/hermes-last-turn-model.patch
RUN git -C /opt/hermes apply --check /tmp/hermes-last-turn-model.patch && \
    git -C /opt/hermes apply /tmp/hermes-last-turn-model.patch

# Install Hermes's pinned Python dependencies into /opt/hermes/.venv.
# `--no-dev` keeps out the dev group; core [project].dependencies are enough
# for the research role (openai/httpx/rich/etc.). If the research path needs a
# provider-specific extra, it is added here and recorded in the lock notes.
WORKDIR /opt/hermes
RUN uv sync --frozen --no-dev --python /usr/local/bin/python

# Install this project's two packages into the SAME venv, without touching
# pydantic (already pinned by Hermes). Both are hatchling wheel projects.
COPY contracts/ /opt/youwei-contracts/
COPY services/agent-runtime/ /opt/youwei-agent-runtime/
RUN uv pip install --no-deps \
        --python /opt/hermes/.venv/bin/python \
        /opt/youwei-contracts \
        /opt/youwei-agent-runtime

# The research link signs/verifies with Ed25519 (research_capability.py),
# which needs cryptography. Hermes's own uv.lock already pins it (==50.0.1,
# via alibabacloud transitively); `--no-deps` above did NOT install the
# [signing] extra, so VERIFY the version Hermes provided rather than
# installing a second copy (a forced downgrade would fight Hermes's pin).
RUN /opt/hermes/.venv/bin/python -c \
        "import cryptography; assert cryptography.__version__ == '50.0.1', cryptography.__version__"

# The runtime stage only needs the venv + Hermes source; drop .git and the
# build-only apt tooling that would otherwise bloat the final image.
RUN rm -rf /opt/hermes/.git

FROM ${PYTHON_IMAGE} AS runtime

# Headless subprocess: no bytecode writes, unbuffered stdout, logs on stderr.
ENV PATH="/opt/hermes/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/opt/hermes" \
    HOME="/tmp" \
    HERMES_HOME="/tmp/hermes-home"

WORKDIR /opt/hermes
COPY --from=build /opt/hermes /opt/hermes

# Run as a non-root user with a writable scratch dir for transient state
# (memory/session search are disabled, but the agent still writes temp files).
RUN useradd --uid 10001 --create-home --home-dir /tmp --shell /usr/sbin/nologin agent && \
    mkdir -p /tmp/hermes-home && chown -R agent:agent /tmp/hermes-home
USER 10001:10001

ENTRYPOINT ["youwei-agent-runtime"]
