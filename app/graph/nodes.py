"""Graph nodes: retrieve grounded context, then write a cited Report."""

import logging
from typing import Any

from app.azure_clients import get_async_aoai
from app.config import get_settings
from app.graph.retrieval import search
from app.graph.state import Report, ResearchState

log = logging.getLogger(__name__)

TOP_K = 8

SYSTEM_PROMPT = """\
You are a financial research analyst writing a short report from SEC 10-K \
excerpts.

Rules:
- Use ONLY the numbered context blocks provided. No outside knowledge.
- Never invent, estimate or compute numbers that are not stated in the context.
- Every section must carry at least one citation.
- For each citation, copy company, doc_type, period, source_blob, chunk_no and \
page exactly from the header of the context block you are citing, and quote a \
short supporting snippet (at most 25 words) verbatim from that block.
- If the context does not answer the question, or only partly does, say so \
explicitly in the summary and in the relevant section rather than guessing.
"""


def format_context(hits: list[dict[str, Any]]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        header = (
            f"[{i}] company={h['company']} doc_type={h['doc_type']} "
            f"period={h['period']} source_blob={h['source_blob']} "
            f"chunk_no={h['chunk_no']} page={h['page']}"
        )
        blocks.append(f"{header}\n{h['content']}")
    return "\n\n".join(blocks) if blocks else "(no context retrieved)"


def drop_ungrounded(report: Report, hits: list[dict[str, Any]]) -> Report:
    """Remove citations that don't point at a chunk we actually retrieved."""
    known = {(h["source_blob"], h["chunk_no"]) for h in hits}
    for section in report.sections:
        kept = [c for c in section.citations if (c.source_blob, c.chunk_no) in known]
        if len(kept) < len(section.citations):
            log.warning(
                "Dropped %d ungrounded citation(s) in section %r",
                len(section.citations) - len(kept),
                section.heading,
            )
        section.citations = kept
    return report


async def retrieve(state: ResearchState) -> dict[str, Any]:
    hits = await search(state.query, state.companies, k=TOP_K)
    log.info("Retrieved %d hit(s) for %r", len(hits), state.query)
    return {"retrieved": hits}


async def write(state: ResearchState) -> dict[str, Any]:
    user = f"Question: {state.query}\n\nContext:\n{format_context(state.retrieved)}"
    # No temperature / max_tokens: GPT-5-family reasoning models reject them.
    completion = await get_async_aoai().chat.completions.parse(
        model=get_settings().azure_openai_chat_deployment,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        response_format=Report,
    )
    message = completion.choices[0].message
    if message.refusal:
        raise RuntimeError(f"Model refused: {message.refusal}")
    if message.parsed is None:
        raise RuntimeError("Model returned no parsable Report")
    return {"report": drop_ungrounded(message.parsed, state.retrieved)}
