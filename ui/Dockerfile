# syntax=docker/dockerfile:1
# Streamlit client. Its own slim image with only the `ui` dependency group
# (streamlit, httpx): it can't import the graph or touch Postgres even by
# accident; it talks to the API over HTTP. Build context is the repo root.

ARG PYTHON_IMAGE=python:3.12-slim
FROM ghcr.io/astral-sh/uv:0.12 AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --only-group ui --no-install-project

FROM ${PYTHON_IMAGE}
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY ui ./ui
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
USER app
EXPOSE 8501
CMD ["streamlit", "run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
