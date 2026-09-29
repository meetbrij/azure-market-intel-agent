# Eval: smoke-traced-day16

- **When:** 2026-09-29T10:26:40+00:00 · **commit:** `fee6651` · **variant:** `hybrid_semantic` · **top-k:** 8 · **index:** `filings-v1`
- **Answer model:** `chat-mini` · **embeddings:** `text-embedding-3-small` · **judge:** gpt-5-mini via `chat-mini` (RAGAS 0.4.3)
- **Questions:** 5 (4 answerable, 1 unanswerable)

| metric | value |
|---|---|
| context precision | — |
| context recall | — |
| faithfulness | 1.000 |
| answer relevancy | — |
| citation validity | 75.0% |
| abstention (unanswerable) | 100.0% |
| false abstention (answerable) | 0.0% |
| expected filing retrieved | 100.0% |
| expected page retrieved (page-level; a page spans chunks) | 100.0% |
| retrieval p50 / p95 (ms) | 1073.228 / 1281.980 |
| end-to-end p95 (ms) | 49663.685 |
| semantic ranker fallbacks | 0 |
| cost / query (USD) | 0.00157 |

## By category

| category | n | precision | recall | faithfulness | relevancy | citation validity | abstention |
|---|---|---|---|---|---|---|---|
| factual | 2 | — | — | 1.000 | — | 100.0% | — |
| comparative | 1 | — | — | 1.000 | — | 0.0% | — |
| temporal | 1 | — | — | 1.000 | — | 100.0% | — |
| unanswerable | 1 | — | — | — | — | — | 100.0% |

## Per question

| id | category | abstained | citations (valid/total) | faithfulness | recall |
|---|---|---|---|---|---|
| q002 | factual | no | 2/2 | 1.000 | — |
| q012 | factual | no | 2/2 | 1.000 | — |
| q014 | comparative | no | 0/2 | 1.000 | — |
| q023 | temporal | no | 2/2 | 1.000 | — |
| q029 | unanswerable | yes | 0/0 | — | — |
