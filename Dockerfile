# syntax=docker/dockerfile:1
# One image for the api, the worker and the MCP news server; they differ only
# by command (and MIA_COMPONENT, which picks the health check).
#   runtime: slim production image (Day 18 adds a managed identity)
#   local:   runtime + Azure CLI, so containers can reuse the host's `az login`
#
# Base images are pinned by digest (multi-arch index: amd64 + arm64), so a
# rebuild can't silently pick up a different base. Bump deliberately:
#   docker buildx imagetools inspect python:3.12-slim

ARG PYTHON_IMAGE=python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.20@sha256:100047e74f30778ab704942321a09750d6158739573ff58bf3924085cc6cd2d8

FROM ${UV_IMAGE} AS uv

# ---------- builder: resolve and install locked dependencies ----------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Main dependencies only: no dev tools, no ingestion (PDF parsing), no UI.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-install-project

# ---------- runtime: non-root, app code only, no toolchain, no secrets ----------
FROM ${PYTHON_IMAGE} AS runtime
# The app runs from its own virtualenv; the base image's pip/setuptools are a
# package manager nobody needs at runtime, so they go.
RUN python -m pip uninstall --yes --quiet pip setuptools wheel 2>/dev/null || true \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY mcp_news ./mcp_news
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MIA_COMPONENT=api
USER 10001
EXPOSE 8000 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]
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
USER 10001
ENTRYPOINT ["/usr/local/bin/entrypoint-local.sh"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
