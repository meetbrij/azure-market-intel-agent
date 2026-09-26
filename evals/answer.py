"""Retrieval + single-shot answer for one golden question.

This measures the retriever, not the full agent graph: one search with the
question as the query, then one grounded answer from exactly those chunks.
"""

import time
from typing import Any

from pydantic import BaseModel

from app.graph.llm import parse_structured, recording_calls
from app.graph.nodes import normalize_reference
from app.graph.retrieval import search_many
from evals.golden import GoldenItem

ANSWER_SYSTEM = """\
You answer questions about SEC 10-K filings using ONLY the numbered context \
blocks provided.

Rules:
- If the context does not contain the answer, set abstained=true and say the \
filings provided do not contain it. Do not guess, and do not substitute a \
related figure (e.g. a larger segment total) for the one asked about.
- Never compute numbers (growth rates, margins, differences) that the context \
does not state; quote stated figures with their units and periods.
- citations: the reference ids (from the block headers) of every block you \
used. Cite nothing when you abstain.
- Keep the answer to a few sentences.
"""


class EvalAnswer(BaseModel):
    answer: str
    abstained: bool
    citations: list[str]


class Sample(BaseModel):
    id: str
    category: str
    answerable: bool
    question: str
    ground_truth: str
    retrieved_ids: list[str]
    retrieved_sources: list[str]
    retrieved_pages: list[tuple[str, int]]
    contexts: list[str]
    answer: str
    abstained: bool
    citations: list[str]
    valid_citations: list[str]
    retrieval_ms: float
    answer_ms: float
    prompt_tokens: int
    completion_tokens: int
    embed_tokens_est: int
    deployments: list[str]


def format_context(hits: list[dict[str, Any]]) -> str:
    return (
        "\n\n".join(
            f"[{h['id']}] {h['company']} {h['doc_type']} {h['period']} p.{h['page']}\n{h['content']}"
            for h in hits
        )
        or "(no context retrieved)"
    )


async def answer_item(item: GoldenItem, k: int) -> Sample:
    t0 = time.perf_counter()
    hits = await search_many([item.question], item.companies, k=k)
    t1 = time.perf_counter()
    with recording_calls() as calls:
        result = await parse_structured(
            "eval_answer",
            ANSWER_SYSTEM,
            f"QUESTION: {item.question}\n\nCONTEXT:\n{format_context(hits)}",
            EvalAnswer,
        )
    t2 = time.perf_counter()
    ids = [h["id"] for h in hits]
    cited = [normalize_reference(c) for c in result.citations]
    return Sample(
        id=item.id,
        category=item.category,
        answerable=item.answerable,
        question=item.question,
        ground_truth=item.ground_truth,
        retrieved_ids=ids,
        retrieved_sources=sorted({h["source_blob"] for h in hits}),
        retrieved_pages=[(h["source_blob"], h["page"]) for h in hits],
        contexts=[h["content"] for h in hits],
        answer=result.answer,
        abstained=result.abstained,
        citations=cited,
        valid_citations=[c for c in cited if c in ids],
        retrieval_ms=(t1 - t0) * 1000,
        answer_ms=(t2 - t1) * 1000,
        prompt_tokens=sum(c.prompt_tokens or 0 for c in calls),
        completion_tokens=sum(c.completion_tokens or 0 for c in calls),
        # The embeddings call's usage isn't surfaced by search_many; ~4 chars/token.
        embed_tokens_est=max(1, len(item.question) // 4),
        deployments=sorted({c.deployment for c in calls}),
    )
