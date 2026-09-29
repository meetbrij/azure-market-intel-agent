"""AI Search retrieval: vector, hybrid (BM25 + vector, RRF) or hybrid +
semantic reranking, chosen by Settings.retrieval_mode."""

import asyncio
import logging
from typing import Any, Literal

from azure.core.exceptions import HttpResponseError
from azure.search.documents.models import VectorizedQuery

from app.azure_clients import get_async_aoai, get_async_search_client
from app.config import get_settings
from app.observability import observe
from app.resilience import with_retries

log = logging.getLogger(__name__)

SELECT_FIELDS = [
    "id",
    "content",
    "company",
    "ticker",
    "doc_type",
    "period",
    "source_blob",
    "chunk_no",
    "page",
]

_companies: list[str] | None = None


async def embed_queries(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    with observe(
        "embed", "embedding", model=s.azure_openai_embed_model, input=texts
    ) as span:
        resp = await with_retries(
            "embed",
            lambda: get_async_aoai().embeddings.create(
                model=s.azure_openai_embed_deployment, input=texts
            ),
        )
        if span is not None:
            span.update(
                usage_details={"input": resp.usage.prompt_tokens},
                metadata={"deployment": s.azure_openai_embed_deployment},
            )
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def company_filter(companies: list[str]) -> str | None:
    if not companies:
        return None
    # Canonical names never contain commas or quotes (see ingestion/parse.py).
    names = ",".join(c.replace("'", "''") for c in companies)
    return f"search.in(company, '{names}', ',')"


Mode = Literal["vector", "hybrid", "hybrid_semantic"]

# Vector candidates fed into RRF fusion (and so into the semantic ranker,
# which reranks up to 50). Wider than top-k so fusion has something to fuse.
HYBRID_CANDIDATES = 50


def semantic_unavailable(exc: HttpResponseError) -> bool:
    """The semantic ranker refused the query (e.g. the Free tier's monthly
    quota is used up). Azure errors rather than silently degrading."""
    return "semantic" in str(exc).lower()


async def _search(
    query: str, vector: list[float], companies: list[str], k: int, mode: Mode
) -> list[dict[str, Any]]:
    # Results are fetched while iterating, so retry the search and the read.
    if mode == "hybrid_semantic":
        try:
            return await with_retries(
                "search", lambda: _search_once(query, vector, companies, k, mode)
            )
        except HttpResponseError as e:
            if not semantic_unavailable(e):
                raise
            log.warning(
                "Semantic ranker unavailable (%s); falling back to hybrid",
                str(e.message)[:200],
            )
            mode = "hybrid"
    return await with_retries(
        "search", lambda: _search_once(query, vector, companies, k, mode)
    )


async def _search_once(
    query: str, vector: list[float], companies: list[str], k: int, mode: Mode
) -> list[dict[str, Any]]:
    knn = k if mode == "vector" else max(k, HYBRID_CANDIDATES)
    options: dict[str, Any] = {}
    if mode == "hybrid_semantic":
        options = {
            "query_type": "semantic",
            "semantic_configuration_name": get_settings().semantic_configuration,
        }
    results = await get_async_search_client().search(
        search_text=None if mode == "vector" else query,
        vector_queries=[
            VectorizedQuery(
                vector=vector, k_nearest_neighbors=knn, fields="content_vector"
            )
        ],
        filter=company_filter(companies),
        select=SELECT_FIELDS,
        top=k,
        **options,
    )
    hits: list[dict[str, Any]] = []
    async for r in results:
        hit = {f: r.get(f) for f in SELECT_FIELDS}
        # Semantic mode ranks by the reranker score (0-4); others by the
        # search score (cosine for vector, RRF for hybrid).
        reranker = r.get("@search.reranker_score")
        hit["score"] = (
            reranker
            if mode == "hybrid_semantic" and reranker is not None
            else r["@search.score"]
        )
        hit["retrieval_mode"] = mode
        hits.append(hit)
    return hits


async def search_many(
    queries: list[str], companies: list[str], k: int, mode: Mode | None = None
) -> list[dict[str, Any]]:
    """Top-k per query — and, with several companies, per company — deduped
    by chunk id (best score wins), best first. mode defaults to
    Settings.retrieval_mode.

    Per-company top-k stops whichever filing phrases things closest to the
    query (e.g. "Google Cloud" vs "AWS") from crowding out the others.
    """
    if not queries:
        return []
    mode = mode or get_settings().retrieval_mode
    vectors = await embed_queries(queries)
    scopes = [[c] for c in companies] if len(companies) > 1 else [companies]
    batches = await asyncio.gather(
        *(
            _traced_search(q, v, scope, k, mode)
            for q, v in zip(queries, vectors, strict=True)
            for scope in scopes
        )
    )
    best: dict[str, dict[str, Any]] = {}
    for hit in (h for batch in batches for h in batch):
        if hit["id"] not in best or hit["score"] > best[hit["id"]]["score"]:
            best[hit["id"]] = hit
    return sorted(best.values(), key=lambda h: h["score"], reverse=True)


async def _traced_search(
    query: str, vector: list[float], companies: list[str], k: int, mode: Mode
) -> list[dict[str, Any]]:
    """_search, as a Langfuse retriever span: the mode asked for and the mode
    that ran (they differ when the semantic ranker falls back)."""
    with observe(
        "search",
        "retriever",
        input=query,
        metadata={"mode_requested": mode, "companies": companies, "k": k},
    ) as span:
        hits = await _search(query, vector, companies, k, mode)
        if span is not None:
            span.update(
                output=[{"id": h["id"], "score": h["score"]} for h in hits],
                metadata={"mode_used": hits[0]["retrieval_mode"] if hits else None},
            )
    return hits


async def search(query: str, companies: list[str], k: int = 8) -> list[dict[str, Any]]:
    return await search_many([query], companies, k)


async def list_companies() -> list[str]:
    """Companies present in the index (cached for the process lifetime)."""
    global _companies
    if _companies is None:
        _companies = await with_retries("list_companies", _fetch_companies)
    return _companies


async def _fetch_companies() -> list[str]:
    results = await get_async_search_client().search(
        search_text="*", facets=["company,count:100"], top=0
    )
    facets = await results.get_facets() or {}
    return sorted(f["value"] for f in facets.get("company", []))
