# Archive: build specs

The specs the project was built from, one day at a time, between
2026-09-23 and 2026-09-29. The project is complete, so they're no longer
instructions. They're kept as the record of what was planned. Each one notes
where the build deviated from it, and `DECISIONS.md` and
`evals/golden_set.yaml` refer back to them.

| Spec | Days | Scope |
|---|---|---|
| [PHASE1_SPEC.md](PHASE1_SPEC.md) | 1–5 | Thin slice: ingestion, a linear graph, API + worker, containers |
| [PHASE2_SPEC.md](PHASE2_SPEC.md) | 6–11 | Full agent graph, MCP news server, checkpointing, human approval, resilience |
| [PHASE2_UI_SPEC.md](PHASE2_UI_SPEC.md) | 12 | Streamlit client and the immutable report archive |
| [PHASE3_SPEC.md](PHASE3_SPEC.md) | 13–18 | Evals, hybrid search, Entra ID auth, tracing, Helm, Azure deployment and CI/CD |

What was actually built, and why: [DECISIONS.md](../DECISIONS.md) and
[ADRs](../adr/). Current status: the [README](../../README.md#project-status).
