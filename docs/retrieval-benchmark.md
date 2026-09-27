# Retrieval benchmark: vector vs hybrid vs hybrid + semantic ranker

**Shipped:** `RETRIEVAL_MODE=hybrid_semantic`. If the semantic ranker is
unavailable (e.g. the Free plan's monthly quota runs out), it automatically
falls back to hybrid.

## Setup

- **Golden set:** 30 hand-written questions over three 10-Ks: 12 factual, 8
  comparative, 5 year-over-year and 5 unanswerable
  ([`evals/golden_set.yaml`](../evals/golden_set.yaml)).
- **Per question:** one retrieval (top-8, with the question's company
  filter), one grounded answer from exactly those chunks, then scoring.
- **Scoring:** RAGAS 0.4.3 for context precision and recall, faithfulness and
  answer relevancy; plain Python for citation validity and abstention.
- **Same conditions for all three variants:** the same day (2026-09-27), the
  same index (rebuilt with a `title` field and the `filings-semantic`
  configuration), commit `010b727`, run one question at a time after a
  warm-up.
- **Models:** answers from `chat-mini` (gpt-5-mini), embeddings from
  `text-embedding-3-small`. Azure AI Search Free tier, semantic ranker on the
  Free plan.
- **The variants:**
  - **vector:** HNSW, k = 8.
  - **hybrid:** BM25 on the query text plus vector (50 candidates), fused by
    Azure with Reciprocal Rank Fusion (RRF); top 8.
  - **hybrid_semantic:** hybrid, then the semantic ranker reorders the fused
    results using the `title` and `content` fields; top 8.

## Results

| variant | context precision | context recall | faithfulness | answer relevancy | citation validity | abstention (unanswerable) | false abstention (answerable) | expected page retrieved | retrieval p50 / p95 | end-to-end p95 | cost/query |
|---|---|---|---|---|---|---|---|---|---|---|---|
| vector | 0.542 | 0.847 | 0.967 | 0.806 | 100% | 100% | 16% | 80% | 539 / 1095 ms | 16.4 s | $0.0017 |
| hybrid | 0.592 | 0.947 | 0.990 | 0.869 | 100% | 100% | 4% | 88% | 536 / 812 ms | 5.7 s | $0.0015 |
| **hybrid_semantic** | **0.806** | **1.000** | 0.973 | 0.858 | 100% | 100% | **0%** | **100%** | 592 / 1163 ms | 7.2 s | $0.0015 |

- **Where the numbers come from:** full per-question results are in
  `results/bench-{vector,hybrid,hybrid_semantic}.{json,md}`.
- **Cost/query** covers the answer call plus embeddings, at list prices
  ([`evals/pricing.yaml`](../evals/pricing.yaml)). It excludes the RAGAS judge
  and the semantic ranker (see below).
- **Two measurement caveats:** "expected page retrieved" is page-level, and
  the judge is the same model that writes the answers
  ([KNOWN_LIMITATIONS](KNOWN_LIMITATIONS.md)).

**Context recall by category:**
- vector: factual 0.83, comparative 0.83, temporal 0.90
- hybrid_semantic: 1.00 in every category

**Context precision on factual questions:** 0.47 (vector) → 0.62 (hybrid) →
0.93 (hybrid_semantic).

**How much is noise:** the Day 13 baseline used the same vector
configuration a day earlier (`results/baseline-vector.json`). Against this
run it differs by 0.01 in precision, 0.04 in recall and 0.02 in answer
relevancy. So **differences under about 0.05 are within run-to-run noise**,
and the faithfulness spread (0.967–0.990) is noise, not a signal. The gains
below are several times larger than that.

## Interpretation

**Quality.** Hybrid + semantic ranking wins clearly on the metrics that
depend on retrieval:
- Context precision rises from 0.54 to 0.81: most of the 8 chunks handed to
  the model are now relevant, where about half were noise before.
- Context recall reaches 1.00, and the expected page is retrieved for every
  answerable question.
- Wrongly declined questions drop from 4 of 25 to none.

Those four were exact-figure lookups ("1,576,000 employees", "223,000
people", a stated growth rate). Embeddings blur exact figures. BM25 matches
them word for word, and the semantic ranker then puts the right chunk first:
Amazon's employee-count chunk went from not retrieved (vector) to rank 2
(hybrid) to rank 1 (hybrid + semantic). Hybrid alone recovers most of the
recall (0.95) but little of the precision (0.59). The reranker is what turns
"the answer is somewhere in the 8" into "the top chunks are the answer".

What did **not** change matters just as much. Every variant declined all 5
unanswerable questions, including "Azure's standalone revenue", where a
related Intelligent Cloud figure sits right there to be misused. Citation
validity stayed at 100%. Better retrieval did not make the system more
willing to guess. Faithfulness is high everywhere (0.97–0.99), within noise,
because the answer prompt already confines the model to its context.

**Latency and cost.** The semantic ranker costs about **+55 ms at the
median** (592 vs 539 ms). The p95 figures (812–1,163 ms) come from a single
run of 30 sequential queries on a shared Free-tier service; the spread
between hybrid and the other two is within that noise, not an effect of the
variant. Either way, retrieval is well under a second, while one answer takes
several seconds. End-to-end latency is dominated by the LLM (its p95 ranged
from 5.7 to 16.4 s between otherwise equal runs), so the reranker's cost
doesn't show up in what a user waits for. Token cost per query is unchanged
at about $0.0015; if anything it falls slightly, because precise context
means less off-topic text in the prompt.

The real cost is **the semantic ranker's quota**. The Free plan includes
1,000 semantic queries a month. The agent issues one per sub-question, per
company, per pass: about 4–5 for a single-company report and up to about 20
for a two-company report that loops twice. That's roughly **50–200 reports a
month** before the quota runs out. A full eval run uses 31 queries and a CI
smoke run 6. Beyond the free allowance, the Standard semantic plan bills per
1,000 queries (check current Azure pricing). BM25 adds no charge.

**What shipped and why.** `hybrid_semantic` is the default. It is the only
variant that retrieved the expected evidence for every answerable question,
and it wrongly declined none, while keeping perfect abstention and citation
validity and adding negligible latency. The quota risk is covered by
design: if Azure refuses a semantic query, retrieval logs a warning and
re-runs it as hybrid. Hybrid is the second-best variant here (recall 0.95,
false abstention 4%), so running out of quota degrades gracefully rather than
back to vector-only quality. Eval results record every fallback
(`semantic_fallbacks`: 0 in this run), so a quota problem can't
masquerade as a quality change. The CI smoke gate now runs the production
mode; thresholds were re-set from four hybrid_semantic measurements
([`evals/thresholds.yaml`](../evals/thresholds.yaml)).

## Reproduce

```bash
uv run python -m ingestion.ingest --recreate      # index with the semantic configuration
uv run python -m evals.run --variant vector          --name bench-vector
uv run python -m evals.run --variant hybrid          --name bench-hybrid
uv run python -m evals.run --variant hybrid_semantic --name bench-hybrid_semantic
uv run python -m evals.run --smoke                   # production mode; CI gate
```

Each full run takes 12–15 minutes and uses 31 semantic queries (for the
semantic variant). Avoid running it in a loop on the Free plan.
