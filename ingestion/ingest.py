"""CLI: parse filings from blob, chunk, embed, upsert to AI Search.

uv run python -m ingestion.ingest [--recreate]
"""

import argparse
import logging
import re
import sys
from collections.abc import Iterator, Sequence
from typing import Any

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from openai import RateLimitError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.azure_clients import (
    get_aoai,
    get_container_client,
    get_search_client,
    get_search_index_client,
)
from app.config import get_settings
from ingestion.chunk import chunk_pages
from ingestion.index_schema import build_index
from ingestion.parse import extract_pages, parse_filename

log = logging.getLogger("ingest")

FREE_TIER_STORAGE_BYTES = 50 * 1024 * 1024
MIN_EXPECTED_PAGES = 10


class StorageQuotaExceeded(RuntimeError):
    pass


def batched[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def doc_key(blob_name: str) -> str:
    # AI Search keys allow letters, digits, '_', '-', '='.
    return (
        re.sub(r"[^A-Za-z0-9]+", "-", blob_name.removesuffix(".pdf")).strip("-").lower()
    )


@retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_random_exponential(min=2, max=60),
    stop=stop_after_attempt(8),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def embed(texts: Sequence[str]) -> list[list[float]]:
    resp = get_aoai().embeddings.create(
        model=get_settings().azure_openai_embed_deployment, input=list(texts)
    )
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def ensure_index(recreate: bool) -> None:
    s = get_settings()
    idx = get_search_index_client()
    if recreate:
        try:
            idx.delete_index(s.azure_search_index)
            log.info("Dropped index %s", s.azure_search_index)
        except ResourceNotFoundError:
            pass
    else:
        try:
            idx.get_index(s.azure_search_index)
            log.info("Index %s exists; upserting into it", s.azure_search_index)
            return
        except ResourceNotFoundError:
            pass
    dims = len(embed(["dimension probe"])[0])
    idx.create_index(build_index(s.azure_search_index, dims))
    log.info("Created index %s (vector dims=%d, HNSW)", s.azure_search_index, dims)


def upload(docs: list[dict[str, Any]]) -> None:
    client = get_search_client()
    for batch in batched(docs, get_settings().upload_batch_size):
        try:
            results = client.merge_or_upload_documents(list(batch))
        except HttpResponseError as e:
            msg = str(e).lower()
            if "quota" in msg or "storage" in msg:
                raise StorageQuotaExceeded(
                    "AI Search storage quota exceeded (Free tier is ~50 MB). "
                    f"Service said: {e.message}"
                ) from e
            raise
        failed = [r for r in results if not r.succeeded]
        if failed:
            f = failed[0]
            raise RuntimeError(
                f"{len(failed)} document(s) rejected, e.g. {f.key}: "
                f"{f.status_code} {f.error_message}"
            )


def ingest_blob(blob_name: str) -> int:
    s = get_settings()
    meta = parse_filename(blob_name)
    pages = extract_pages(get_container_client(), blob_name)
    chunks = chunk_pages(pages, s.chunk_size, s.chunk_overlap)
    if not chunks:
        log.warning("%s: no extractable text (scanned PDF?); skipped", blob_name)
        return 0

    if len(pages) < MIN_EXPECTED_PAGES:
        log.warning(
            "%s: only %d page(s) of text — a full %s is usually 50+ pages. "
            "Is this a partial export?",
            blob_name,
            len(pages),
            meta.doc_type,
        )

    vectors: list[list[float]] = []
    for batch in batched([c.text for c in chunks], s.embed_batch_size):
        vectors.extend(embed(batch))

    key = doc_key(blob_name)
    docs = [
        {
            "id": f"{key}-{c.chunk_no}",
            "content": c.text,
            "content_vector": v,
            "company": meta.company,
            "ticker": meta.ticker,
            "doc_type": meta.doc_type,
            "period": meta.period,
            "source_blob": meta.source_blob,
            "chunk_no": c.chunk_no,
            "page": c.page,
        }
        for c, v in zip(chunks, vectors, strict=True)
    ]
    upload(docs)
    log.info(
        "%-40s company=%s ticker=%s type=%s period=%s pages=%d chunks=%d",
        blob_name,
        meta.company,
        meta.ticker,
        meta.doc_type,
        meta.period,
        len(pages),
        len(docs),
    )
    return len(docs)


def log_storage() -> None:
    s = get_settings()
    stats = get_search_index_client().get_index_statistics(s.azure_search_index)
    size = stats.storage_size + (stats.vector_index_size or 0)
    pct = 100 * size / FREE_TIER_STORAGE_BYTES
    log.info(
        "Index %s: %s docs, %.1f MB storage + vector index (%.0f%% of Free-tier 50 MB; "
        "stats lag a few seconds behind uploads)",
        s.azure_search_index,
        stats.document_count,
        size / 1e6,
        pct,
    )
    if pct > 80:
        log.warning("Approaching the Free-tier storage limit")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recreate", action="store_true", help="drop and recreate the index"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
    )
    for noisy in ("azure", "httpx", "httpx2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    ensure_index(args.recreate)
    blobs = [
        b.name
        for b in get_container_client().list_blobs()
        if b.name.lower().endswith(".pdf")
    ]
    log.info("Found %d PDF(s) in container", len(blobs))

    total = 0
    try:
        for name in blobs:
            total += ingest_blob(name)
    except StorageQuotaExceeded as e:
        log.error("%s — stopped after %d chunks.", e, total)
        return 2
    log.info("Done: %d documents, %d chunks total", len(blobs), total)
    log_storage()
    return 0


if __name__ == "__main__":
    sys.exit(main())
