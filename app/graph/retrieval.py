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


async def embed_query(text: str) -> list[float]:
    resp = await get_async_aoai().embeddings.create(
        model=get_settings().azure_openai_embed_deployment, input=[text]
    )
    return resp.data[0].embedding


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


async def search(query: str, companies: list[str], k: int = 8) -> list[dict[str, Any]]:
    """Top-k chunks; with several companies, top-k per company.

    A single top-k over several companies lets whichever filing phrases things
    closest to the query crowd out the rest (e.g. "Google Cloud" vs "AWS"), so
    comparisons would silently lose a side.
    """
    vector = await embed_query(query)
    if len(companies) <= 1:
        return await _vector_search(vector, companies, k)
    per_company = await asyncio.gather(
        *(_vector_search(vector, [c], k) for c in companies)
    )
    return [hit for hits in per_company for hit in hits]
