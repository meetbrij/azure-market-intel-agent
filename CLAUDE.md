# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is
A research agent for public companies. A user asks a question and gets back a structured report in which every claim cites a specific page of an SEC 10-K (from Azure AI Search) or a news URL. FastAPI queues the job in Redis (arq). A worker runs a LangGraph agent: `plan → (retrieve_filings ∥ fetch_news) → compact → approve_gate (human interrupt) → write → critique`, and the critic can loop back to `plan`, capped at `MAX_LOOPS=2`. State is checkpointed in Postgres with `thread_id = job id`, so jobs resume after a worker crash. Finished reports are archived immutably to Blob. The corpus is the FY2025/26 10-Ks of Amazon, Alphabet and Microsoft. Phases 1–3 are complete: evals, Entra ID auth, tracing, a Helm chart, and a deployment on Azure Container Apps with Azure Pipelines CI/CD. See `docs/PHASE*_SPEC.md`, `docs/ROADMAP.md` and `docs/DECISIONS.md`.

## Layout
- `app/graph/`: agent nodes, prompts, state, retrieval, and graph build. `app/jobs/`: arq worker and job store. `app/api/`: FastAPI. `app/reports/`: archive and Markdown rendering.
- `ingestion/`: one-off CLI (Blob PDFs → pypdf → per-page chunks → embeddings → the `filings-v1` index).
- `mcp_news/`: a standalone FastMCP server wrapping Tavily, called over stdio.
- `ui/`: Streamlit client. It talks to the API **only over HTTP** (`ui/api_client.py`), runs in its own image and dependency group, and must never import `app`.
- `evals/`: 30-question golden set and harness. RAGAS runs in its **own uv env** (`evals/ragas/`) because it needs `openai<3`.

## Commands
```bash
uv run pytest                                   # offline: SQLite, in-memory checkpointer, mocked LLM/Search/news/queue
uv run pytest tests/test_nodes.py::test_name    # single test
uv run ruff check app ingestion mcp_news tests ui
uv run mypy app ingestion mcp_news tests --ignore-missing-imports
uv run --group ui mypy ui --ignore-missing-imports
uv run python -m evals.run --smoke              # eval gate, real model calls; fails below evals/thresholds.yaml
docker compose up -d --build                    # api :8000, worker, ui :8501, postgres, redis
uv run python -m app.graph.build "question" --yes   # run the graph alone (auto-approve)
```

## Rules that aren't obvious
- **Citations are grounded in Python.** The writer cites by reference id only. Company, page and period are filled in from the retrieved evidence, and unretrieved ids are dropped. Deterministic critic checks override the LLM. Never let the model compute numbers.
- **Degrade, don't fail.** Search or news outages go into `data_gaps` and the run completes. Only `plan`, `compact`, `write` and `critique` failures fail the job.
- **Keyless auth.** Use `DefaultAzureCredential` everywhere. Deployment names come from env vars, and the Tavily key lives only in Key Vault.
- Report schema changes are additive-only. Archived blobs are never overwritten.
- Retrieval changes need before/after numbers from `evals.run --variant ...` (see `docs/retrieval-benchmark.md`).
- Each significant decision gets an entry in `docs/DECISIONS.md` (plus an ADR in `docs/adr/` if it's architectural). Accepted gaps go in `docs/KNOWN_LIMITATIONS.md`.
- `tests/conftest.py` sets dummy env vars before importing `app`, because some modules read settings at import time.
