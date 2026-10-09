# Day 12 Spec — Streamlit Client

Save as `docs/PHASE2_UI_SPEC.md`. Runs after Phase 2 (`phase-2-agents`),
before Phase 3. Budget: one day.

## Purpose

A thin client over the existing API so the approval gate and crash-resume
behaviour are demonstrable, and so completed reports are readable. It is a demo
harness, not a product.

**Non-goal:** this is not a frontend engineering exercise. No custom CSS beyond
Streamlit defaults, no charting library, no session persistence beyond
`st.session_state`.

---

## Role model — read this first

`Planner`, `Writer` and `Critic` are **agent nodes inside the graph**, executed
by the LLM. They are not human roles and must not appear as login personas.

Two distinct concepts in the UI:

### Human roles (change what a person can do)

| Role | Can do | Real-world persona |
|---|---|---|
| **Analyst** | Submit research, view jobs and reports | Research associate answering a question that would otherwise take half a day of reading filings |
| **Reviewer** | Everything Analyst can, plus approve/reject at the gate | Senior analyst or compliance officer accountable for what goes out |
| **Admin** | Everything, plus the operations view | Platform owner — index health, ingestion freshness, cost |

In Day 12 the role is a **sidebar dropdown, clearly labelled "Simulated role
(dev only)"**. It is a UI-side simulation with no security value. Day 15
replaces it with the Entra ID token's app role and the dropdown disappears. Say
this in the README — claiming the dropdown is access control would be false.

### Agent stages (change what a person can see about one job)

Plan, Filing evidence, News evidence, Draft, Critique. These are **tabs inside
the job detail view**, available to every role. This is how a user inspects what
each agent produced, which is the transparency story that matters for a
regulated use case.

---

## API additions needed first

- `GET /api/v1/research` — list jobs, newest first, with
  `job_id, status, query, subject, created_at, updated_at`. Support
  `?status=` and `?limit=`.
- `GET /api/v1/research/{id}` — extend the response to expose `plan`,
  `filing_evidence`, `news_evidence`, `critique`, `degraded`, `loop_count`,
  `last_node` alongside the existing report, so the stage tabs have data.
- `GET /api/v1/ops/status` (Admin view) — document count in the index, index
  name, most recent ingestion timestamp, job counts by status. Keep it cheap.

---

## Screens

### 1. Submit (Analyst, Reviewer, Admin)

- Query text area, with two or three example queries as clickable presets.
- Company multi-select, sourced from the distinct companies in the index
  (fetch once, cache in session state).
- Submit button → `POST /api/v1/research` → store the returned `job_id` in
  session state and navigate to the job view.

### 2. Jobs list (all roles)

Table of recent jobs: status badge, subject or truncated query, created time,
and a View button. Filter by status. For a Reviewer, jobs in
`awaiting_approval` sort to the top and are visually flagged — that is their
queue.

### 3. Job detail (all roles)

Header: status, elapsed time, current node, degraded-source warnings, and a
loop counter if the critique loop ran.

While `running`: poll `GET /api/v1/research/{id}` every 2 seconds using
`st.fragment` with `run_every`, or an explicit Refresh button as fallback. Show
a simple node progress list (plan → retrieve/news → compact → approval → write
→ critique) with completed nodes ticked. Stop polling on a terminal status.

Tabs:

- **Report** — rendered Markdown: subject, summary, sections. Every citation
  rendered inline as a footnote-style marker, with the full reference list
  beneath (company, doc type, period, blob, chunk) and news items as links. Show
  `data_gaps` prominently if present — a report that quietly omits what it could
  not find is the dangerous failure mode.
- **Plan** — sub-questions, companies, whether live news was requested.
- **Evidence** — filing evidence and news evidence in two expandable lists, each
  showing the snippet and its reference.
- **Critique** — completeness verdict, missing items, citation problems.
- **Raw** — the full job JSON in an expander, for debugging.

### 4. Approval (Reviewer and Admin only)

Shown when status is `awaiting_approval`. Renders the interrupt payload: the
plan, evidence counts, and sub-questions. Then:

- **Approve** → `POST /research/{id}/resume` with `{"approved": true}`
- **Reject** → requires notes, posts `{"approved": false, "notes": "..."}`,
  which routes back to the planner

An Analyst viewing a job at this stage sees "Awaiting reviewer approval" and no
buttons.

### 5. Operations (Admin only)

Index name and document count, last ingestion time, job counts by status,
and the list of any jobs currently stuck in `running`. Read-only. No
triggering of ingestion from the UI.

---

## Report archival (add in this phase)

On job completion, the worker writes an immutable bundle to a new blob container
`reports/`, at `reports/{yyyy}/{mm}/{job_id}/`:

- `report.json` — the validated `Report`
- `report.md` — rendered Markdown, standalone readable, dated, with sources
- `provenance.json` — plan, evidence references, critique, degraded sources,
  model deployment names per node, retrieval params, token counts, timestamps
- `approval.json` — decision, notes, timestamp (written at resume time)

Never overwrite. The job row stores the blob prefix.

Add a Download report (Markdown) button in the Report tab that fetches the
archived file.

**Why:** the report is the artifact the business consumes, and in a regulated
setting you must be able to show, months later, what the system said, what it
was grounded in, and who approved it. Record this in
`docs/adr/0007-report-archival.md`.

---

## Implementation notes

- `ui/app.py` plus `ui/api_client.py` (one thin HTTP wrapper — all calls go
  through it). The UI **must not** import the graph, the job models, or touch
  Postgres directly. It is an API client only.
- API base URL from env (`API_BASE_URL`), defaulting to `http://api:8000`.
- Add a `ui` service to `docker-compose.yml`, port 8501, depending on `api`.
- Handle API errors visibly: show status code and message rather than an empty
  page. A silent failure during a live demo is worse than an ugly error.
- Day 15 will add MSAL device-code login here; keep the API client's header
  construction in one function so adding a bearer token is a one-line change.

---

## Record the demo clips today

The app is at its simplest now, and by the deployment phase an expired token or
a broken role assignment can cost you a day. Capture:

1. **Happy path** — submit → progress → approval → approved → cited report
   (~30s GIF)
2. **Crash resume** — submit, `docker compose kill worker` mid-run, restart,
   job completes from checkpoint, with the UI showing progress throughout
3. **Rejection loop** — reject with notes, planner re-runs, report improves

Store them in `docs/media/` and embed in the README.

## Definition of done

`docker compose up` brings up api, worker, postgres, redis and ui. A full job
runs from the browser including approval, the report renders with working
citations, the archive bundle appears in blob storage, and the three clips are
recorded. Tag `phase-2-ui`.
