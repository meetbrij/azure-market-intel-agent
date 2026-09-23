"""Sanity check: vector query against the index, print top hits.

uv run python -m ingestion.verify ["query text"]
"""

import sys

from azure.search.documents.models import VectorizedQuery

from app.azure_clients import get_search_client
from ingestion.ingest import embed


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "operating income growth"
    vq = VectorizedQuery(
        vector=embed([query])[0], k_nearest_neighbors=3, fields="content_vector"
    )
    hits = get_search_client().search(
        search_text=None,
        vector_queries=[vq],
        select=["company", "doc_type", "period", "page", "chunk_no", "content"],
        top=3,
    )
    print(f"Query: {query!r}\n")
    for i, h in enumerate(hits, 1):
        print(
            f"[{i}] score={h['@search.score']:.4f}  {h['company']} {h['doc_type']} "
            f"{h['period']}  page {h['page']}, chunk {h['chunk_no']}"
        )
        print("    " + " ".join(h["content"].split())[:400] + "…\n")


if __name__ == "__main__":
    main()
