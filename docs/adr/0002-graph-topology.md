# ADR 0002: Research graph topology

- **Status:** Accepted
- **Date:** 2026-09-25
- **Phase:** 2 (agents, MCP and resilience)

## Context

Phase 1 ran two nodes: retrieve, then write. Phase 2 needs a system that
plans before it searches, combines 10-K evidence with live news, lets a human
steer it before the expensive step, and checks its own output. It also has to
stay affordable. A two-pass run already costs about 65K tokens on the
chat model (mostly `compact` and `write`), and the deployment is on a
150K tokens-per-minute quota.

## Decision

```
START -> plan -> (retrieve_filings || fetch_news) -> compact -> approve_gate
approve_gate -(approved)-> write -> critique -(gaps, loop_count < 2)-> plan
approve_gate -(rejected)-> plan                  critique -(otherwise)-> END
```

| Node | Job | On failure |
|---|---|---|
| `plan` | Structured `ResearchPlan`: 3–5 sub-questions, companies, whether news is needed | fails the job |
| `retrieve_filings` | Vector search per sub-question (per company for comparisons), deduped by chunk id | degrades (`filings`) |
| `fetch_news` | MCP news tool, only when the plan asks for it | degrades (`news`) |
| `compact` | Condenses all evidence into a brief of about 2K tokens, keeping every reference id | fails the job |
| `approve_gate` | `interrupt()` with the plan and evidence counts; resumes with a human decision | waits |
| `write` | Report from the brief plus the evidence; cites by reference id only | fails the job |
| `critique` | LLM review plus deterministic citation checks | fails the job |

### Why this shape

- **Plan first, then fan out.** Sub-questions make retrieval targeted: one
  search per question, not one per query. The two evidence sources are
  independent, so they run in parallel and `compact` waits for both. Each
  evidence list has exactly one writer node. Only `degraded`, which either
  branch may write, needs a reducer (an order-preserving union).
- **Compact before writing.** The writer gets a small brief instead of 20–40
  raw chunks. That keeps prompts bounded and keeps the state small, since it
  is written to every checkpoint (ADR 0003). The brief is guaranteed to keep
  every reference id: any the model drops are appended by Python.
- **Evidence failures degrade; reasoning failures fail.** A report from 10-Ks
  alone is still useful, so a Search or news outage marks the source in
  `degraded` and the report's `data_gaps`, and the run continues. A plan,
  brief or report the model can't produce leaves nothing to deliver, so those
  failures fail the job.
- **Citations are grounded in Python, not trusted from the model.** The
  writer returns only `reference` + `quote`. Company, period, page and chunk
  are filled in from the evidence. A reference that wasn't retrieved is
  dropped. The critic's deterministic checks (uncited sections, unknown
  references) override the LLM's verdict.

### Why the approval gate sits before the writer

- **It is the cheapest point that still shows the reviewer something real.**
  By then the plan exists and the evidence has been gathered, so the reviewer
  sees the sub-questions, which companies were searched, how much evidence was
  found and which sources were unavailable. The expensive calls come after:
  `write` (about 4–12K prompt tokens) and `critique`. Rejecting there costs a
  re-plan, not a thrown-away report.
- **The reviewer approves scope, not prose.** A wrong plan is the failure a
  human spots fastest and a critic spots slowest. In the Phase 2 demo the
  planner asked for fiscal 2021–2024, while the index holds FY2025/26. A
  reviewer rejected it with notes, and the next plan was correct.
- **Rejection routes to `plan`, not `write`.** The reviewer's notes take
  precedence over the critic's gaps, and the planner revises the whole plan.
  Evidence already gathered is kept.

The gate asks on every pass, including critic-driven re-plans, because the
plan changes each time. `APPROVAL_REQUIRED=false` turns it off (auto-approve).

### Why the loop is capped at two passes

- **Cost is linear in passes and the critic is rarely fully satisfied.** In
  live runs the critic kept asking for data a 10-K doesn't contain, or numbers
  the writer isn't allowed to compute. An uncapped loop would keep spending
  about 30K tokens per pass on requests no search can satisfy.
- **The second pass gets most of the value.** In testing it added 7 new chunks
  (18 → 25) and fixed the flagged citation problems. Later passes would
  mostly re-retrieve the same chunks.
- **Rejections count toward the cap.** `plan` increments `loop_count` whatever
  the reason. After a rejection, the critic can't trigger a further re-plan, so
  the cost cap holds even with a human in the loop. A human can still reject
  again: that is paced by people, not by a runaway loop.
- **It is a code constant (`MAX_LOOPS = 2`), not configuration,** so it can't
  be raised by accident.

## Consequences

- **Worst case per job:** 2 plans, 2 compacts, 2 writes and 2 critiques, plus
  the embedding and Search calls. The LLM nodes have a wall-clock `timeout`
  (`NODE_TIMEOUT_S`). Retries and model fallback are in `app/resilience.py`
  and `app/graph/llm.py`.
- **A capped run can end "incomplete".** The final report is still returned,
  and the last critique is in the checkpoint, but the API doesn't expose it
  yet.
- **Two pauses per job when the critic loops,** which is acceptable for now.
- **Revisit** when Phase 3 evals show whether the second pass actually improves
  answer quality, or only citation coverage.
