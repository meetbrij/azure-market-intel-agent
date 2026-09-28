"""Web content is untrusted: strip markup, collapse whitespace, cap length.

Used by the MCP server on everything it returns, and again by the graph's
client (the server may run remotely in Phase 4, so the client can't assume
it was sanitised).
"""

import html
import re
from datetime import UTC
from email.utils import parsedate_to_datetime

MAX_SNIPPET_CHARS = 500
MAX_TITLE_CHARS = 200

_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(value: object, limit: int) -> str:
    # Decode entities *before* stripping tags, and repeat until nothing
    # changes: otherwise "&lt;/tag&gt;" (or a double-encoded form) survives
    # the strip and turns back into a live tag, e.g. a fake fence close.
    text = str(value or "")
    for _ in range(5):
        before = text
        text = html.unescape(text)
        text = _SCRIPT_STYLE.sub(" ", text)
        text = _TAG.sub(" ", text)
        if text == before:
            break
    text = _WS.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def iso_date(value: object) -> str | None:
    """Tavily dates are RFC 2822 ("Tue, 23 Sep 2026 10:00:00 GMT") or ISO."""
    if not value:
        return None
    raw = str(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    try:
        return parsedate_to_datetime(raw).astimezone(UTC).date().isoformat()
    except (TypeError, ValueError):
        return None
