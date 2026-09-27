# Development defaults only. Release builds override base references with digests.
ARG PYTHON_IMAGE=python:3.13-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.16

FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY contracts/ contracts/
# The root lock knows this development source. It is NOT installed with --no-dev.
COPY services/sandbox-runner/pyproject.toml services/sandbox-runner/pyproject.toml
COPY youwei_core/ youwei_core/
COPY quant/ quant/
RUN uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY migrations/ migrations/
COPY alembic.ini ./
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "youwei_core.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
