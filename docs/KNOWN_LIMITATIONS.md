# Known limitations

Trade-offs we accepted on purpose, and gaps we know about, as of Day 13. Each
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

## Durability and operations

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| Crash recovery assumes **one worker**. With several, releasing the stale lock could run a live job twice. | Medium | Compose runs one worker; you accepted this | A heartbeat or lease check before releasing locks |
| Checkpoints are never pruned; every step of every job is kept | Low | Tens of KB per step at portfolio scale | A retention policy (Phase 3/4 deployment) |
| Schema changes are idempotent `ALTER`s at startup, not migrations | Low | One schema, a few columns | Alembic |
| A node interrupted mid-call re-runs on resume (at-least-once), repeating that LLM or Search call | Low | The cost is one repeated call, never corrupted state | — (inherent to checkpointing at node boundaries) |
| Archiving is best-effort: a job can complete without an archive bundle | Low | Losing a finished report to a storage error would be worse | Operations view alert or retry job, if needed |
| No `/metrics` endpoint, tracing or dashboards yet | Medium | Scheduled | Day 16: Langfuse, metrics |

## Quality and behaviour

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| **Retrieval misses exact-figure lookups.** In the baseline, 3 of 4 wrongly declined questions were chunks not retrieved (e.g. "1,576,000 employees"). | Medium | It declines rather than guessing; that is the safe failure | Day 14: hybrid (BM25) and semantic ranking, measured |
| Context precision is 0.556: about half of the top-8 chunks are noise | Medium | Baseline; not tuned yet, per the spec | Day 14: semantic reranker |
| The critic is rarely fully satisfied, so two passes (and two approvals) are common | Low | The loop is capped at 2; the cost is bounded | Phase 3 evals: decide whether the second pass pays off |
| The compact brief sometimes leaves out filing references; the safety net adds them back | Low | The writer still sees every reference and cites correctly | Tune the compact prompt; measure with evals |
| The writer occasionally still adds a section saying news is unavailable | Low | `data_gaps` records it reliably anyway | Prompt tuning |
| News search is by company name only: results are broad, not topic-specific | Low | News is supplementary | Also call `get_market_context(plan.subject)` |
| The same citation can appear twice in a section | Low | The Markdown numbering removes duplicates | Remove duplicates when citations are filled in |
| Right after a rejection, the progress list shows "Write report" for a few seconds instead of re-planning | Low | Cosmetic and brief | Expose the last approval decision to the UI |
| Fiscal years differ between companies (Microsoft's ends June 30), so comparisons span different periods | Low | Reports and golden answers state the periods | — (inherent to the filings) |

## Evaluation

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| **The judge is the model being judged**: RAGAS uses the same gpt-5-mini deployment | Medium | Only one chat deployment; comparing variants is still fair | A second judge deployment from a different model family |
| Small samples: 30 questions; the smoke gate sees about 7 citations | Medium | The spec's size; thresholds account for it (D-44) | Grow the golden set; repeat runs |
| The golden set was written by one author, from the filing text | Low | Answers are quoted and page-cited, so they can be checked | A human review pass |
| The eval gives each question its company filter; the real system's planner picks companies itself | Low | It isolates the retriever, as the spec asks | A full-graph eval variant |
| "Expected page retrieved" is page-level and overstates hits (a page spans several chunks) | Low | Labelled as such; chunk-level recall comes from RAGAS | Expected-snippet matching |
| Faithfulness is also computed for declined answerable questions, where it is less meaningful | Low | False abstention is reported separately | Exclude declined answers from answer metrics |
| Cost per query uses list prices and an estimated embedding token count (characters ÷ 4) | Low | Embedding cost is negligible; prices are in `evals/pricing.yaml` | Day 16: reconcile against Langfuse |

## Data, platform and capacity

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| The corpus is three annual 10-Ks; no quarterly data | Low | A deliberate scope decision (D-07) | — |
| The AI Search Free tier is at **67% of 50 MB**; re-ingesting (upserting) grows storage until the service compacts | Medium | Enough for 3 filings; avoid frequent re-ingests | A larger tier, or 512-dimension embeddings |
| Low model quotas make jobs slow (a report takes 3–6 minutes; the full eval about 21) | Low | A cost choice on a dev subscription | Higher TPM, or parallelism in the eval |
| Adding a company means editing the alias list in `ingestion/parse.py` | Low | Three companies | Read the issuer from the cover page only, or from configuration |
| The jobs list has no pagination (limit ≤ 200) | Low | Demo volumes | Cursor pagination |
| The local image is 773 MB (it includes the Azure CLI); the UI image is 569 MB | Low | Local only; the runtime image is 355 MB | Day 17: image hardening |
