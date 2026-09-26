# ADR 0007: Immutable report archive in Blob Storage

- **Status:** Accepted
- **Date:** 2026-09-26
- **Phase:** 2 (Day 12, Streamlit client)

## Context

The report is what the business actually uses. In a regulated setting you
have to be able to show, months later, three things: what the system said,
what it was grounded in, and who approved it. Until now the report lived only
in `jobs.result`, a mutable row that any later migration or re-run could
change. The evidence and decisions behind it lived only in LangGraph
checkpoints, which are an internal format that will need pruning (ADR 0003).

## Decision

When a job completes, the worker writes an immutable bundle to the `reports`
Blob container. Each reviewer decision is written when it is made.

```
reports/{yyyy}/{mm}/{job_id}/
    report.json            the validated Report (same schema as the API)
    report.md              standalone Markdown: dated, data gaps up front,
                           numbered citations, full source list
    provenance.json        plan, evidence references, critique, degraded sources,
                           deployment + tokens per LLM call, retrieval params,
                           timestamps
    approval-pass{n}.json  each approve/reject decision, its notes, the plan it
                           reviewed, and when it was made
```

- **Never overwrite.** Every blob is uploaded with `overwrite=False`. If a
  blob already exists, for example on a re-run after a crash, the original is
  kept and a warning is logged. One approval file per pass means a
  reject-then-approve history is fully preserved.
- **The path comes from the job's creation date,** so approvals written before
  completion and the final report share one prefix. The prefix is stored in
  `jobs.archive_prefix`.
- **Provenance holds references, not documents:** chunk ids with blob, page
  and period for filings; URLs for news. The sources themselves are the 10-Ks
  in `raw-filings` and the linked articles. This keeps the bundle small and
  avoids copying third-party text.
- **One renderer.** `report.md` comes from `app/reports/markdown.py`, which
  the API also uses to serve `GET /research/{id}/report.md`. The UI shows the
  archived file and offers it for download, so what people read is the
  archived artifact.
- **Untrusted text is disarmed in the Markdown.** Quoted source text is
  escaped, and model-written prose has images, inline links and raw HTML
  neutralised. A news snippet or an injected instruction can't turn the
  archived report into a phishing page or a tracking pixel.
- **Archiving is best-effort for the job.** If Blob Storage fails, the job
  still completes (the report is in `jobs.result`), the error is logged, and
  `archive_prefix` stays empty. The UI then says "not found in the archive;
  rendered from the job record". Losing a finished report to a transient
  storage error would be worse than a delayed archive.

## Why Blob Storage

Blob Storage is already in the stack, keyless, cheap, and durable. It also
supports the controls a real deployment would add without changing the
application code: immutability policies (time-based retention or legal hold,
i.e. WORM), versioning, and lifecycle tiering to cool or archive storage. A
database table would put regulated artifacts in an operational store that gets
migrated and pruned. A document store would be a new dependency.

## Consequences

- **The worker needs *Storage Blob Data Contributor*,** not Reader. The
  README's role table says so.
- **"Never overwrite" is enforced by the application,** not yet by the
  storage account. Phase 4 should add a container-level immutability policy,
  so that even an admin can't alter or delete a bundle within its retention
  period.
- **`reviewer` is `null` in approval records** until Day 15 adds Entra ID.
  The UI's role dropdown is a simulation and is deliberately not recorded as
  identity.
- **Re-rendering a report** (e.g. after a renderer change) creates nothing
  new: old bundles keep the Markdown they were issued with.
