# Decision log

The reasoning behind the project's significant decisions, from Phase 1 to
Day 18, including the fixes from the code review before Day 15 ("Day 14.5"). Architecture-level choices have full ADRs in [`docs/adr/`](adr/);
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
| D-44 | 13 | Evaluation | Gate thresholds from measured variance (re-measured Day 14) |
| D-45 | 14 | Ingestion | `title` field and semantic configuration; rebuild the index |
| D-46 | 14 | Retrieval | Retrieval mode is configuration; hybrid uses a 50-candidate vector pool |
| D-47 | 14 | Retrieval | Ship hybrid + semantic ranker as the default |
| D-48 | 14 | Resilience | Semantic ranker unavailable → fall back to hybrid, recorded per hit |
| D-49 | 14 | Evaluation | The smoke gate follows the production retrieval mode |
| D-50 | 14.5 | Human loop | A resume decision is bound to the pass it was made for |
| D-51 | 14.5 | Human loop | Rejections are capped too: rejecting the last pass ends the run |
| D-52 | 14.5 | Human loop | A rejection discards the rejected plan's evidence |
| D-53 | 14.5 | Grounding | A citation's quote must appear in its source |
| D-54 | 14.5 | Security | Decode before stripping; everything from the web goes inside the fence |
| D-55 | 14.5 | Resilience | `plan` survives a Search outage |
| D-56 | 14.5 | Durability | arq `job_timeout` raised to 30 minutes |
| D-57 | 14.5 | Evaluation | The gate fails on a missing score and on an answered unanswerable |
| D-58 | 14.5 | Evaluation | Page hit is per source; stored results recomputed |
| D-59 | 14.5 | Platform | Data ports on localhost only; bounded `companies` filter |
| D-60 | 15 | Security | The API validates Entra ID v2 access tokens itself |
| D-61 | 15 | Security | Two app roles; analysts see only their own jobs |
| D-62 | 15 | Human loop | Separation of duties: nobody approves their own job |
| D-63 | 15 | Security | Dev bypass is local-only, enforced at startup |
| D-64 | 15 | Security | App registrations by script: no secrets, assignment required, auth code + PKCE UI |
| D-65 | 15 | Governance | Append-only `audit_events`; a person's action needs its audit record |
| D-66 | 15 | Security | News is screened for injection before any prompt; fails closed |
| D-67 | 15 | UI | The UI shows identity and roles from the API and hides what it would refuse |
| D-68 | 16 | Observability | One Langfuse trace per job; trace id = job id; keys in Key Vault; optional |
| D-69 | 16 | Observability | Callback handler for nodes and tools; explicit spans for model calls and searches |
| D-70 | 16 | Observability | Langfuse prices calls from model names; we never compute cost for tracing |
| D-71 | 16 | Observability | Prometheus `/metrics` for request latency and jobs by status |
| D-72 | 16 | Evaluation | Eval runs can be traced; `evals.reconcile` checks cost against Langfuse |
| D-73 | 17 | Platform | Images: pinned digests, no package manager, per-component HEALTHCHECK; bytecode kept |
| D-74 | 17 | Platform | One Helm chart, verified on kind; plain manifests for demo Postgres/Redis; no AKS |
| D-75 | 17 | Platform | News server as its own service; the worker reconnects |
| D-76 | 17 | Platform | Probes separate liveness from readiness; one worker, Recreate |
| D-77 | 17 | Security | Least-privilege pods and NetworkPolicies; generated Postgres password |
| D-78 | 18 | Platform | Azure Container Apps (api, worker, news, ui, redis), Bicep in two passes |
| D-79 | 18 | Security | Keyless Postgres: Entra-only auth, a token per connection, no password anywhere |
| D-80 | 18 | Security | User-assigned identities per app plus a shared database identity |
| D-81 | 18 | Platform | Images built locally or on the pipeline agent (ACR Tasks blocked) |
| D-82 | 18 | Platform | Azure Pipelines: test, eval gate, build, deploy; workload identity federation |
| D-83 | 18 | Operations | $50 monthly budget with actual and forecast alerts |

Bugs found in live testing, and what each one changed: [F-01 to F-22](#found-in-live-testing).

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

### D-45 · `title` field and semantic configuration; rebuild the index · Day 14
- **Context:** A semantic configuration names a title field and content
  fields, and the index had no title.
- **Decision:**
  - Add a searchable `title` built at ingest time ("Amazon 10-K 2025-12-31
    p.27").
  - Add the semantic configuration `filings-semantic` (title plus
    `content`); keep the vector profile.
  - Rebuild with `--recreate`, since search-configuration changes require it.
    Chunk ids are deterministic, so existing citations and archives still
    resolve.
- **Why:** The ranker and BM25 then see which company, form, period and page
  a chunk comes from; the chunk text alone often doesn't say.
- **Status:** Active. The configuration name comes from one setting
  (`SEMANTIC_CONFIGURATION`), shared by ingestion and queries.

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

### D-46 · Retrieval mode is configuration; hybrid uses a 50-candidate vector pool · Day 14
- **Decision:**
  - `RETRIEVAL_MODE` is `vector`, `hybrid` (BM25 + vector in one call, fused
    by Azure with RRF) or `hybrid_semantic` (hybrid, then the semantic
    ranker).
  - Hybrid modes ask the vector side for 50 candidates (not k), so fusion and
    reranking have enough to choose from; `top` stays at k.
  - Each hit records the mode that served it.
- **Why:** Evals can switch variants without code changes, as the spec
  requires. With only k candidates, RRF would have little to fuse and the
  ranker little to reorder.
- **Status:** Active.

### D-47 · Ship hybrid + semantic ranker as the default · Day 14
- **Context:** The benchmark ran all three variants on the same day and the
  same index.
- **Decision:** `RETRIEVAL_MODE` defaults to `hybrid_semantic`.
- **Evidence:**
  - Context precision 0.54 → 0.81 and recall 0.85 → 1.00.
  - Wrongly declined questions 16% → 0%; the expected page retrieved 80% →
    100%.
  - Abstention and citation validity unchanged at 100%.
  - About +55 ms median retrieval latency, and no change in token cost.
- **Alternatives:**
  - Hybrid without the ranker: good recall (0.95) but little precision gain
    (0.59).
  - Vector: the baseline.
- **Trade-off:** The Free plan's 1,000 semantic queries a month is roughly
  50–200 reports (see the limitations).
- **See:** [retrieval-benchmark.md](retrieval-benchmark.md).
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
- **Status:** Active. You accepted approval on every pass "for now". The
  code review found that the rejection route didn't actually check the cap;
  D-51 enforces it.

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
  deployment. In practice only the 429 path can fire: see the limitations
  (timeout fallback).

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

### D-48 · Semantic ranker unavailable → fall back to hybrid, recorded per hit · Day 14
- **Context:** When the semantic quota runs out, Azure returns an error rather
  than quietly giving unranked results.
- **Decision:**
  - A search error that mentions the semantic ranker is retried as plain
    hybrid, with a warning logged.
  - Any other search error is raised as usual, not hidden.
  - The mode that actually served the query is stored on each hit, and eval
    runs report `semantic_fallbacks`.
- **Why:** Running out of quota should degrade to the second-best variant
  (hybrid: recall 0.95), not fail the job. Recording fallbacks keeps a quota
  problem from being mistaken for a quality change.
- **Status:** Active.

---

## Security and untrusted content

### D-08 · Keyless auth everywhere; deployment names from config · Days 1–5
- **Decision:**
  - Every Azure client uses `DefaultAzureCredential`.
  - The only third-party secret (Tavily) is read from Key Vault by the MCP
    server alone.
  - Model and deployment names come from environment variables.
- **Status:** Active. Managed identity comes in Phase 3 / Day 18. Day 15
  checked for remaining local key handling and found none. The Entra app
  registrations it adds have no client secrets (D-64).

### D-22 · Web content is untrusted: sanitise twice, fence in prompts · Day 8
- **Decision:**
  - The MCP server strips HTML, caps length, drops non-http(s) URLs and skips
    social platforms.
  - The graph's client sanitises again.
  - Prompts fence news text in `<untrusted_web_content>`.
  - The stdio server receives only the environment variables Key Vault needs.
- **Status:** Active. D-54 closed an encoded-entity bypass of the fence. Day
  15 added a classifier screen (D-66), and ADR 0004 describes all the
  layers.

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
- **Status:** Superseded on Day 15 by D-61, D-62 and D-67. The dropdown is
  gone, and roles come from the token.

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
- **Status:** Active. Since D-59 the ports bind to `127.0.0.1` only.

### D-23 · One MCP session per worker, held in its own task · Day 8
- **Decision:** The session opens at worker startup and stays open. It's owned
  by a dedicated asyncio task, because MCP's stdio transport must be opened
  and closed by the same task.
- **Why:** The spec says not to handshake per request, and arq's startup and
  shutdown hooks may run in different tasks.
- **Status:** Active. Since Day 17 (D-75) the session is also re-opened with
  backoff whenever it drops, and in Kubernetes it runs over HTTP.

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
- **Status:** Active (`evals/thresholds.yaml`). Re-measured on Day 14 for
  hybrid_semantic: 1.0 on both metrics in 4 of 4 runs. The values stay at
  0.90 and 0.80, because the smoke set is still only about 8 citations.

### D-49 · The smoke gate follows the production retrieval mode · Day 14
- **Decision:** `evals.run` defaults `--variant` to `RETRIEVAL_MODE`, so
  `--smoke` (the CI gate) tests what production actually runs. Previously it
  was hard-coded to `vector`.
- **Why:** A gate that tests a mode production doesn't use gives false
  confidence.
- **Status:** Active.

---

## Review fixes (Day 14.5)

A read-only review of the whole codebase before Day 15 (three reviewers:
graph and news; API, jobs, UI and Docker; ingestion and evals). Nothing was
critical. These entries are the fixes; the findings we accepted instead are
in the limitations.

### D-50 · A resume decision is bound to the pass it was made for · Day 14.5
- **Context:** The worker applied any `resume` payload to whatever interrupt
  was waiting. After a crash, arq retries a killed resume task once its lock
  expires. By then crash recovery may have finished that pass, and the job
  may be paused at pass 2, which the old task would approve without anyone
  having seen it. A browser tab left open on pass 1 could do the same.
- **Decision:**
  - `POST /resume` reads the waiting pass *after* claiming the job and puts
    it in the task payload.
  - The worker applies a decision only if the job row is `queued` and the
    payload's pass is the one the graph is waiting on. Otherwise it logs the
    task as stale and drops it.
  - Optional `expected_pass` in the request (additive); a mismatch gets 409.
    The UI always sends it.
- **Why:** An approval is only meaningful for the plan the reviewer saw. Day
  15 makes approvals attributable to a person, so this must hold first.
- **Status:** Active.

### D-51 · Rejections are capped too: rejecting the last pass ends the run · Day 14.5
- **Context:** D-27 said rejections count toward `MAX_LOOPS`, but only the
  critic's route checked the cap. Ten rejections meant ten full passes.
- **Decision:**
  - A rejection on the last pass routes to END.
  - If an earlier pass produced a report, it stands, as it does when the
    critic hits the cap. Otherwise the job fails with "plan rejected on the
    final pass".
  - The approval request carries `final_pass`, and the UI tells the reviewer
    that rejecting now ends the job.
- **Status:** Active.

### D-52 · A rejection discards the rejected plan's evidence · Day 14.5
- **Context:** Evidence accumulated across passes. After "Only AWS, please",
  the other companies' chunks from the rejected pass still reached the
  writer, and the reviewer's evidence counts included them.
- **Decision:**
  - On a rejection, `plan` resets the filing and news evidence. A critic loop
    still keeps and extends its evidence, as before.
  - The reviewer may narrow the request's company filter ("Amazon only"),
    never widen it. Before this, the request filter always won, so pass 2
    of a live run still covered both companies.
- **Verified live:** a two-company job rejected with "Amazon only" re-planned
  with Amazon alone, showed 4 filings instead of the 19 from pass 1, and
  cited only Amazon.
- **Trade-off:** `degraded` isn't reset. It has a merge reducer, and a reset
  would need a special marker. See the limitations.
- **Status:** Active.

### D-53 · A citation's quote must appear in its source · Day 14.5
- **Context:** Company, page and period came from the evidence, but the
  quote text came straight from the model, and no check compared it with the
  source. A misquoted figure could be archived next to a real page.
- **Decision:**
  - `hydrate` drops a citation whose quote isn't in its evidence snippet, as
    it already does for unknown references.
  - Matching ignores case, punctuation and whitespace, but not words, and an
    ellipsis may skip text.
  - The critic's existing check then flags any section left without
    citations.
- **Status:** Active.

### D-54 · Decode before stripping; everything from the web goes inside the fence · Day 14.5
- **Context:** `clean_text` stripped tags *before* decoding entities. So an
  encoded `&lt;/untrusted_web_content&gt;` came back as a live closing tag,
  which let web text escape the fence. News titles were also outside the
  fence.
- **Decision:**
  - Decode entities, then strip tags, repeating until the text stops
    changing.
  - In prompts, a news item's reference, date, title and snippet are all
    inside the fence, with `<` and `>` replaced by look-alikes, so nothing
    can close it.
- **Status:** Active; tested with plain, double-encoded and numeric-entity
  payloads.

### D-55 · `plan` survives a Search outage · Day 14.5
- **Context:** `plan` lists the indexed companies with a Search query. If
  Search was down, the job failed, contrary to D-31.
- **Decision:** If the listing fails, `plan` logs it and uses the request's
  own company filter. If Search really is down, `retrieve_filings` then
  records the gap.
- **Status:** Active.

### D-56 · arq `job_timeout` raised to 30 minutes · Day 14.5
- **Context:** One arq task can run a whole segment: plan through critique,
  then plan to compact again. Each LLM node may take up to 300 s. At 600 s,
  a slow but healthy run was cancelled, left `running`, and not retried
  until the worker restarted.
- **Decision:** `job_timeout = 1800`. LangGraph's node timeouts remain the
  real bound.
- **Status:** Active.

### D-57 · The gate fails on a missing score and on an answered unanswerable · Day 14.5
- **Context:**
  - RAGAS returns NaN when it can't score an answer, and NaN compares as
    "not below the threshold", so the faithfulness gate passed.
  - The smoke set includes an unanswerable question (q029), but the gate
    never checked abstention.
- **Decision:**
  - NaN becomes "no score" with an error recorded.
  - The gate (`evals.metrics.gate`, now unit-tested) fails when a metric
    has no score.
  - New gated metric: `abstention_rate: 1.0`.
- **Status:** Active.

### D-58 · Page hit is per source; stored results recomputed · Day 14.5
- **Context:** Page hit crossed every expected source with every expected
  page, so for comparisons another company's page counted as a hit. One
  company's page alone was also enough.
- **Decision:**
  - A comparison's `expected_pages` maps each source to its own pages.
  - A hit needs a page from every source.
  - The stored results were re-scored from their saved samples: hybrid 88%
    → 76%, vector 80% → 76%, hybrid_semantic unchanged at 100%.
- **Why:** A published number must mean what it says. The shipping decision
  (D-47) doesn't change; the corrected metric widens hybrid_semantic's lead.
- **Status:** Active.

### D-59 · Data ports on localhost only; bounded `companies` filter · Day 14.5
- **Decision:**
  - Compose binds Postgres and Redis to `127.0.0.1`, not every interface.
    Postgres has demo credentials.
  - `companies` accepts at most 10 names of at most 64 characters each.
- **Status:** Active.

---

## Security and governance (Day 15)

### D-60 · The API validates Entra ID v2 access tokens itself · Day 15
- **Decision:**
  - Every `/api/v1` route needs a bearer token.
  - `app/api/auth.py` checks the RS256 signature against the tenant's JWKS
    (PyJWT's cached `PyJWKClient`).
  - It checks the issuer (`login.microsoftonline.com/{tenant}/v2.0`), the
    audience (the API's client id, or `api://` plus it), the tenant, and
    expiry with 60 s of leeway. It requires `oid`, and ignores `alg: none`.
  - The caller is identified by `oid`, not by name or email.
  - `/health` stays open for probes.
- **Why no gateway or library middleware:** the checks are about 40 lines,
  every rule is visible and tested, and it works the same on the laptop,
  kind and Azure.
- **Keys outage:** if the signing keys can't be fetched, the API returns
  503, not 401. An Entra outage shouldn't look like a bad token.
- **Tested:** real RS256 tokens signed by a local key cover wrong audience,
  issuer, tenant, expiry, forged signature and an unsigned token.
- **Status:** Active.

### D-61 · Two app roles; analysts see only their own jobs · Day 15
- **Decision:**
  - `analyst` can submit, list and read their own jobs.
  - `approver` can do all that, see every job, see operations status, and
    decide approvals.
  - Roles are Entra app roles, so they arrive in the token's `roles` claim.
    Assignment is per user; the Free tier doesn't support groups.
  - Someone else's job returns 404, not 403, so job ids can't be probed.
  - Jobs record `submitted_by` (the `oid`) and a display name, as additive
    columns. Pre-Day 15 jobs have none and are visible only to approvers.
- **Status:** Active.

### D-62 · Separation of duties: nobody approves their own job · Day 15
- **Decision:**
  - `POST /resume` returns 403 if the caller submitted the job, even with
    the approver role, and audits the attempt as `approval_refused`.
  - The reviewer's `oid` and name travel with the decision into graph state
    and `approval-pass{n}.json`, which replaces `reviewer: null`.
- **Why:** the spec says the gate has real meaning only when a different
  person approves. A role check alone would let an approver wave through
  their own work.
- **Status:** Active.

### D-63 · Dev bypass is local-only, enforced at startup · Day 15
- **Decision:**
  - `DEV_AUTH_BYPASS` defaults to false.
  - When it's on, the caller's identity comes from `X-Dev-User` and
    `X-Dev-Roles` headers, which is how the offline tests run.
  - API startup raises if the bypass is on and `ENVIRONMENT != local`, and
    also raises if real mode lacks the tenant or client id.
- **Verified:** the real image with `ENVIRONMENT=prod` and the bypass on
  refuses to start.
- **Status:** Active.

### D-64 · App registrations by script: no secrets, assignment required, auth code + PKCE UI · Day 15
- **Decision:** `infra/entra/setup.sh` creates two registrations and is safe
  to re-run (the scope and role ids are fixed):
  - **`mia-api`:**
    - exposes `api://<id>` with the delegated scope `access_as_user`;
    - defines the `analyst` and `approver` app roles;
    - issues v2 tokens;
    - sets `appRoleAssignmentRequired`, so users without a role can't even
      get a token.
  - **`mia-ui`:** a public client with localhost redirect URIs and
    tenant-wide admin consent to that scope.
  - **No client secrets anywhere:** the API only validates tokens, and the
    UI is a public client.
- **Sign-in flow:** auth code with PKCE. The first build used device code,
  which needs no redirect, but Entra security defaults block it
  (AADSTS530035, F-18). Turning security defaults off to allow it was
  rejected: that would drop tenant-wide MFA for a Global Administrator
  account, just to suit a demo client.
- **How the redirect works in Streamlit:** it lands in a new Streamlit
  session. So the pending flow (state, PKCE verifier) is held in the UI
  process, keyed by `state`, for 10 minutes and used once. `get_token.py`
  uses MSAL's interactive browser sign-in on a loopback port.
- **Token cache:** MSAL's cache lives only in the browser session.
- **Status:** Active. Assigned: analyst to the Gmail account, approver to
  `approver@…onmicrosoft.com`.

### D-65 · Append-only `audit_events`; a person's action needs its audit record · Day 15
- **Decision:**
  - **Table columns:** `at`, `actor` (an `oid`, or `system`), `actor_name`,
    `action`, `job_id`, `detail`.
  - **Events:**
    - `job_submitted`, written in the same transaction as the job.
    - `approval_decided` and `approval_refused`.
    - `llm_call`: node, deployment, tokens.
    - `news_item_withheld`.
    - `job_completed` and `job_failed`.
  - **Append-only:** the app has no update or delete path, and on Postgres a
    trigger refuses UPDATE, DELETE and TRUNCATE (verified live).
  - **When a write fails:**
    - For a person's action (submit, decide), the action fails too; a
      decision is un-claimed and returns 503.
    - For the worker's own events, the failure is logged and the run
      carries on.
  - The API doesn't expose the table, as the spec asks.
- **Status:** Active. The trigger doesn't stop a database superuser (see the
  limitations).

### D-66 · News is screened for injection before any prompt; fails closed · Day 15
- **Decision:**
  - A classifier call flags instruction-like news items, and Azure's Prompt
    Shields refusals count as flags.
  - A flagged item is dropped, recorded in `screened_out`, audited, and
    marked in `degraded` (`news_screened`).
  - If the screen can't run, all of that batch's news is dropped.
  - `NEWS_SCREEN_ENABLED` switches it off.
  - Details and the other layers: ADR 0004.
- **Status:** Active; checked live (3 of 3 attacks flagged, 0 of 3 genuine
  items).

### D-67 · The UI shows identity and roles from the API and hides what it would refuse · Day 15
- **Decision:**
  - The sidebar shows the user and their roles from `GET /api/v1/me`.
  - The approve and reject buttons are hidden from analysts, and from the
    job's own submitter.
  - Operations is shown to approvers only.
  - None of this is security; the API enforces it.
  - Without the `AUTH_*` settings, the UI runs in a labelled dev mode that
    sends the dev headers.
- **Status:** Active.

---

## Observability (Day 16)

### D-68 · One Langfuse trace per job; trace id = job id; keys in Key Vault; optional · Day 16
- **Decision:**
  - **Trace id:** the job id without dashes, so the job, its audit rows and
    its trace share one id.
  - **Runs:** each worker run (start, resume, continue after a crash) is a
    `job:*` span in the same trace.
  - **Trace attributes:** user is the submitter's `oid`, session is the job
    id, and tags carry the variant, model and outcome.
  - **Keys** are read from Key Vault at worker startup. If they're missing,
    or `TRACING_ENABLED=false`, jobs run untraced.
- **Why:** the spec asks for `job_id` as the trace id. Keeping the whole job,
  approval waits included, in one trace makes "cost per report" a single
  number. And observability must never be a new way for a job to fail.
- **Status:** Active.

### D-69 · Callback handler for nodes and tools; explicit spans for model calls and searches · Day 16
- **Context:**
  - The spec says to use the LangChain callback handler. It sees LangGraph
    nodes and the MCP tool.
  - It can't see our model calls: we call the OpenAI and Search SDKs
    directly, for structured outputs and our own retry and fallback layer.
- **Decision:**
  - `observe()` adds explicit observations: a `generation` per chat call, an
    `embedding`, and a `retriever` per search.
  - LangGraph runs a node in a task that can't see the handler's current
    span, so these are parented explicitly to the node's span, found through
    the node's callback manager.
  - That lookup relies on the handler's internal run map;
    `tests/test_observability.py` pins the nesting, so an SDK upgrade that
    breaks it fails a test.
- **Alternative rejected:** switching to LangChain chat models just to be
  traced. That would replace working, tested code for a tracing convenience.
- **Status:** Active.

### D-70 · Langfuse prices calls from model names; we never compute cost for tracing · Day 16
- **Decision:**
  - Generations report the model name (`AZURE_OPENAI_CHAT_MODEL`, default
    `gpt-5-mini`) and raw token usage. Langfuse applies its own price table.
  - The deployment name is kept in metadata.
- **Why:** Langfuse's cost and the eval harness's (evals/pricing.yaml) are
  then independent calculations, which is what makes reconciling them
  meaningful (D-72).
- **Status:** Active.

### D-71 · Prometheus `/metrics` for request latency and jobs by status · Day 16
- **Decision:**
  - A middleware records `http_request_duration_seconds` by method, route
    template and status.
  - `research_jobs{status}` is read from the database at scrape time.
  - Each non-probe request is logged in one line.
  - `/metrics` is open like `/health`: counts and timings only, no job
    content.
- **Why:** it's the spec's "/metrics or structured logs" option, in a format
  the Day 17 Kubernetes setup can scrape. Tracing covers per-job detail, and
  metrics cover the service.
- **Status:** Active.

### D-72 · Eval runs can be traced; `evals.reconcile` checks cost against Langfuse · Day 16
- **Decision:**
  - `evals.run --trace` makes one trace per question, with session set to
    the run name.
  - `evals.reconcile` fetches each trace's cost through the v2 observations
    API. New Langfuse Cloud organisations can't use the legacy traces API.
  - It compares against the harness and fails if the two differ by more
    than 5%.
- **Result:** the smoke set matched exactly ($0.007859 on both sides).
- **Status:** Active.

---

## Containers and Kubernetes (Day 17)

Full reasoning: [ADR 0005](adr/0005-kubernetes.md).

### D-73 · Images: pinned digests, no package manager, per-component HEALTHCHECK; bytecode kept · Day 17
- **Decision:**
  - The python and uv base images are pinned by digest (multi-arch index).
  - pip, setuptools and wheel are removed from the runtime image, and the
    `USER` is numeric.
  - `HEALTHCHECK` runs `python -m app.healthcheck`, which uses
    `MIA_COMPONENT` to pick the api, worker or news check.
  - Ingestion-only packages (`pypdf`, the text splitters) moved to an
    `ingest` dependency group, which images don't install.
- **Measured:** the runtime image stayed at 397 MB. Dropping precompiled
  bytecode would save 57 MB but doubled import time (2.1 s → 4.4 s) for
  every start and probe, so the bytecode stays.
- **Status:** Active.

### D-74 · One Helm chart, verified on kind; plain manifests for demo Postgres/Redis; no AKS · Day 17
- **Decision:**
  - `infra/helm/` has production-leaning `values.yaml` and a
    `values-local.yaml` for kind. `infra/kind/up.sh` builds, loads and
    installs.
  - Postgres and Redis use the official images in small templates, not the
    Bitnami charts (whose images moved behind a subscription in 2025).
  - AKS was skipped on purpose: a real monthly cost for the same talking
    point. Day 18 uses Container Apps.
- **Status:** Active.

### D-75 · News server as its own service; the worker reconnects · Day 17
- **Decision:**
  - In Kubernetes, the MCP server runs as its own Deployment over streamable
    HTTP (`NEWS_MCP_URL`).
  - It enforces DNS-rebinding protection with an explicit allowed-hosts list
    (FastMCP turns the protection off when bound off-loopback).
  - The worker's `NewsToolRunner` reconnects with exponential backoff (1 s
    up to 60 s) whenever the session can't start or drops. Before, one
    failure left every later job without news until the worker restarted.
- **Status:** Active; verified on kind (worker → news over HTTP).

### D-76 · Probes separate liveness from readiness; one worker, Recreate · Day 17
- **Decision:**
  - **api:** liveness on `/livez` (new; no dependency checks), readiness on
    `/health`.
  - **worker:** an exec probe on arq's health key, now refreshed every 30 s
    (arq's default is hourly).
  - **news:** a TCP check.
  - **Worker count:** always one replica, with the `Recreate` strategy, so a
    rollout never runs two workers (D-25).
  - **Schema setup:** `init_db` runs in the worker too, under a Postgres
    advisory lock, since Kubernetes gives no start order.
- **Status:** Active.

### D-77 · Least-privilege pods and NetworkPolicies; generated Postgres password · Day 17
- **Decision:**
  - **Pods:** non-root, a read-only root filesystem, all capabilities
    dropped, no privilege escalation, the seccomp `RuntimeDefault` profile,
    and no service-account token.
  - **Ingress NetworkPolicies:** only the worker may reach news; only the
    api and worker may reach Postgres and Redis.
  - **Postgres password:** generated by the chart and kept across upgrades.
- **Verified on kind** by probing between pods: every allowed path was open
  and every other was blocked.
- **Status:** Active.

---

## Azure deployment and CI/CD (Day 18)

Full reasoning: [ADR 0006](adr/0006-deployment-topology.md).

### D-78 · Azure Container Apps (api, worker, news, ui, redis), Bicep in two passes · Day 18
- **Decision:**
  - **Apps and ingress:** five container apps in one environment. The api
    and ui are public over HTTPS, news uses internal HTTP, Redis internal
    TCP, and the worker has no ingress.
  - **Scaling:** the worker runs exactly one replica; the api, ui and news
    scale to zero.
  - **Deployment:** `infra/azure/main.bicep`, applied by
    `infra/azure/deploy.sh`: base resources, then images, then the database
    role, then apps. `--what-if` previews.
  - **The UI is deployed too**, a deviation (the spec lists three apps). The
    public demo is then usable in a browser. Its https address was added as
    a redirect on `mia-ui`.
- **Status:** Active. Deployed 2026-09-29; `/health` ok (Postgres and Redis),
  and the worker connected to news over internal HTTP.

### D-79 · Keyless Postgres: Entra-only auth, a token per connection, no password anywhere · Day 18
- **Decision:**
  - The server has password authentication disabled.
  - Apps connect as the `id-mia-db` role (created by
    `infra/azure/db_setup.py`, run as the Entra admin). Their managed
    identity's token is the password, fetched for every new connection:
    asyncpg and psycopg both accept a callable (`app/db_auth.py`).
  - `DATABASE_AUTH=password` keeps Compose and kind as they were.
- **Why:** the project's rule is keyless everywhere. A password in Key
  Vault would have been the one exception, and a secret to rotate.
- **Status:** Active; verified live.

### D-80 · User-assigned identities per app plus a shared database identity · Day 18
- **Decision:**
  - Each app has its own user-assigned identity with only its roles (see
    ADR 0006).
  - The api and worker also share `id-mia-db`, the Postgres role.
  - This deviates from the spec's system-assigned identities, for two
    reasons: the AcrPull grant must exist before an app's first revision
    pulls its image, and both apps alter the same tables, so they need one
    owner.
- **Status:** Active.

### D-81 · Images built locally or on the pipeline agent (ACR Tasks blocked) · Day 18
- **Context:** `az acr build` failed with `TasksOperationsNotAllowed`, a
  subscription-level restriction (F-22).
- **Decision:**
  - `deploy.sh` builds `linux/amd64` with `docker buildx` and pushes, logged
    in with Entra (`az acr login`; the registry admin user is off).
  - The pipeline builds natively on its amd64 agent.
  - Images are tagged with the commit, `-dirty` if built from uncommitted
    changes.
- **Status:** Active.

### D-82 · Azure Pipelines: test, eval gate, build, deploy; workload identity federation · Day 18
- **Decision:**
  - The stages are: ruff, mypy and pytest; then the smoke eval against real
    models; then the image build; then `az containerapp update` and a
    `/health` poll.
  - Build and deploy run on `main` only.
  - Auth is through an Azure Resource Manager service connection with
    workload identity federation, so no secret exists.
  - The pipeline's identity needs data-plane roles for the eval gate
    (OpenAI User, Search Index Data Reader).
- **Status:** Written and wired, since the Azure DevOps pipeline already
  points at `infra/azure-pipelines.yml`. It waits on the service connection
  and on Microsoft's free hosted-agent grant.

### D-83 · $50 monthly budget with actual and forecast alerts · Day 18
- **Decision:** a subscription budget `mia-monthly-50`, which emails at 80%
  and 100% of actual spend and at 100% of forecast. There was no budget
  before.
- **Status:** Active.

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
| F-16 | 14 | The fallback metric counted a plain-hybrid run as 30 "semantic fallbacks" | Count fallbacks only for `hybrid_semantic` runs (D-48); stored results recomputed |
| F-17 | 15 | Azure Prompt Shields refused the whole screening batch (400 `content_filter`) because one item was a jailbreak, which would have dropped all news. Before Day 15, such a snippet reaching `compact` or `write` would have failed the job. | Treat the refusal as a flag: screen item by item (D-66, ADR 0004) |
| F-18 | 15 | Device code sign-in was refused with AADSTS530035: the tenant's security defaults block that flow | D-64: auth code + PKCE with localhost redirects; security defaults stay on |
| F-19 | 16 | Langfuse's legacy `GET /traces/{id}` returns 410 for organisations created after 2026-09-16 | Read through `/v2/observations` (D-72) |
| F-20 | 16 | The smoke gate failed once on citation validity (0.75): the model shortened long reference ids (`amzn-332` for `amzn-annual-report-10k-332`); two re-runs scored 1.0 | Recorded as a limitation; proposed fix: short per-prompt reference labels mapped back in Python |
| F-21 | 17 | Uninstalling pip in a later image layer didn't shrink the image (the base layer keeps the bytes); precompiled bytecode is 70 MB of the venv | Size kept, trade-off recorded (D-73) |
| F-22 | 18 | `az acr build` refused: ACR Tasks not permitted on this subscription | Build with docker buildx (local) / docker (agent) and push (D-81) |

---

## Documented elsewhere

- **Spec deviations:** recorded inline in `docs/PHASE1_SPEC.md` and the other
  specs where they occurred.
- **Deferred work:** the Phase 3 roadmap (chunking comparison, MLflow,
  Airflow, long-term memory) will be in `docs/ROADMAP.md`, a Phase 3
  deliverable.
