# Known limitations

Trade-offs we accepted on purpose, and gaps we know about, as of Day 14.5
(after the pre-Day 15 code review). Each
entry says why it's acceptable for now and what would remove it. The
reasoning behind the decisions is in [DECISIONS.md](DECISIONS.md).

**Impact:**
- **High:** blocks production use.
- **Medium:** affects quality or operations.
- **Low:** cosmetic, or only matters at scale.

## Security and governance

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| **No authentication.** Anyone who can reach the API can submit, read and **approve** jobs; `POST /resume` is open. | High | A local demo on a laptop; the spec schedules auth for Day 15 | Day 15: Entra ID tokens, `analyst`/`approver` roles |
| The UI's role dropdown is a simulation; approval records have `reviewer: null` | High | Clearly labelled; nothing trusts it | Day 15 |
| Report immutability is enforced by the app (`overwrite=False`), not by the storage account | Medium | Enough to show the design; a policy is an infrastructure setting | A container immutability policy (WORM) at deployment |
| No classifier screen for injected instructions in news; defences are sanitising, fencing and escaping only | Medium | Layers 1–3 are in place | Day 15: classifier pass, ADR 0004 |
| No audit table; decisions are recorded only in the archive and logs | Medium | The archive covers approvals and provenance for completed jobs | Day 15: append-only `audit_events` |
| Local containers authenticate with your Azure CLI login (mounted `~/.azure`) | Low | Local development only; the deployable image doesn't include it | Day 18: managed identity |
| Raw exception text (`TypeName: message`) is stored in `job.error` and shown to clients; it can include hostnames or request details | Low | No external users yet | Day 15: a generic message for clients, details in logs only |
| Markdown neutralising misses reference-style links (`[x][1]` plus `[1]: url`) in model prose | Low | Needs a model to produce them; images, inline links and HTML are handled (D-38) | Drop link-definition lines when rendering |

## Durability and operations

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| Crash recovery assumes **one worker**. With several, releasing the stale lock could run a live job twice. | Medium | Compose runs one worker; you accepted this | A heartbeat or lease check before releasing locks |
| Checkpoints are never pruned; every step of every job is kept | Low | Tens of KB per step at portfolio scale | A retention policy (Phase 3/4 deployment) |
| Schema changes are idempotent `ALTER`s at startup, not migrations | Low | One schema, a few columns | Alembic |
| A node interrupted mid-call re-runs on resume (at-least-once), repeating that LLM or Search call | Low | The cost is one repeated call, never corrupted state | — (inherent to checkpointing at node boundaries) |
| A job whose enqueue was lost (the API died between saving the row and enqueueing) stays `queued` forever; recovery only looks at `running` jobs | Medium | A narrow window, and re-running is safe (the checkpoint decides what to do) | Recovery also re-enqueues `queued` jobs older than a few minutes that have no arq task |
| The news MCP subprocess isn't restarted if it dies; later jobs degrade to "news unavailable" until the worker restarts | Low | News is supplementary and the gap is reported | Reconnect on the next job |
| The MCP server's first tool call reads the Tavily key from Key Vault synchronously, using part of the 30 s call budget | Low | Only the first call after startup; it degrades if it runs out | Read the key at server startup |
| Archiving is best-effort: a job can complete without an archive bundle | Low | Losing a finished report to a storage error would be worse | Operations view alert or retry job, if needed |
| No `/metrics` endpoint, tracing or dashboards yet | Medium | Scheduled | Day 16: Langfuse, metrics |

## Quality and behaviour

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| **Semantic ranker quota:** the Free plan allows 1,000 semantic queries a month, roughly 50–200 reports | Medium | Automatic fallback to hybrid (recall 0.95), recorded per query | The Standard semantic plan (billed per 1,000 queries) for real usage |
| The critic is rarely fully satisfied, so two passes (and two approvals) are common | Low | The loop is capped at 2; the cost is bounded | Phase 3 evals: decide whether the second pass pays off |
| The compact brief sometimes leaves out filing references; the safety net adds them back | Low | The writer still sees every reference and cites correctly | Tune the compact prompt; measure with evals |
| The writer occasionally still adds a section saying news is unavailable | Low | `data_gaps` records it reliably anyway | Prompt tuning |
| News search is by company name only: results are broad, not topic-specific | Low | News is supplementary | Also call `get_market_context(plan.subject)` |
| The same citation can appear twice in a section | Low | The Markdown numbering removes duplicates | Remove duplicates when citations are filled in |
| Right after a rejection, the progress list shows "Write report" for a few seconds instead of re-planning | Low | Cosmetic and brief | Expose the last approval decision to the UI |
| Fiscal years differ between companies (Microsoft's ends June 30), so comparisons span different periods | Low | Reports and golden answers state the periods | — (inherent to the filings) |
| **Number grounding is prompt-only:** quotes are checked against their source (D-53), but figures in a section's prose are not | Medium | The writer is told never to compute; the critic checks too | A deterministic check that every figure in a section appears in its cited snippets |
| `degraded` never clears: if news fails on pass 1 and works on pass 2 (or the pass was rejected), the report still lists "live news unavailable" | Low | It errs on the side of disclosure | A reset marker in the reducer, or per-pass tracking |
| One failed search (of several per pass) drops all filing hits for that pass; one failed company's news drops all news | Low | Degrades and is reported; the next pass retries | Gather per query with `return_exceptions` and keep the successes |
| Model fallback (D-30) can in practice only fire on 429s: a hung call hits the 300 s node timeout before 4 retries of the SDK's 600 s timeout finish | Low | No fallback deployment is configured; a short per-call timeout would cut legitimate long reasoning calls | A per-call timeout sized so retries and the fallback fit the node budget, if a fallback deployment is added |
| The UI keeps the "rendered from the job record" report for the session if the archive read fails once; the job page fetches the job twice per render | Low | Cosmetic; the content is the same | Don't cache the fallback; reuse the first fetch |

## Evaluation

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| **The judge is the model being judged**: RAGAS uses the same gpt-5-mini deployment | Medium | Only one chat deployment; comparing variants is still fair | A second judge deployment from a different model family |
| Small samples: 30 questions; the smoke gate sees about 8 citations. Run-to-run noise is about ±0.05 on RAGAS metrics, and each variant was run once. | Medium | The spec's size; thresholds account for it (D-44); the benchmark's gains are several times the noise | Grow the golden set; repeat runs and report spread |
| Latency p95s come from a single run of 30 sequential queries on a shared Free-tier service; end-to-end p95 varied 5.7–16.4 s between equal runs | Low | Medians are stable; retrieval is a small part of what users wait for | Repeat runs; measure under load on the deployed service |
| The golden set was written by one author, from the filing text | Low | Answers are quoted and page-cited, so they can be checked | A human review pass |
| The eval gives each question its company filter; the real system's planner picks companies itself | Low | It isolates the retriever, as the spec asks | A full-graph eval variant |
| "Expected page retrieved" is page-level and overstates hits (a page spans several chunks) | Low | Labelled as such; chunk-level recall comes from RAGAS | Expected-snippet matching |
| **The smoke gate isn't in CI yet**: `infra/azure-pipelines.yml` is a placeholder | Medium | Run by hand before merging; the spec schedules the pipeline | Day 18: pipeline runs `evals.run --smoke` |
| Abstention is the model's own `abstained` flag; "abstained" with citations or a figure still counts as a correct decline | Low | Seen consistent in every run so far | Flag answers where the flag and the content disagree |
| p95 on small samples is nearest-rank: with 5 smoke questions it is the maximum, with 30 the second-highest | Low | A standard definition; the benchmark reports medians too | Print n next to percentiles |
| q030's ground truth ("the filing does not mention China") hasn't been checked against the full PDF | Low | No retrieved chunk mentions it; unanswerable items were searched when written | Check by hand |
| Faithfulness is also computed for declined answerable questions, where it is less meaningful | Low | False abstention is reported separately | Exclude declined answers from answer metrics |
| Cost per query uses list prices and an estimated embedding token count (characters ÷ 4) | Low | Embedding cost is negligible; prices are in `evals/pricing.yaml` | Day 16: reconcile against Langfuse |

## Data, platform and capacity

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| The corpus is three annual 10-Ks; no quarterly data | Low | A deliberate scope decision (D-07) | — |
| The AI Search Free tier is at **62% of 50 MB** (after the Day 14 rebuild, with the `title` field); re-ingesting (upserting) grows storage until the service compacts | Medium | Enough for 3 filings; prefer `--recreate` to repeated upserts | A larger tier, or 512-dimension embeddings |
| Low model quotas make jobs slow (a report takes 3–6 minutes; the full eval about 21) | Low | A cost choice on a dev subscription | Higher TPM, or parallelism in the eval |
| Adding a company means editing the alias list in `ingestion/parse.py` | Low | Three companies | Read the issuer from the cover page only, or from configuration |
| The jobs list has no pagination (limit ≤ 200) | Low | Demo volumes | Cursor pagination |
| The local image is 773 MB (it includes the Azure CLI); the UI image is 569 MB | Low | Local only; the runtime image is 355 MB | Day 17: image hardening |
| The `uv:0.12` build image tag floats | Low | Minor-version pinned; the lockfile pins everything installed | Day 17: pin by digest |
| Re-ingesting **without** `--recreate` leaves stale chunks: ids are `{doc}-{chunk_no}`, so a PDF that now yields fewer chunks keeps its old higher-numbered ones | Medium | Ingestion is a one-off and the docs use `--recreate` | Delete a document's chunks with `chunk_no >= n` after uploading |
| `--recreate` deletes the live index before embedding starts; a failed run leaves it empty and every job degrades | Medium | A local, supervised one-off | Build into a new index name and switch `AZURE_SEARCH_INDEX` (the Free tier allows 3) |
| No unit tests for `ingestion/` (chunking, filename parsing, cover-page metadata) | Low | Verified end to end with `ingestion.verify` and the page checks | Add them before the next corpus change |

## Resolved

| Limitation | Resolved | How |
|---|---|---|
| Retrieval missed exact-figure lookups (e.g. "1,576,000 employees"), so 16% of answerable questions were declined | Day 14 | Hybrid + semantic ranking: 0% wrongly declined, expected page retrieved 100% |
| Context precision of 0.556 (about half the top-8 chunks were noise) | Day 14 | Semantic ranker: 0.806 |
| A stale or retried resume task could approve a later pass nobody saw | Day 14.5 | Decisions are bound to a pass (D-50) |
| Rejections weren't capped: every rejection started another full pass | Day 14.5 | Rejecting the last pass ends the run (D-51) |
| A rejected plan's evidence still reached the writer | Day 14.5 | Reset on rejection (D-52) |
| Citation quotes were taken from the model unchecked | Day 14.5 | Quotes must appear in their source (D-53) |
| Entity-encoded tags could close the untrusted-content fence; news titles sat outside it | Day 14.5 | Decode-then-strip, everything fenced and defused (D-54) |
| A Search outage while planning failed the job | Day 14.5 | Falls back to the request's filter (D-55) |
| arq's 600 s timeout could cut a healthy run and leave it `running` | Day 14.5 | 1,800 s (D-56) |
| The smoke gate passed on a NaN faithfulness score and ignored abstention | Day 14.5 | Missing scores fail; abstention gated (D-57) |
| The page-hit metric counted another company's page for comparisons (hybrid overstated 88% vs 76%) | Day 14.5 | Per-source page hit; results re-scored (D-58) |
| UI rendered critique, plan and job titles as Markdown (links from web-influenced text) | Day 14.5 | Escaped |
| Postgres and Redis were published on all interfaces | Day 14.5 | Bound to 127.0.0.1 (D-59) |
