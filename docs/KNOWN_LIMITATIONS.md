# Known limitations

Trade-offs we accepted on purpose, and gaps we know about, as of Day 18. Each
entry says why it's acceptable for now and what would remove it. The
reasoning behind the decisions is in [DECISIONS.md](DECISIONS.md).

**Impact:**
- **High:** blocks production use.
- **Medium:** affects quality or operations.
- **Low:** cosmetic, or only matters at scale.

## Security and governance

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| Report immutability is enforced by the app (`overwrite=False`), not by the storage account | Medium | Enough to show the design; a policy is an infrastructure setting | A container immutability policy (WORM) at deployment |
| `audit_events` is append-only through the app and a trigger, but the app's database user owns the table and could drop the trigger | Medium | Stops accidental or app-level tampering; in Azure too, the apps' role (`id-mia-db`) owns the tables | A separate owner role for migrations and a non-owner runtime role; or ship events to an external log store |
| A token stays valid until it expires (about an hour): removing a role or a user takes effect only at the next token | Low | Standard for bearer tokens; short lifetime | Continuous access evaluation, or a short token lifetime policy |
| The UI keeps sign-ins in memory: reloading the page (or restarting the UI) means signing in again, and pending sign-ins are per UI process | Low | Tokens never touch disk; one UI container | A server-side session store if the UI is scaled out |
| The injection screen uses the same model family as the writer, and its false positives drop genuine news | Low | Prompt Shields is an independent second detector; news is supplementary and withheld items are reported | A dedicated classifier; measure precision/recall on a labelled set |
| Jobs created before Day 15 have no submitter, so only approvers can see them | Low | Old demo data | — |
| Local containers (Compose and kind) authenticate with your Azure CLI login (mounted `~/.azure`, read-only) | Low | Local development only; Azure uses managed identities (Day 18), and the deployed image has no CLI | — |
| Raw exception text (`TypeName: message`) is stored in `job.error` and shown to clients; it can include hostnames or request details | Low | Clients are now only signed-in users of this tenant | Day 16: a generic message for clients, details in logs and traces |
| Markdown neutralising misses reference-style links (`[x][1]` plus `[1]: url`) in model prose | Low | Needs a model to produce them; images, inline links and HTML are handled (D-38) | Drop link-definition lines when rendering |

## Durability and operations

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| Crash recovery assumes **one worker**. With several, releasing the stale lock could run a live job twice. | Medium | Compose runs one worker; you accepted this | A heartbeat or lease check before releasing locks |
| Checkpoints are never pruned; every step of every job is kept | Low | Tens of KB per step at portfolio scale | A retention policy (Phase 3/4 deployment) |
| Schema changes are idempotent `ALTER`s at startup, not migrations | Low | One schema, a few columns | Alembic |
| A node interrupted mid-call re-runs on resume (at-least-once), repeating that LLM or Search call | Low | The cost is one repeated call, never corrupted state | — (inherent to checkpointing at node boundaries) |
| A job whose enqueue was lost (the API died between saving the row and enqueueing) stays `queued` forever; recovery only looks at `running` jobs | Medium | A narrow window, and re-running is safe (the checkpoint decides what to do) | Recovery also re-enqueues `queued` jobs older than a few minutes that have no arq task |
| The MCP server's first tool call reads the Tavily key from Key Vault synchronously, using part of the 30 s call budget | Low | Only the first call after startup; it degrades if it runs out | Read the key at server startup |
| Archiving is best-effort: a job can complete without an archive bundle | Low | Losing a finished report to a storage error would be worse | Operations view alert or retry job, if needed |
| Langfuse's legacy trace API is being retired (Nov 2026); our reads already use the v2 observations API, but the SDK's LangChain handler is the part to watch on upgrades | Low | Nesting is pinned by a test | Re-run `tests/test_observability.py` on every SDK upgrade |
| Tracing sends questions, prompts (filing and news excerpts) and reports to Langfuse Cloud | Medium | A demo with public filings; the keys are in Key Vault | Langfuse's `mask` hook for sensitive fields, or self-hosted Langfuse in the deployment region |
| The parenting of our spans relies on the Langfuse handler's internal run map | Low | One small function; a test fails if it breaks | A public SDK hook, if Langfuse adds one |

## Quality and behaviour

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| **Semantic ranker quota:** the Free plan allows 1,000 semantic queries a month, roughly 50–200 reports | Medium | Automatic fallback to hybrid (recall 0.95), recorded per query | The Standard semantic plan (billed per 1,000 queries) for real usage |
| The critic is rarely fully satisfied, so two passes (and two approvals) are common | Low | The loop is capped at 2; the cost is bounded | Phase 3 evals: decide whether the second pass pays off |
| The compact brief sometimes leaves out filing references; the safety net adds them back | Low | The writer still sees every reference and cites correctly | Tune the compact prompt; measure with evals |
| **The model occasionally shortens long reference ids** (`amzn-332` for `amzn-annual-report-10k-332`): in the eval those citations count as invalid (the smoke gate failed once in three runs on Day 16, once in four on Day 13); in the graph they are dropped | Medium | Intermittent; grounding drops them rather than trusting them | Short per-prompt labels (`[E1]`, `[E2]` …) mapped back to chunk ids in Python |
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
| The pipeline (eval gate included) can't run until the Azure DevOps service connection exists and Microsoft grants free hosted agents (a 2–3 day request) | Medium | The pipeline is written and wired to `infra/azure-pipelines.yml`; deploys are done with `infra/azure/deploy.sh` meanwhile | The grant, or a self-hosted agent |
| Abstention is the model's own `abstained` flag; "abstained" with citations or a figure still counts as a correct decline | Low | Seen consistent in every run so far | Flag answers where the flag and the content disagree |
| p95 on small samples is nearest-rank: with 5 smoke questions it is the maximum, with 30 the second-highest | Low | A standard definition; the benchmark reports medians too | Print n next to percentiles |
| q030's ground truth ("the filing does not mention China") hasn't been checked against the full PDF | Low | No retrieved chunk mentions it; unanswerable items were searched when written | Check by hand |
| Faithfulness is also computed for declined answerable questions, where it is less meaningful | Low | False abstention is reported separately | Exclude declined answers from answer metrics |
| Cost per query uses list prices and an estimated embedding token count (characters ÷ 4) | Low | Reconciled with Langfuse on Day 16: identical to the sixth decimal | Use the embeddings call's real usage |

## Data, platform and capacity

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| The corpus is three annual 10-Ks; no quarterly data | Low | A deliberate scope decision (D-07) | — |
| The AI Search Free tier is at **62% of 50 MB** (after the Day 14 rebuild, with the `title` field); re-ingesting (upserting) grows storage until the service compacts | Medium | Enough for 3 filings; prefer `--recreate` to repeated upserts | A larger tier, or 512-dimension embeddings |
| Low model quotas make jobs slow (a report takes 3–6 minutes; the full eval about 21) | Low | A cost choice on a dev subscription | Higher TPM, or parallelism in the eval |
| Adding a company means editing the alias list in `ingestion/parse.py` | Low | Three companies | Read the issuer from the cover page only, or from configuration |
| The jobs list has no pagination (limit ≤ 200) | Low | Demo volumes | Cursor pagination |
| Images are large for what they do: runtime 397 MB, local (with the Azure CLI) 775 MB, UI 591 MB. The bulk is transitive dependencies, and dropping precompiled bytecode (−57 MB) doubled start time | Low | Measured in ADR 0005; the hardening targets what the image allows, not its size | One Postgres driver instead of two; a distroless base |
| Re-ingesting **without** `--recreate` leaves stale chunks: ids are `{doc}-{chunk_no}`, so a PDF that now yields fewer chunks keeps its old higher-numbered ones | Medium | Ingestion is a one-off and the docs use `--recreate` | Delete a document's chunks with `chunk_no >= n` after uploading |
| `--recreate` deletes the live index before embedding starts; a failed run leaves it empty and every job degrades | Medium | A local, supervised one-off | Build into a new index name and switch `AZURE_SEARCH_INDEX` (the Free tier allows 3) |
| No unit tests for `ingestion/` (chunking, filename parsing, cover-page metadata) | Low | Verified end to end with `ingestion.verify` and the page checks | Add them before the next corpus change |

## Kubernetes (kind)

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| In-cluster Postgres has no backups or HA; Redis has no persistence, so a Redis restart loses queued (not yet running) tasks | Medium | Demo environments only; production values point at managed services | Managed Postgres/Redis (Day 18) |
| On first start the worker restarts until Postgres accepts connections (no start order in Kubernetes) | Low | Seconds; restarts are Kubernetes' retry | An init container waiting for Postgres |
| No Ingress on kind (no controller); local runs use port-forward | Low | The sign-in redirect is `localhost:8501` anyway; the Ingress template is linted with production values | An ingress controller in the kind setup |
| The UI must run one replica (a sign-in in progress is held in its process) | Low | Demo client | A shared session store |
| Egress isn't restricted: pods can reach any external host | Low | They need Azure, Tavily, Langfuse and Entra; FQDN egress rules need a CNI that supports them | FQDN-based egress policy in the real cluster |

## Azure (Container Apps)

| Limitation | Impact | Why accepted | Removed by |
|---|---|---|---|
| A worker deploy starts the new revision before stopping the old one, so a deploy during a running job can briefly run two workers (unsafe with D-25's lock release) | Medium | Container Apps has no Recreate strategy; deploys are rare and manual-gated to `main` | Deploy between jobs; or a lease/heartbeat check before recovery releases locks |
| Postgres is reached over its public endpoint (Entra-only auth, TLS, firewall: Azure services + admin IP), not a private endpoint | Medium | A virtual network and private DNS cost more than the demo warrants | VNet-integrated Container Apps environment + private endpoint |
| "Allow Azure services" admits any Azure-hosted client to the Postgres firewall (auth still required) | Low | Container Apps' outbound IPs aren't fixed without a VNet | The private endpoint above |
| The api scales to zero: the first request after idle waits for a cold start (~10–20 s) | Low | Saves cost; the UI keeps polling | `minReplicas: 1` for the api |
| The worker has no health probe in Azure (Container Apps has no exec probes; it has no port) | Low | The platform restarts it if it exits; a hung loop isn't detected | A tiny HTTP health endpoint in the worker |
| ACR Tasks (`az acr build`) is blocked on this subscription, so images are built locally (amd64 under emulation, slow) or on the pipeline agent | Low | Works; only slower locally | An Azure support request to enable ACR Tasks |

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
| **No authentication**: anyone who could reach the API could submit, read and approve | Day 15 | Entra ID tokens validated by the API; `analyst`/`approver` app roles (D-60, D-61) |
| The UI's role dropdown was a simulation; approvals recorded `reviewer: null` | Day 15 | Roles from the token; the reviewer's identity on each decision and in the archive; submitters can't approve (D-62, D-67) |
| No classifier screen for injected instructions in news | Day 15 | Classifier + Azure Prompt Shields, fail closed (D-66, ADR 0004) |
| No audit table | Day 15 | Append-only `audit_events` (D-65) |
| No `/metrics`, tracing or dashboards | Day 16 | Langfuse trace per job; Prometheus `/metrics` (D-68 to D-71) |
| The news MCP subprocess wasn't restarted if it died, so later jobs degraded until the worker restarted | Day 17 | The runner reconnects with backoff; news now runs as its own service (D-75) |
| The `uv` build image tag floated; base images weren't pinned | Day 17 | Pinned by digest (D-73) |
