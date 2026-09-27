"""Retrieval modes (vector, hybrid, hybrid_semantic) and the semantic fallback,
against a fake Azure Search client that records what it was asked."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from azure.core.exceptions import HttpResponseError

from app.graph import retrieval
from ingestion.index_schema import build_index

ROW = {
    "id": "amzn-annual-report-10k-157",
    "content": "Operating income was $80.0 billion",
    "company": "Amazon",
    "ticker": "AMZN",
    "doc_type": "10-K",
    "period": "2025-12-31",
    "source_blob": "amzn_annual_report_10k.pdf",
    "chunk_no": 157,
    "page": 27,
    "@search.score": 0.03,
    "@search.reranker_score": 2.9,
}


class FakeResults:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self) -> "FakeResults":
        self._it = iter(self._rows)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class FakeSearch:
    def __init__(self, fail_semantic: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_semantic = fail_semantic

    async def search(self, **kwargs: Any) -> FakeResults:
        self.calls.append(kwargs)
        if self.fail_semantic and kwargs.get("query_type") == "semantic":
            error = HttpResponseError(message=self.fail_semantic)
            error.status_code = 400
            raise error
        return FakeResults([ROW])


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch) -> FakeSearch:
    fake = FakeSearch()
    monkeypatch.setattr(retrieval, "get_async_search_client", lambda: fake)
    monkeypatch.setattr(
        retrieval, "embed_queries", AsyncMock(return_value=[[0.1, 0.2]])
    )
    return fake


async def test_vector_mode_sends_no_text(fake_search: FakeSearch) -> None:
    [hit] = await retrieval.search_many(
        ["AWS operating income"], [], k=8, mode="vector"
    )

    call = fake_search.calls[0]
    assert call["search_text"] is None
    assert call["vector_queries"][0].k_nearest_neighbors == 8
    assert "query_type" not in call
    assert (hit["score"], hit["retrieval_mode"]) == (0.03, "vector")


async def test_hybrid_mode_sends_text_and_wider_vector_pool(
    fake_search: FakeSearch,
) -> None:
    await retrieval.search_many(
        ["AWS operating income"], ["Amazon"], k=8, mode="hybrid"
    )

    call = fake_search.calls[0]
    assert call["search_text"] == "AWS operating income"  # BM25 side
    assert call["vector_queries"][0].k_nearest_neighbors == retrieval.HYBRID_CANDIDATES
    assert call["top"] == 8
    assert call["filter"] == "search.in(company, 'Amazon', ',')"
    assert "query_type" not in call


async def test_semantic_mode_reranks_and_uses_reranker_score(
    fake_search: FakeSearch,
) -> None:
    [hit] = await retrieval.search_many(["q"], [], k=8, mode="hybrid_semantic")

    call = fake_search.calls[0]
    assert call["query_type"] == "semantic"
    assert call["semantic_configuration_name"] == "filings-semantic"
    assert call["search_text"] == "q"
    assert (hit["score"], hit["retrieval_mode"]) == (2.9, "hybrid_semantic")


async def test_semantic_quota_error_falls_back_to_hybrid(
    fake_search: FakeSearch,
) -> None:
    fake_search.fail_semantic = "Semantic search quota exceeded for this service"

    [hit] = await retrieval.search_many(["q"], [], k=8, mode="hybrid_semantic")

    assert hit["retrieval_mode"] == "hybrid"
    assert "query_type" not in fake_search.calls[-1]  # retried as plain hybrid


async def test_other_search_errors_are_not_masked(fake_search: FakeSearch) -> None:
    fake_search.fail_semantic = "Invalid filter expression"

    with pytest.raises(HttpResponseError):
        await retrieval.search_many(["q"], [], k=8, mode="hybrid_semantic")


async def test_mode_defaults_to_settings(
    fake_search: FakeSearch, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    get_settings.cache_clear()

    [hit] = await retrieval.search_many(["q"], [], k=8)

    assert hit["retrieval_mode"] == "hybrid"


def test_index_has_title_field_and_semantic_config() -> None:
    index = build_index("filings-v1", 1536, "filings-semantic")

    title = next(f for f in index.fields if f.name == "title")
    assert title.searchable
    assert index.semantic_search is not None
    configs = index.semantic_search.configurations or []
    [config] = configs
    assert config.name == "filings-semantic"
    fields = config.prioritized_fields
    assert fields.title_field is not None
    assert fields.title_field.field_name == "title"
    assert [f.field_name for f in fields.content_fields or []] == ["content"]
    assert index.vector_search is not None
    profiles = index.vector_search.profiles or []
    assert profiles[0].name == "vector-hnsw"  # vector profile kept
