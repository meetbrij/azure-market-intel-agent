"""Eval harness logic that needs no network: golden set, metrics, aggregation."""

from collections import Counter
from typing import Any

import pytest
from pydantic import ValidationError

from evals.answer import Sample
from evals.golden import GoldenItem, load_golden, load_pricing, load_thresholds
from evals.metrics import (
    citation_validity,
    cost_usd,
    page_hit,
    percentile,
    source_hit,
    summarize,
)

AMZN = "amzn_annual_report_10k.pdf"
GOOGL = "googl_annual_report_10k.pdf"


def sample(**overrides: Any) -> Sample:
    base: dict[str, Any] = {
        "id": "q1",
        "category": "factual",
        "answerable": True,
        "question": "q",
        "ground_truth": "t",
        "retrieved_ids": ["c1", "c2"],
        "retrieved_sources": [AMZN],
        "retrieved_pages": [(AMZN, 24), (AMZN, 69)],
        "contexts": ["x", "y"],
        "answer": "a",
        "abstained": False,
        "citations": ["c1"],
        "valid_citations": ["c1"],
        "retrieval_ms": 100.0,
        "answer_ms": 900.0,
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "embed_tokens_est": 10,
        "deployments": ["chat-mini"],
    }
    base.update(overrides)
    return Sample.model_validate(base)


def item(**overrides: Any) -> GoldenItem:
    base: dict[str, Any] = {
        "id": "q1",
        "question": "q",
        "ground_truth": "t",
        "expected_sources": [AMZN],
        "expected_pages": [24],
        "category": "factual",
        "answerable": True,
    }
    base.update(overrides)
    return GoldenItem.model_validate(base)


# ---------- golden set ----------


def test_golden_set_has_the_specified_mix() -> None:
    items = load_golden()
    assert len(items) == 30
    assert Counter(i.category for i in items) == {
        "factual": 12,
        "comparative": 8,
        "temporal": 5,
        "unanswerable": 5,
    }
    assert all(not i.expected_sources for i in items if not i.answerable)
    assert all(
        len(i.expected_sources) == 2 for i in items if i.category == "comparative"
    )


def test_smoke_ids_exist_and_cover_unanswerable() -> None:
    by_id = {i.id: i for i in load_golden()}
    smoke = load_thresholds().smoke_ids
    assert len(smoke) == 5 and set(smoke) <= set(by_id)
    assert any(not by_id[i].answerable for i in smoke)


def test_inconsistent_items_are_rejected() -> None:
    with pytest.raises(ValidationError):
        item(category="unanswerable", answerable=True)
    with pytest.raises(ValidationError):
        item(expected_sources=[])


# ---------- metrics ----------


def test_citation_validity_counts_citations_not_samples() -> None:
    samples = [
        sample(citations=["c1", "c2"], valid_citations=["c1", "c2"]),
        sample(id="q2", citations=["bogus"], valid_citations=[]),
    ]
    assert citation_validity(samples) == pytest.approx(2 / 3)
    assert citation_validity([sample(citations=[], valid_citations=[])]) is None


def test_source_and_page_hits() -> None:
    comparative = item(expected_sources=[AMZN, GOOGL], expected_pages=[24, 34])
    assert not source_hit(sample(), comparative)  # Google filing missing
    assert source_hit(sample(retrieved_sources=[AMZN, GOOGL]), comparative)
    assert page_hit(sample(), item())  # AMZN p.24 retrieved
    assert not page_hit(sample(retrieved_pages=[(AMZN, 99)]), item())


def test_percentile_and_cost() -> None:
    assert percentile([float(x) for x in range(1, 101)], 95) == 95.0
    assert percentile([], 95) is None
    pricing = load_pricing()
    expected = (1000 * 0.25 + 500 * 2.00 + 10 * 0.02) / 1_000_000
    assert cost_usd(sample(), pricing) == pytest.approx(expected)


def test_summary_separates_answerable_and_unanswerable() -> None:
    samples = [
        sample(id="a1"),
        sample(id="a2", abstained=True, citations=[], valid_citations=[]),
        sample(
            id="u1",
            category="unanswerable",
            answerable=False,
            abstained=True,
            citations=[],
            valid_citations=[],
        ),
        sample(id="u2", category="unanswerable", answerable=False, abstained=False),
    ]
    items = {
        "a1": item(id="a1"),
        "a2": item(id="a2"),
        "u1": item(
            id="u1", category="unanswerable", answerable=False, expected_sources=[]
        ),
        "u2": item(
            id="u2", category="unanswerable", answerable=False, expected_sources=[]
        ),
    }
    ragas: dict[str, dict[str, float | None]] = {
        "a1": {"faithfulness": 1.0},
        "a2": {"faithfulness": 0.5},
    }

    out = summarize(samples, items, ragas, load_pricing())

    assert out["faithfulness"] == pytest.approx(0.75)  # answerable only
    assert out["abstention_rate"] == 0.5  # u2 answered an unanswerable question
    assert out["false_abstention_rate"] == 0.5  # a2 declined an answerable one
    assert out["by_category"]["unanswerable"]["abstention_rate"] == 0.5
    assert out["context_recall"] is None  # not scored in this run
