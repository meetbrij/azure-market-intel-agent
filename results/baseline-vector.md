# Eval: baseline-vector

- **When:** 2026-09-26T07:39:39+00:00 · **commit:** `e455495` · **variant:** `vector` · **top-k:** 8 · **index:** `filings-v1`
- **Answer model:** `chat-mini` · **embeddings:** `text-embedding-3-small` · **judge:** gpt-5-mini via `chat-mini` (RAGAS 0.4.3)
- **Questions:** 30 (25 answerable, 5 unanswerable)

| metric | value |
|---|---|
| context precision | 0.556 |
| context recall | 0.807 |
| faithfulness | 0.967 |
| answer relevancy | 0.788 |
| citation validity | 95.2% |
| abstention (unanswerable) | 100.0% |
| false abstention (answerable) | 16.0% |
| expected filing retrieved | 100.0% |
| expected page retrieved (page-level; a page spans chunks) | 80.0% |
| retrieval p50 / p95 (ms) | 490.776 / 804.881 |
| end-to-end p95 (ms) | 10173.134 |
| cost / query (USD) | 0.00161 |

## By category

| category | n | precision | recall | faithfulness | relevancy | citation validity | abstention |
|---|---|---|---|---|---|---|---|
| factual | 12 | 0.468 | 0.792 | 0.931 | 0.750 | 93.3% | — |
| comparative | 8 | 0.595 | 0.833 | 1.000 | 0.787 | 100.0% | — |
| temporal | 5 | 0.704 | 0.800 | 1.000 | 0.878 | 90.0% | — |
| unanswerable | 5 | — | — | — | — | — | 100.0% |

## Per question

| id | category | abstained | citations (valid/total) | faithfulness | recall |
|---|---|---|---|---|---|
| q001 | factual | yes | 0/0 | 0.500 | 0.000 |
| q002 | factual | no | 2/2 | 1.000 | 1.000 |
| q003 | factual | no | 1/1 | 1.000 | 1.000 |
| q004 | factual | no | 1/1 | 1.000 | 1.000 |
| q005 | factual | no | 2/2 | 1.000 | 1.000 |
| q006 | factual | yes | 0/0 | 1.000 | 1.000 |
| q007 | factual | no | 3/3 | 1.000 | 1.000 |
| q008 | factual | no | 1/1 | 1.000 | 0.500 |
| q009 | factual | yes | 0/0 | 0.667 | 0.000 |
| q010 | factual | no | 1/2 | 1.000 | 1.000 |
| q011 | factual | no | 1/1 | 1.000 | 1.000 |
| q012 | factual | no | 2/2 | 1.000 | 1.000 |
| q013 | comparative | no | 2/2 | 1.000 | 1.000 |
| q014 | comparative | no | 2/2 | 1.000 | 1.000 |
| q015 | comparative | no | 2/2 | 1.000 | 1.000 |
| q016 | comparative | no | 3/3 | 1.000 | 0.333 |
| q017 | comparative | yes | 2/2 | 1.000 | 0.333 |
| q018 | comparative | no | 2/2 | 1.000 | 1.000 |
| q019 | comparative | no | 2/2 | 1.000 | 1.000 |
| q020 | comparative | no | 2/2 | 1.000 | 1.000 |
| q021 | temporal | no | 1/2 | 1.000 | 1.000 |
| q022 | temporal | no | 3/3 | 1.000 | 1.000 |
| q023 | temporal | no | 1/1 | 1.000 | 0.000 |
| q024 | temporal | no | 2/2 | 1.000 | 1.000 |
| q025 | temporal | no | 2/2 | 1.000 | 1.000 |
| q026 | unanswerable | yes | 0/0 | — | — |
| q027 | unanswerable | yes | 0/0 | — | — |
| q028 | unanswerable | yes | 0/0 | — | — |
| q029 | unanswerable | yes | 0/0 | — | — |
| q030 | unanswerable | yes | 0/0 | — | — |
