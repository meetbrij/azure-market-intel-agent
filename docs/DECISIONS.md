# Decision log

The reasoning behind the project's significant decisions, from Phase 1 to
Day 13. Architecture-level choices have full ADRs in [`docs/adr/`](adr/);
their entries here are short and link to them. Accepted trade-offs and open
gaps are in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

**How to read it:** entries are grouped by theme and tagged with the day they
were made. The index below lists them in date order. Superseded decisions
stay in the log, marked as superseded, so the history of the thinking isn't
lost.

**Keeping it current:** adding entries for a day's decisions is part of that
day's definition of done.

## Index (chronological)

| ID | Day | Theme | Decision |
|---|---|---|---|
| D-01 | 2 | Scope | Ingest complete 10-Ks, not one-page exports |
| D-02 | 2 | Ingestion | Metadata from filename, falling back to aliases and the cover page |
| D-03 | 2 | Ingestion | Canonical company names and tickers |
| D-04 | 2 | Ingestion | Chunk per page; add a `page` field to the index |
| D-05 | 2 | Ingestion | Vectors not stored (`stored=False`) on the Free tier |
| D-06 | 2 | Ingestion | Embedding retries honour `Retry-After` |
| D-07 | 2 | Scope | Annual 10-Ks only; no 10-Qs, now or later |
| D-08 | 1–5 | Security | Keyless auth everywhere; deployment names from config |
| D-09 | 3 | Retrieval | Per-company top-k for multi-company questions |
| D-10 | 3 | Grounding | Never compute numbers |
| D-11 | 3 | Grounding | Additive-only changes to the report schema |
| D-12 | 4 | Platform | Separate arq worker, not `BackgroundTasks` |
| D-13 | 4 | Platform | Tables created on startup; idempotent `ALTER`s instead of Alembic |
| D-14 | 5 | Platform | `local` Docker target with the Azure CLI; slim `runtime` target |
| D-15 | 5 | Platform | Compose: worker waits for a healthy API; data ports published |
| D-16 | 6–7 | Graph | Spec topology, with the loop hard-capped at 2 |
| D-17 | 6–7 | Grounding | Cite by reference id; Python fills in metadata |
| D-18 | 6–7 | Grounding | Deterministic citation checks override the LLM critic |
| D-19 | 6–7 | Grounding | The brief keeps every reference id |
| D-20 | 6–7 | Retrieval | Planner companies limited to what's in the index |
| D-21 | 6–7 | Grounding | Limitations go in the summary, not an uncited section |
| D-22 | 8 | Security | Web content is untrusted: sanitise twice, fence in prompts |
| D-23 | 8 | Platform | One MCP session per worker, held in its own task |
| D-24 | 9–10 | Durability | Postgres checkpointer, thread id = job id, strict msgpack |
| D-25 | 9–10 | Durability | Crash recovery releases arq's stale lock; one worker assumed |
| D-26 | 9–10 | Durability | Cancellation leaves the job resumable, not failed |
| D-27 | 9–10 | Human loop | Approval on every pass; rejections count toward the cap |
| D-28 | 9–10 | Human loop | Resume is compare-and-set; enqueue failure keeps the job paused |
| D-29 | 11 | Resilience | One retry layer: 429, 5xx and timeouts only |
| D-30 | 11 | Resilience | Model fallback on repeated 429s or timeouts only |
| D-31 | 11 | Resilience | Evidence failures degrade; reasoning failures fail |
| D-32 | 11 | Resilience | LangGraph's built-in node timeout, not a custom wrapper |
| D-33 | 11 | Grounding | `data_gaps` set by Python, not the model |
| D-34 | 12 | UI | Separate UI image; HTTP only |
| D-35 | 12 | UI | Simulated roles are labelled as not being access control |
| D-36 | 12 | Platform | Job stages read from the checkpoint, not duplicated |
| D-37 | 12 | Archive | Immutable report bundle in Blob Storage; archiving best-effort |
| D-38 | 12 | Security | Disarm Markdown from untrusted sources |
| D-39 | 12 | Ingestion | Ingestion manifest blob for the operations view |
| D-40 | 13 | Evaluation | Golden set written from the filing text, not by the model |
| D-41 | 13 | Evaluation | RAGAS in an isolated environment |
| D-42 | 13 | Evaluation | Retrieval-only eval, with the question's company filter |
| D-43 | 13 | Evaluation | Warm-up, then run sequentially, for honest latency |
| D-44 | 13 | Evaluation | Gate thresholds from measured variance |

Bugs found in live testing, and what each one changed: [F-01 to F-15](#found-in-live-testing).

---

## Scope and source data

### D-01 · Ingest complete 10-Ks, not one-page exports · Day 2
- **Context:** Every PDF in `raw-filings` had one page of text. They had been
  printed from SEC's Inline XBRL viewer (`/ix?doc=`), which prints only the
  visible frame.
- **Decision:** Replace them with complete filings: the primary 10-K
  document only, not the exhibits (EX-21/23/31/32) or XBRL data files. You
  re-exported full PDFs, uploaded them, and deleted the one-page files.
- **Alternatives:**
  - A script to fetch the EDGAR `.htm` and parse the HTML (splitting pages on
    the page-break markers). It was planned but not needed.
  - Ingesting the one-page files as they were.
- **Why:** With about 15 chunks the RAG had almost nothing to retrieve. The
  full filings gave 1,256 chunks.
- **Status:** Active.

### D-07 · Annual 10-Ks only; no 10-Qs, now or later · Day 2
- **Context:** The spec assumed 10-Ks and 10-Qs. Adding three 10-Qs would have
  taken the index to about 47 MB of the Free tier's 50 MB.
- **Decision:** An annual-only corpus: Amazon FY2025, Alphabet FY2025,
  Microsoft FY2026. Quarterly references were removed from the code and the
  spec. Temporal questions mean year over year.
- **Alternatives:** Add 10-Qs; use 512-dimension embeddings to save space;
  upgrade the Search tier.
- **Why:** Finish the portfolio project without growing scope or cost. The
  annual filings are enough for every done-when test.
- **Status:** Active.
- **See:** `docs/PHASE1_SPEC.md`, the "Out of scope" section.

---

## Ingestion and indexing

### D-02 · Metadata from filename, falling back to aliases and the cover page · Day 2
- **Context:** The uploaded names (`amzn_annual_report_10k.pdf`) didn't
  follow the `{COMPANY} {DOC_TYPE} {PERIOD}.pdf` convention.
- **Decision:**
  - Parse the convention first.
  - Otherwise take the issuer from ticker or name aliases in the filename.
  - Take the form type and fiscal-year end from the cover page.
  - Anything still missing becomes `unknown`, with a warning.
- **Alternatives:** Rename the blobs by hand; accept `unknown` metadata.
- **Why:** Without company and period, filtering and citations fail. Parsing
  the filing itself is robust to however files are named.
- **Status:** Active (`ingestion/parse.py`).

### D-03 · Canonical company names and tickers · Day 2
- **Decision:** Legal names map to `Amazon/AMZN`, `Alphabet/GOOGL` and
  `Microsoft/MSFT`. Filters, the planner and the UI all use these names.
- **Why:** Filtering on "AMAZON.COM, INC." would never match what a user types.
- **Status:** Active. The alias list is in code; see the limitations.

### D-04 · Chunk per page; add a `page` field to the index · Day 2
- **Decision:** Split each page separately (about 1,000 characters with 150
  overlap), so every chunk carries an exact page number. Add a `page` field to
  the index; the spec's schema didn't have one.
- **Why:** Citations have to point to a page someone can check.
- **Trade-off:** Chunks never cross a page boundary, so a sentence split
  across two pages is split across two chunks.
- **Status:** Active. Phase 3 compares this with section-aware chunking
  (deferred).

### D-05 · Vectors not stored (`stored=False`) on the Free tier · Day 2
- **Decision:** The vector field is searchable but not stored or retrievable.
- **Why:** It roughly halves vector storage against the 50 MB cap, and the app
  never reads vectors back.
- **Status:** Active.

### D-06 · Embedding retries honour `Retry-After` · Day 2
- **Context:** The first full ingest crashed after 8 retries on 429s. The
  embedding deployment was at 50K TPM.
- **Decision:** Wait as long as the `Retry-After` header says, and stop after
  15 minutes rather than a fixed attempt count. You later raised the TPM
  limits (chat 150K, embeddings 300K).
- **Status:** Active in `ingestion/`. The app itself uses D-29.

### D-39 · Ingestion manifest blob for the operations view · Day 12
- **Context:** The Admin view needs "last ingestion time", and the index
  doesn't record one.
- **Decision:** The ingest CLI writes `_manifest/last-ingestion.json` (time,
  documents, chunks, settings) to the filings container, overwritten each run.
- **Alternatives:**
  - An `ingested_at` field on every document, which needs a reindex.
  - A Postgres table, which would couple ingestion to the app's database.
- **Status:** Active.

---

## Retrieval

### D-09 · Per-company top-k for multi-company questions · Day 3
- **Context:** "Compare Amazon and Alphabet cloud revenue" got all 8 hits
  from Alphabet. Its chunks say "Google Cloud" literally; Amazon's say "AWS".
- **Decision:** With more than one company requested, run top-k per company
  and merge.
- **Why:** A comparison that silently loses one side is worse than a slightly
  bigger context.
- **Status:** Active (`app/graph/retrieval.py`).

### D-20 · Planner companies limited to what's in the index · Day 6–7
- **Decision:**
  - The planner sees the list of companies actually in the index (a facet
    query).
  - Companies the user selects override the planner's choice.
  - Names are mapped to their canonical form, and unknown names are dropped.
- **Why:** An invented or differently spelled company name makes the search
  filter match nothing.
- **Status:** Active.

Retrieval quality is measured, not assumed: see D-40 to D-44, and Day 14 for
hybrid and semantic ranking.

---

## Agent graph

### D-16 · Spec topology, with the loop hard-capped at 2 · Day 6–7
- **Decision:** plan → (filings ‖ news) → compact → approve_gate → write →
  critique → (plan | END). `MAX_LOOPS = 2` is a code constant, not
  configuration.
- **Why:**
  - Each pass costs about 30K tokens.
  - The critic is rarely fully satisfied.
  - The second pass captured most of the gain (18 → 25 chunks).
- **See:** [ADR 0002](adr/0002-graph-topology.md).
- **Status:** Active.

---

## Grounding and report integrity

### D-10 · Never compute numbers · Day 3 (reinforced Days 8 and 12)
- **Decision:** The writer quotes figures the sources state. It never works
  out growth rates, margins or differences. You chose to keep this after
  seeing it leave out Google Cloud's growth percentage.
- **Follow-ups:**
  - **Day 8:** the critic was told not to flag computations that are missing
    (at your request).
  - **Day 12:** the planner was told never to ask for calculations, after a
    report included "~35.4% margin" because the plan asked for margins
    (F-12).
- **Why:** Numbers the filings don't state can't be verified against a
  citation. In a regulated setting that's a correctness risk, not a style
  choice.
- **Status:** Active.

### D-11 · Additive-only changes to the report schema · Day 3 onwards
- **Decision:** Existing Phase 1 fields are never removed or renamed. New
  ones are only added (`page`, `source_type`, `reference`, `data_gaps`), and
  filing-only fields became optional so news citations fit.
- **Why:** It keeps the Phase 1 API contract (a hard constraint in Phase 2).
- **Status:** Active.

### D-17 · Cite by reference id; Python fills in metadata · Day 6–7
- **Decision:**
  - The model returns only `{reference, quote}`.
  - Python fills in company, period, page and chunk from the evidence.
  - Unknown references are dropped.
  - `[brackets]` and whitespace are stripped before matching (F-04).
- **Why:** The model can't mis-copy metadata or cite something that wasn't
  retrieved. Its output is also shorter.
- **Status:** Active.

### D-18 · Deterministic citation checks override the LLM critic · Day 6–7
- **Decision:** A section with no citations, or a citation to an unknown
  reference, makes the report incomplete, whatever the LLM critic says.
- **Why:** Don't rely on the model alone to judge the model.
- **Status:** Active.

### D-19 · The brief keeps every reference id · Day 6–7
- **Decision:** Any evidence reference the compact step leaves out is added
  back to the end of the brief.
- **Why:** The writer can then still cite any retrieved evidence. This is a
  safety net; see the limitations for how often it's needed.
- **Status:** Active.

### D-21 · Limitations go in the summary, not an uncited section · Day 6–7
- **Context:** The writer created a "no news available" section and propped
  it up with an unrelated 10-K citation, to satisfy "every section has a
  citation" (F-05).
- **Decision:**
  - Limitations are stated in the summary.
  - The critic is told which sources were unavailable, so it doesn't send the
    report back to chase them.
- **Status:** Active. The writer still does this occasionally; see the
  limitations.

### D-33 · `data_gaps` set by Python, not the model · Day 11
- **Decision:** `Report.data_gaps` comes from `state.degraded` (e.g. "live
  news unavailable"), not from what the LLM chooses to write.
- **Why:** A report that quietly leaves out what it couldn't find is the
  dangerous failure. It has to be reliable, not left to the model.
- **Status:** Active.

---

## Human in the loop and durability

### D-12 · Separate arq worker, not `BackgroundTasks` · Day 4
- **See:** [ADR 0001](adr/0001-async-job-execution.md). Jobs survive API
  restarts, and this is the basis for resumable runs.
- **Status:** Active.

### D-24 · Postgres checkpointer, thread id = job id, strict msgpack · Day 9–10
- **Decision:**
  - `AsyncPostgresSaver` in the jobs database, under its own `langgraph`
    schema.
  - `thread_id` is the job's UUID.
  - `LANGGRAPH_STRICT_MSGPACK=true` is the default, set in `app/__init__.py`
    before LangGraph is imported.
- **See:** [ADR 0003](adr/0003-checkpointing.md).
- **Status:** Active.

### D-25 · Crash recovery releases arq's stale lock; one worker assumed · Day 9–10
- **Decision:** On startup, re-enqueue jobs still marked `running`, after
  deleting arq's in-progress lock. A killed worker would otherwise leave that
  lock held for 610 seconds.
- **Trade-off:** This is only safe with one worker. You accepted that for
  now.
- **Status:** Active; see the limitations.

### D-26 · Cancellation leaves the job resumable, not failed · Day 9–10
- **Decision:** `CancelledError` and LangGraph's `NodeCancelledError` leave the
  row `running`, so recovery resumes it from its checkpoint (F-08).
- **Status:** Active.

### D-27 · Approval on every pass; rejections count toward the cap · Day 9–10
- **Decision:**
  - The approval gate pauses on each pass, because the plan changes each time.
  - A rejection sends the job back to `plan` with the reviewer's notes, which
    take precedence over the critic's gaps.
  - `plan` increments the loop count whatever the reason, so the hard cost
    cap holds.
  - `APPROVAL_REQUIRED=false` auto-approves.
- **Why:** The gate sits at the cheapest point where there's real evidence to
  judge (ADR 0002). The cost cap applies even with a human in the loop.
- **Status:** Active. You accepted approval on every pass "for now".

### D-28 · Resume is compare-and-set; enqueue failure keeps the job paused · Day 9–10
- **Decision:**
  - `awaiting_approval → queued` is a conditional update, so a second
    concurrent resume gets 409.
  - Each resume uses a fresh arq job id.
  - If enqueueing fails, the job goes back to `awaiting_approval`.
- **Status:** Active.

---

## Resilience

### D-29 · One retry layer: 429, 5xx and timeouts only · Day 11
- **Decision:**
  - The OpenAI and Azure Search SDKs' own retries are off.
  - tenacity with exponential backoff is the only retry layer, set by
    `RETRY_ATTEMPTS`.
  - Other 4xx errors are never retried.
  - Tavily has its own bounded retries inside the MCP server, sized to fit
    the client's 30-second call budget.
- **Why:** Stacked retry layers multiply attempts (e.g. 3 × 4) and hide what
  is really happening.
- **Status:** Active.

### D-30 · Model fallback on repeated 429s or timeouts only · Day 11
- **Decision:** After retries, a call moves to
  `AZURE_OPENAI_CHAT_FALLBACK_DEPLOYMENT`, which is unset by default. A 400 is
  never sent to the fallback, since it would fail on any model. Each call logs
  which deployment served it.
- **Status:** Active; verified by tests only, since there's no second
  deployment.

### D-31 · Evidence failures degrade; reasoning failures fail · Day 11
- **Decision:** If Search or news is unavailable, the source is marked in
  `degraded` and the run continues. If `plan`, `compact`, `write` or
  `critique` fails, the job fails.
- **Why:** A report from the 10-Ks alone is still useful. A missing plan or
  report leaves nothing to deliver.
- **Status:** Active.

### D-32 · LangGraph's built-in node timeout, not a custom wrapper · Day 11
- **Decision:** `add_node(..., timeout=NODE_TIMEOUT_S)` on the LLM nodes. The
  evidence nodes bound their own calls and degrade on timeout.
- **Superseded:** a custom `with_timeout` wrapper written earlier the same
  day, dropped once the built-in parameter turned up. Less custom code, in
  line with your "don't over-engineer" guidance.
- **Status:** Active.

---

## Security and untrusted content

### D-08 · Keyless auth everywhere; deployment names from config · Days 1–5
- **Decision:**
  - Every Azure client uses `DefaultAzureCredential`.
  - The only third-party secret (Tavily) is read from Key Vault by the MCP
    server alone.
  - Model and deployment names come from environment variables.
- **Status:** Active. Managed identity comes in Phase 3 / Day 18.

### D-22 · Web content is untrusted: sanitise twice, fence in prompts · Day 8
- **Decision:**
  - The MCP server strips HTML, caps length, drops non-http(s) URLs and skips
    social platforms.
  - The graph's client sanitises again.
  - Prompts fence news text in `<untrusted_web_content>`.
  - The stdio server receives only the environment variables Key Vault needs.
- **Status:** Active. Day 15 adds a classifier screen.

### D-38 · Disarm Markdown from untrusted sources · Day 12
- **Context:** A news snippet starting with `#` rendered as a heading in the
  UI (F-11).
- **Decision:**
  - Quoted source text is escaped.
  - The model's prose keeps its formatting, but images, inline links and raw
    HTML are neutralised.
  - URLs are percent-encoded.
  - The UI shows snippets as plain text.
- **Why:** Rendered web text could otherwise inject links or tracking images
  into an archived, regulated report.
- **Status:** Active.

### D-35 · Simulated roles are labelled as not being access control · Day 12
- **Decision:**
  - The role dropdown is labelled "Simulated role (dev only)".
  - The README says it has no security value.
  - Approval records store `reviewer: null` rather than trusting the dropdown.
- **Status:** Active until Day 15 (Entra ID).

---

## Platform, containers and UI

### D-13 · Tables created on startup; idempotent `ALTER`s instead of Alembic · Day 4 onwards
- **Decision:** `create_all` at API startup, plus a short list of
  `ALTER … IF NOT EXISTS` statements for columns added later.
- **Why:** Enough for a single-schema demo. Alembic comes later.
- **Status:** Active; see the limitations.

### D-14 · `local` Docker target with the Azure CLI; slim `runtime` target · Day 5
- **Context:** Mounting `~/.azure` read-only isn't enough:
  `AzureCliCredential` calls the `az` binary, and `az` rewrites its token
  cache.
- **Decision:**
  - The `local` image target adds the Azure CLI.
  - At startup the container copies the mount to a writable directory.
  - `AZURE_TOKEN_CREDENTIALS=AzureCliCredential` is pinned.
  - The slim `runtime` target (355 MB) is what gets deployed.
- **Status:** Active for local development.

### D-15 · Compose: worker waits for a healthy API; data ports published · Day 5
- **Decision:**
  - The worker depends on `api: service_healthy`, because the API creates the
    tables.
  - Postgres and Redis publish ports, so `uv run` workflows on the host work
    against the same containers.
- **Status:** Active.

### D-23 · One MCP session per worker, held in its own task · Day 8
- **Decision:** The session opens at worker startup and stays open. It's owned
  by a dedicated asyncio task, because MCP's stdio transport must be opened
  and closed by the same task.
- **Why:** The spec says not to handshake per request, and arq's startup and
  shutdown hooks may run in different tasks.
- **Status:** Active.

### D-36 · Job stages read from the checkpoint, not duplicated · Day 12
- **Decision:** `GET /research/{id}` reads plan, evidence and critique from
  the job's checkpoint. The API opens the checkpointer read-only
  (`setup=False`), so it never races the worker on migrations. If the
  checkpoint can't be read, those fields are empty rather than failing the
  request.
- **Why:** One source of truth, and the jobs table stays small.
- **Status:** Active.

### D-34 · Separate UI image; HTTP only · Day 12
- **Decision:** Streamlit runs in its own image with only the `ui` dependency
  group, so it can't import the graph or reach Postgres. All requests go
  through one `_headers()` function, ready for Day 15's bearer token.
- **Status:** Active.

### D-37 · Immutable report bundle in Blob Storage; archiving best-effort · Day 12
- **Decision:**
  - `report.json`, `report.md`, `provenance.json` and
    `approval-pass{n}.json` go under `reports/{yyyy}/{mm}/{job_id}/`,
    uploaded with `overwrite=False`.
  - If Blob Storage fails, the job still completes.
- **See:** [ADR 0007](adr/0007-report-archival.md).
- **Status:** Active.

---

## Evaluation

### D-40 · Golden set written from the filing text, not by the model · Day 13
- **Decision:**
  - The 30 questions were written from text extracted from the PDFs, each
    answer quoted from a named page.
  - Each unanswerable question was confirmed absent by searching all three
    filings.
  - "Temporal" means year over year (D-07).
- **Why:** Generating questions with the model under test is circular. The
  unanswerable set is what shows whether the system invents numbers.
- **Status:** Active. Worth a human review pass.

### D-41 · RAGAS in an isolated environment · Day 13
- **Context:**
  - RAGAS 0.4.3 depends on `instructor`, which requires an older openai than
    the app's 3.19.
  - RAGAS 0.4.3 also imports a module that `langchain-community` 0.4.2
    removed.
  - RAGAS detects reasoning models by name, and the deployment is called
    `chat-mini`.
- **Decision:**
  - `evals/ragas/` is a separate uv project with its own lockfile, pinned to
    `langchain-community==0.4.1`, run as a subprocess.
  - Its Azure client is fixed to the deployment URL, and RAGAS is told the
    model is `gpt-5-mini`.
- **Alternatives:**
  - Write our own RAGAS-style metrics (a spec deviation).
  - Downgrade the app's SDK for an evaluation library.
- **Status:** Active (you chose this option).

### D-42 · Retrieval-only eval, with the question's company filter · Day 13
- **Decision:**
  - One search per question, top-8.
  - Each question carries the company filter a user would pick in the UI.
  - One grounded answer; RAGAS scores the answerable questions.
  - Python checks citation validity and abstention.
- **Why:** The spec measures the retriever, not the whole graph.
- **Trade-off:** Real questions get their companies from the planner; see the
  limitations.
- **Status:** Active.

### D-43 · Warm-up, then run sequentially, for honest latency · Day 13
- **Context:** The first run showed about 3.76 s retrieval for every
  question. That was concurrent callers waiting for an Entra token, not search
  time.
- **Decision:** One warm-up call first, then one question at a time by
  default (`--concurrency 1`).
- **Status:** Active.

### D-44 · Gate thresholds from measured variance · Day 13
- **Decision:** faithfulness ≥ 0.90; citation validity ≥ **0.80**, not
  "10% below".
- **Why:** The smoke set produces only about 7 citations. In 4 runs citation
  validity measured 1.0 three times and 0.857 once (a single mangled id). A
  0.90 gate would trip on normal variance, and the spec warns that such
  gates get switched off.
- **Status:** Active (`evals/thresholds.yaml`).

---

## Found in live testing

Bugs that only appeared in real runs, never in unit tests, and the fix each
one led to.

| ID | Day | What happened | Fix / decision |
|---|---|---|---|
| F-01 | 2 | Every source PDF had one page (Inline XBRL viewer print) | D-01 |
| F-02 | 2 | Ingest crashed after 8 retries on embedding 429s | D-06 |
| F-03 | 3 | A two-company comparison got only one company's chunks | D-09 |
| F-04 | 6–7 | The model copied `[brackets]` around reference ids; all 34 citations were dropped | D-17: normalise references |
| F-05 | 6–7 | A "no news" section cited an unrelated 10-K chunk | D-21 |
| F-06 | 8 | The MCP stdio server started without its settings (the client passes only safe env vars) | Pass the Azure/Key Vault variables explicitly (D-22) |
| F-07 | 8 | "Company news" results were all social-media posts | Exclude social domains (D-22) |
| F-08 | 9–10 | A cancelled node raised `NodeCancelledError`, marking the job failed | D-26 |
| F-09 | 9–10 | Tests ran without strict msgpack (LangGraph imported first) | Set the flag before any import |
| F-10 | 12 | The test suite reached real Blob Storage (172 s instead of 1 s) | Automatic in-memory blob fake |
| F-11 | 12 | A news snippet rendered as a Markdown heading in the UI | D-38 |
| F-12 | 12 | The planner asked for margins, so the report computed "~35.4%" | D-10: planner and writer prompts |
| F-13 | 12 | On a second pass the UI showed every step as done | Reset the progress list when a new pass starts |
| F-14 | 13 | RAGAS conflicts and name-based reasoning-model detection | D-41 |
| F-15 | 13 | Token waits inflated retrieval latency; the page-hit metric overstated hits | D-43; metric relabelled |

---

## Documented elsewhere

- **Spec deviations:** recorded inline in `docs/PHASE1_SPEC.md` and the other
  specs where they occurred.
- **Deferred work:** the Phase 3 roadmap (chunking comparison, MLflow,
  Airflow, long-term memory) will be in `docs/ROADMAP.md`, a Phase 3
  deliverable.
