"""AI Search vector query wrapper."""

import asyncio
from typing import Any

from azure.search.documents.models import VectorizedQuery

from app.azure_clients import get_async_aoai, get_async_search_client
from app.config import get_settings

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
    resp = await get_async_aoai().embeddings.create(
        model=get_settings().azure_openai_embed_deployment, input=texts
    )
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def company_filter(companies: list[str]) -> str | None:
    if not companies:
        return None
    # Canonical names never contain commas or quotes (see ingestion/parse.py).
    names = ",".join(c.replace("'", "''") for c in companies)
    return f"search.in(company, '{names}', ',')"


async def _vector_search(
    vector: list[float], companies: list[str], k: int
) -> list[dict[str, Any]]:
    vq = VectorizedQuery(vector=vector, k_nearest_neighbors=k, fields="content_vector")
    results = await get_async_search_client().search(
        search_text=None,
        vector_queries=[vq],
        filter=company_filter(companies),
        select=SELECT_FIELDS,
        top=k,
    )
    hits: list[dict[str, Any]] = []
    async for r in results:
        hit = {f: r.get(f) for f in SELECT_FIELDS}
        hit["score"] = r["@search.score"]
        hits.append(hit)
    return hits


async def search_many(
    queries: list[str], companies: list[str], k: int
) -> list[dict[str, Any]]:
    """Top-k per query — and, with several companies, per company — deduped
    by chunk id (best score wins), best first.

    Per-company top-k stops whichever filing phrases things closest to the
    query (e.g. "Google Cloud" vs "AWS") from crowding out the others.
    """
    if not queries:
        return []
    vectors = await embed_queries(queries)
    scopes = [[c] for c in companies] if len(companies) > 1 else [companies]
    batches = await asyncio.gather(
        *(_vector_search(v, scope, k) for v in vectors for scope in scopes)
    )
    best: dict[str, dict[str, Any]] = {}
    for hit in (h for batch in batches for h in batch):
        if hit["id"] not in best or hit["score"] > best[hit["id"]]["score"]:
            best[hit["id"]] = hit
    return sorted(best.values(), key=lambda h: h["score"], reverse=True)


async def search(query: str, companies: list[str], k: int = 8) -> list[dict[str, Any]]:
    return await search_many([query], companies, k)


async def list_companies() -> list[str]:
    """Companies present in the index (cached for the process lifetime)."""
    global _companies
    if _companies is None:
        results = await get_async_search_client().search(
            search_text="*", facets=["company,count:100"], top=0
        )
        facets = await results.get_facets() or {}
        _companies = sorted(f["value"] for f in facets.get("company", []))
    return _companies
