# Eval: bench-vector

- **When:** 2026-09-27T13:44:36+00:00 · **commit:** `010b727` · **variant:** `vector` · **top-k:** 8 · **index:** `filings-v1`
- **Answer model:** `chat-mini` · **embeddings:** `text-embedding-3-small` · **judge:** gpt-5-mini via `chat-mini` (RAGAS 0.4.3)
- **Questions:** 30 (25 answerable, 5 unanswerable)

| metric | value |
|---|---|
| context precision | 0.542 |
| context recall | 0.847 |
| faithfulness | 0.967 |
| answer relevancy | 0.806 |
| citation validity | 100.0% |
| abstention (unanswerable) | 100.0% |
| false abstention (answerable) | 16.0% |
| expected filing retrieved | 100.0% |
| expected page retrieved (page-level; a page spans chunks) | 76.0% |
| retrieval p50 / p95 (ms) | 538.788 / 1095.335 |
| end-to-end p95 (ms) | 16367.517 |
| semantic ranker fallbacks | — |
| cost / query (USD) | 0.00169 |

## By category

| category | n | precision | recall | faithfulness | relevancy | citation validity | abstention |
|---|---|---|---|---|---|---|---|
| factual | 12 | 0.468 | 0.833 | 0.931 | 0.837 | 100.0% | — |
| comparative | 8 | 0.530 | 0.833 | 1.000 | 0.715 | 100.0% | — |
| temporal | 5 | 0.738 | 0.900 | 1.000 | 0.880 | 100.0% | — |
| unanswerable | 5 | — | — | — | — | — | 100.0% |

## Per question

| id | category | abstained | citations (valid/total) | faithfulness | recall |
|---|---|---|---|---|---|
| q001 | factual | yes | 0/0 | 1.000 | 0.000 |
| q002 | factual | no | 2/2 | 0.500 | 1.000 |
| q003 | factual | no | 1/1 | 1.000 | 1.000 |
| q004 | factual | no | 1/1 | 1.000 | 1.000 |
| q005 | factual | no | 2/2 | 1.000 | 1.000 |
| q006 | factual | yes | 0/0 | 0.667 | 1.000 |
| q007 | factual | no | 4/4 | 1.000 | 1.000 |
| q008 | factual | no | 1/1 | 1.000 | 1.000 |
| q009 | factual | yes | 0/0 | 1.000 | 0.000 |
| q010 | factual | no | 2/2 | 1.000 | 1.000 |
| q011 | factual | no | 1/1 | 1.000 | 1.000 |
| q012 | factual | no | 2/2 | 1.000 | 1.000 |
| q013 | comparative | no | 2/2 | 1.000 | 1.000 |
| q014 | comparative | no | 2/2 | 1.000 | 1.000 |
| q015 | comparative | no | 2/2 | 1.000 | 1.000 |
| q016 | comparative | no | 2/2 | 1.000 | 0.333 |
| q017 | comparative | yes | 0/0 | 1.000 | 0.333 |
| q018 | comparative | no | 2/2 | 1.000 | 1.000 |
| q019 | comparative | no | 4/4 | 1.000 | 1.000 |
| q020 | comparative | no | 2/2 | 1.000 | 1.000 |
| q021 | temporal | no | 2/2 | 1.000 | 1.000 |
| q022 | temporal | no | 2/2 | 1.000 | 1.000 |
| q023 | temporal | no | 1/1 | 1.000 | 0.500 |
| q024 | temporal | no | 2/2 | 1.000 | 1.000 |
| q025 | temporal | no | 2/2 | 1.000 | 1.000 |
| q026 | unanswerable | yes | 0/0 | — | — |
| q027 | unanswerable | yes | 0/0 | — | — |
| q028 | unanswerable | yes | 0/0 | — | — |
| q029 | unanswerable | yes | 0/0 | — | — |
| q030 | unanswerable | yes | 0/0 | — | — |
