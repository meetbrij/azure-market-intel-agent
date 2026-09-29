# Roadmap

The four items below were deferred on purpose in the Phase 3 spec: they're
increments to the system, not gaps in it. Each has an intended approach. The
smaller known gaps are in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## 1. Chunking strategy comparison

**Today:** one chunk per page region (1,000 characters, 150 overlap), with
a page number on every chunk (D-04).

**Approach:**
- Build two more indexes side by side:
  - one with section-aware chunks (split on 10-K item headings, then by
    size);
  - one with larger chunks that carry a parent-page summary.
- Run the existing harness on each (`evals.run --variant …` with an index
  override). Compare context precision and recall, page hit and cost, as
  `docs/retrieval-benchmark.md` did for hybrid search.
- **Watch:** the Free Search tier's 50 MB (62% used). Test variants on a
  Basic tier, or drop to 512-dimension embeddings.

## 2. MLflow experiment tracking

**Today:** eval runs are JSON and Markdown files under `results/`, and
Langfuse holds traces.

**Approach:**
- Log each `evals.run` as an MLflow run:
  - parameters: variant, top-k, index, chat and embedding deployments,
    commit;
  - metrics: every summary figure;
  - artifact: the results JSON.
- Keep a local file store or an Azure ML workspace as the backend.
- Langfuse stays the per-request view; MLflow becomes the "which
  configuration won, and when" view.
- A thin `--mlflow` flag keeps it optional, like `--trace`.

## 3. Airflow DAG for ingestion

**Today:** ingestion is a one-off CLI, run by hand with `--recreate`.

**Approach:**
- A DAG (or a Fabric / Data Factory pipeline) with these steps: detect new
  filings in Blob → parse and chunk → embed → upsert into a **new** index
  version → run the smoke eval against it → switch `AZURE_SEARCH_INDEX` only
  if the gate passes.
- That also fixes two current limitations: stale chunks on re-ingest, and
  `--recreate` emptying the live index.

## 4. Cross-job long-term memory

**Today:** each job starts cold. The checkpointer is per-thread (per-job)
memory only.

**Approach:**
- A LangGraph store (Postgres-backed, in the same database) keyed by user
  and company, holding:
  - reviewer notes that were accepted ("always compare to the prior fiscal
    year");
  - facts already verified with their citations.
- The planner reads it; the writer never cites it directly, only the
  evidence it points back to (the grounding rules still apply).
- Scoped per user, with deletion on request.
