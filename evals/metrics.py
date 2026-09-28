"""Plain-Python metrics and aggregation.

The two checks that matter most for a filings tool, and don't need an LLM:
citation validity (did it cite only what was retrieved?) and abstention (did
it decline the unanswerable questions instead of inventing a number?).
"""

import math
from collections.abc import Iterable
from statistics import mean
from typing import Any

from evals.answer import Sample
from evals.golden import GoldenItem, Thresholds

RAGAS_METRICS = [
    "context_precision",
    "context_recall",
    "faithfulness",
    "answer_relevancy",
]


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, math.ceil(q / 100 * len(ordered)) - 1)
    return ordered[idx]


def rate(flags: Iterable[bool]) -> float | None:
    items = list(flags)
    return sum(items) / len(items) if items else None


def citation_validity(samples: list[Sample]) -> float | None:
    cited = sum(len(s.citations) for s in samples)
    return sum(len(s.valid_citations) for s in samples) / cited if cited else None


def source_hit(sample: Sample, item: GoldenItem) -> bool:
    """Every expected filing appears among the retrieved chunks."""
    return set(item.expected_sources) <= set(sample.retrieved_sources)


def page_hit(sample: Sample, item: GoldenItem) -> bool:
    """For every expected source, a retrieved chunk comes from a page of that
    source the answer is on (a comparison needs both companies' pages)."""
    retrieved = set(sample.retrieved_pages)
    return all(
        any((src, p) in retrieved for p in pages)
        for src, pages in item.pages_by_source().items()
    )


def cost_usd(sample: Sample, pricing: dict[str, dict[str, Any]]) -> float:
    chat, embed = pricing["chat"], pricing["embedding"]
    return (
        sample.prompt_tokens * float(chat["input_per_million"])
        + sample.completion_tokens * float(chat["output_per_million"])
        + sample.embed_tokens_est * float(embed["input_per_million"])
    ) / 1_000_000


def summarize(
    samples: list[Sample],
    items: dict[str, GoldenItem],
    ragas: dict[str, dict[str, float | None]],
    pricing: dict[str, dict[str, Any]],
    variant: str = "vector",
) -> dict[str, Any]:
    answerable = [s for s in samples if s.answerable]
    unanswerable = [s for s in samples if not s.answerable]

    def ragas_mean(metric: str, group: list[Sample]) -> float | None:
        values = [
            v
            for s in group
            if (v := ragas.get(s.id, {}).get(metric)) is not None and not math.isnan(v)
        ]
        return mean(values) if values else None

    out: dict[str, Any] = {
        "n": len(samples),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        **{m: ragas_mean(m, answerable) for m in RAGAS_METRICS},
        "citation_validity": citation_validity(samples),
        "abstention_rate": rate(s.abstained for s in unanswerable),
        "false_abstention_rate": rate(s.abstained for s in answerable),
        "source_hit_rate": rate(source_hit(s, items[s.id]) for s in answerable),
        "page_hit_rate": rate(page_hit(s, items[s.id]) for s in answerable),
        "retrieval_p50_ms": percentile([s.retrieval_ms for s in samples], 50),
        "retrieval_p95_ms": percentile([s.retrieval_ms for s in samples], 95),
        "end_to_end_p95_ms": percentile(
            [s.retrieval_ms + s.answer_ms for s in samples], 95
        ),
        # Questions a hybrid_semantic run had to serve with plain hybrid
        # (semantic ranker unavailable). Not applicable to other variants.
        "semantic_fallbacks": (
            sum("hybrid_semantic" not in s.retrieval_modes for s in samples)
            if variant == "hybrid_semantic"
            else None
        ),
        "cost_per_query_usd": mean(cost_usd(s, pricing) for s in samples)
        if samples
        else None,
    }
    by_category: dict[str, dict[str, Any]] = {}
    for category in ("factual", "comparative", "temporal", "unanswerable"):
        group = [s for s in samples if s.category == category]
        if not group:
            continue
        row: dict[str, Any] = {"n": len(group)}
        if category == "unanswerable":
            row["abstention_rate"] = rate(s.abstained for s in group)
        else:
            row.update({m: ragas_mean(m, group) for m in RAGAS_METRICS})
            row["source_hit_rate"] = rate(source_hit(s, items[s.id]) for s in group)
            row["false_abstention_rate"] = rate(s.abstained for s in group)
        row["citation_validity"] = citation_validity(group)
        by_category[category] = row
    out["by_category"] = by_category
    return out


def gate(summary: dict[str, Any], thresholds: Thresholds) -> list[str]:
    """The CI gate's failures (empty = pass). A metric that couldn't be
    computed fails too: "no score" must never read as "good enough"."""
    failures = []
    for metric in ("faithfulness", "citation_validity", "abstention_rate"):
        value, floor = summary.get(metric), getattr(thresholds, metric)
        if value is None or math.isnan(value):
            failures.append(f"{metric} could not be computed")
        elif value < floor:
            failures.append(f"{metric} {value:.3f} < {floor}")
    return failures
