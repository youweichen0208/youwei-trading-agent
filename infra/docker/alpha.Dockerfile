FROM ghcr.io/astral-sh/uv:0.11.16@sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d AS uv
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753 AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY services/alpha-engine services/alpha-engine
COPY services/tools/src services/tools/src
RUN uv sync --locked --no-dev --no-editable

FROM build AS tools-build
RUN uv sync --locked --no-dev --extra tools --no-editable

FROM build AS training-build
RUN uv sync --locked --no-dev --extra training --no-editable

FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753 AS base-runtime
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
USER 10001:10001

FROM base-runtime AS migrator
COPY --chown=10001:10001 infra/migrations /app/infra/migrations
ENTRYPOINT ["python", "-m", "yta_alpha.migrate"]

FROM base-runtime AS runtime
EXPOSE 8100
CMD ["uvicorn", "yta_alpha.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]

FROM base-runtime AS training-runtime
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=training-build --chown=10001:10001 /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8100
CMD ["uvicorn", "yta_alpha.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]

FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753 AS tools
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=tools-build --chown=10001:10001 /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8200
CMD ["uvicorn", "yta_tools.http:create_http_app", "--factory", "--host", "0.0.0.0", "--port", "8200", "--no-access-log"]
