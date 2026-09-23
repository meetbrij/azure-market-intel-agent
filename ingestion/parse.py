"""Blob PDF -> page-numbered text blocks plus filename metadata."""

import io
import re
from dataclasses import dataclass

from azure.storage.blob import ContainerClient
from pypdf import PdfReader

UNKNOWN = "unknown"

# Filename: "{COMPANY} {DOC_TYPE} {PERIOD}.pdf", e.g. "AMAZON.COM, INC. 10-K 2025-12-31.pdf"
_FILENAME_RE = re.compile(
    r"^(?P<company>.+?)\s+(?P<doc_type>10-K|10-Q)\s+(?P<period>\d{4}-\d{2}-\d{2})\.pdf$",
    re.IGNORECASE,
)

# Legal names -> (canonical company, ticker). Canonical names are what the API
# filters on ("Amazon", "Alphabet"). Unlisted issuers keep their raw name.
_KNOWN_ISSUERS: dict[str, tuple[str, str]] = {
    "amazon": ("Amazon", "AMZN"),
    "alphabet": ("Alphabet", "GOOGL"),
    "microsoft": ("Microsoft", "MSFT"),
    "apple": ("Apple", "AAPL"),
    "meta platforms": ("Meta", "META"),
    "nvidia": ("NVIDIA", "NVDA"),
}


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


def parse_filename(blob_name: str) -> FilingMeta:
    m = _FILENAME_RE.match(blob_name.rsplit("/", 1)[-1])
    if not m:
        return FilingMeta(UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN, blob_name)
    raw_company = m["company"].strip()
    company, ticker = raw_company, UNKNOWN
    for prefix, (canonical, tick) in _KNOWN_ISSUERS.items():
        if raw_company.lower().startswith(prefix):
            company, ticker = canonical, tick
            break
    return FilingMeta(company, ticker, m["doc_type"].upper(), m["period"], blob_name)


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
