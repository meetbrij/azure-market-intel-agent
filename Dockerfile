# syntax=docker/dockerfile:1
# One image for both api and worker; they differ only by command.
#   runtime: slim production image (Phase 4 adds a managed identity)
#   local:   runtime + Azure CLI, so containers can reuse the host's `az login`

ARG PYTHON_IMAGE=python:3.12-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12

FROM ${UV_IMAGE} AS uv

# ---------- builder: resolve and install locked dependencies ----------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------- runtime: non-root, app code only, no secrets ----------
FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------- local: adds Azure CLI for AzureCliCredential (dev only) ----------
FROM runtime AS local
USER root
COPY --from=uv /uv /bin/uv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/azure-cli \
    && uv pip install --python /opt/azure-cli/bin/python azure-cli \
    && ln -s /opt/azure-cli/bin/az /usr/local/bin/az \
    && rm /bin/uv
COPY --chmod=755 docker/entrypoint-local.sh /usr/local/bin/entrypoint-local.sh
ENV AZURE_CONFIG_DIR=/home/app/.azure \
    AZURE_CORE_COLLECT_TELEMETRY=false
USER app
ENTRYPOINT ["/usr/local/bin/entrypoint-local.sh"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
