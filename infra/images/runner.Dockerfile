# The privileged Runner has its own dependency lock and no Core package.
ARG PYTHON_IMAGE=python:3.13-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.16
ARG DOCKER_CLI_IMAGE=docker:29-cli

FROM ${UV_IMAGE} AS uv
FROM ${DOCKER_CLI_IMAGE} AS docker_cli
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/services/sandbox-runner
COPY contracts/ /app/contracts/
COPY services/sandbox-runner/ /app/services/sandbox-runner/
RUN uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/app/services/sandbox-runner/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/services/sandbox-runner
COPY --from=build /app/services/sandbox-runner/.venv /app/services/sandbox-runner/.venv
# Copy the client binary, not a Docker daemon. Only this service gets the socket.
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
EXPOSE 8091
CMD ["youwei-runner"]
