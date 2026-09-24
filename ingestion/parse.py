"""Blob PDF -> page-numbered text blocks plus filing metadata."""

import io
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from azure.storage.blob import ContainerClient
from pypdf import PdfReader

UNKNOWN = "unknown"

# Filename: "{COMPANY} {DOC_TYPE} {PERIOD}.pdf", e.g. "AMAZON.COM, INC. 10-K 2025-12-31.pdf"
_FILENAME_RE = re.compile(
    r"^(?P<company>.+?)\s+(?P<doc_type>10-K)\s+(?P<period>\d{4}-\d{2}-\d{2})\.pdf$",
    re.IGNORECASE,
)

# (canonical company, ticker, lowercase aliases). Canonical names are what the
# API filters on ("Amazon", "Alphabet"). Unlisted issuers keep their raw name.
_KNOWN_ISSUERS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Amazon", "AMZN", ("amazon", "amzn")),
    ("Alphabet", "GOOGL", ("alphabet", "googl", "goog")),
    ("Microsoft", "MSFT", ("microsoft", "msft")),
    ("Apple", "AAPL", ("apple", "aapl")),
    ("Meta", "META", ("meta platforms",)),
    ("NVIDIA", "NVDA", ("nvidia", "nvda")),
]

# Cover-page fallbacks for files that don't follow the naming convention.
_COVER_FORM_RE = re.compile(r"FORM\s+(10-K)", re.IGNORECASE)
_COVER_PERIOD_RE = re.compile(
    r"fiscal year ended\s+([A-Z][a-z]+ \d{1,2}, \d{4})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FilingMeta:
    company: str
    ticker: str
    doc_type: str
    period: str
    source_blob: str


@dataclass(frozen=True)
class PageText:
    page: int  # 1-based
    text: str


def _match_issuer(text: str) -> tuple[str, str] | None:
    # Alias must start at a word boundary ("amzn_annual..." matches, "gamazon" doesn't).
    lowered = text.lower()
    for company, ticker, aliases in _KNOWN_ISSUERS:
        if any(re.search(rf"(?<![a-z]){re.escape(a)}", lowered) for a in aliases):
            return company, ticker
    return None


def parse_filename(blob_name: str) -> FilingMeta:
    name = blob_name.rsplit("/", 1)[-1]
    m = _FILENAME_RE.match(name)
    if not m:
        company, ticker = _match_issuer(name) or (UNKNOWN, UNKNOWN)
        return FilingMeta(company, ticker, UNKNOWN, UNKNOWN, blob_name)
    raw_company = m["company"].strip()
    company, ticker = _match_issuer(raw_company) or (raw_company, UNKNOWN)
    return FilingMeta(company, ticker, m["doc_type"].upper(), m["period"], blob_name)


def fill_from_cover(meta: FilingMeta, pages: list[PageText]) -> FilingMeta:
    """Fill fields the filename left unknown from the filing's cover page."""
    cover = " ".join(" ".join(p.text.split()) for p in pages[:2])
    if meta.company == UNKNOWN and (issuer := _match_issuer(cover[:1500])):
        meta = replace(meta, company=issuer[0], ticker=issuer[1])
    if meta.doc_type == UNKNOWN and (m := _COVER_FORM_RE.search(cover)):
        meta = replace(meta, doc_type=m[1].upper())
    if meta.period == UNKNOWN and (m := _COVER_PERIOD_RE.search(cover)):
        try:
            period = (
                datetime.strptime(m[1], "%B %d, %Y")
                .replace(tzinfo=UTC)
                .date()
                .isoformat()
            )
            meta = replace(meta, period=period)
        except ValueError:
            pass
    return meta


def extract_pages(container: ContainerClient, blob_name: str) -> list[PageText]:
    stream = io.BytesIO()
    container.download_blob(blob_name).readinto(stream)
    reader = PdfReader(stream)
    pages: list[PageText] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(PageText(page=i, text=text))
    return pages
