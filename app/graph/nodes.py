"""Graph nodes. Each returns a partial state update.

plan -> (retrieve_filings || fetch_news) -> compact -> approve_gate -> write
-> critique -> (plan | END)
"""

import logging
from typing import Any

from langgraph.graph import END

from app.config import get_settings
from app.graph import prompts
from app.graph.llm import complete_text, parse_structured
from app.graph.retrieval import list_companies, search_many
from app.graph.state import (
    Citation,
    Critique,
    DraftReport,
    Evidence,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchState,
)
from app.graph.tools import get_news_tool

log = logging.getLogger(__name__)

MAX_LOOPS = 2  # hard cap on plan passes; an uncapped critic loop burns budget
MAX_SUB_QUESTIONS = 5
COMPACT_TOKEN_BUDGET = 2000
NEWS_DAYS = 7
NEWS_PER_COMPANY = 5


# ---------- plan ----------


def resolve_companies(
    proposed: list[str], requested: list[str], available: list[str]
) -> list[str]:
    """Request filter wins; otherwise the plan's picks. Mapped onto canonical
    index names so the Search filter matches; unknown names are dropped."""
    canonical = {c.lower(): c for c in available}
    chosen = requested or proposed
    resolved = [canonical[c.lower()] for c in chosen if c.lower() in canonical]
    return list(dict.fromkeys(resolved))


async def plan(state: ResearchState) -> dict[str, Any]:
    available = await list_companies()
    follow_up = state.critique if state.loop_count > 0 else None
    result = await parse_structured(
        "plan",
        prompts.PLAN_SYSTEM,
        prompts.plan_user(
            state.query, state.companies, available, state.plan, follow_up
        ),
        ResearchPlan,
    )
    result.companies = resolve_companies(result.companies, state.companies, available)
    result.sub_questions = [q for q in result.sub_questions if q.strip()][
        :MAX_SUB_QUESTIONS
    ] or [state.query]
    log.info(
        "plan (pass %d): %d sub-question(s), companies=%s, news=%s",
        state.loop_count + 1,
        len(result.sub_questions),
        result.companies,
        result.needs_live_news,
    )
    return {"plan": result, "loop_count": state.loop_count + 1}


# ---------- evidence ----------


def evidence_from_hit(hit: dict[str, Any]) -> Evidence:
    return Evidence(
        source_type="filing",
        title=f"{hit['company']} {hit['doc_type']} {hit['period']} p.{hit['page']}",
        snippet=hit["content"],
        reference=hit["id"],
        period=hit["period"],
        company=hit["company"],
        doc_type=hit["doc_type"],
        source_blob=hit["source_blob"],
        chunk_no=hit["chunk_no"],
        page=hit["page"],
    )


async def retrieve_filings(state: ResearchState) -> dict[str, Any]:
    assert state.plan is not None
    hits = await search_many(
        state.plan.sub_questions, state.plan.companies, k=get_settings().retrieval_top_k
    )
    known = {e.reference for e in state.filing_evidence}
    new = [evidence_from_hit(h) for h in hits if h["id"] not in known]
    log.info(
        "retrieve_filings: %d hit(s), %d new, %d total",
        len(hits),
        len(new),
        len(state.filing_evidence) + len(new),
    )
    return {"filing_evidence": state.filing_evidence + new}


async def fetch_news(state: ResearchState) -> dict[str, Any]:
    assert state.plan is not None
    if not state.plan.needs_live_news:
        log.info("fetch_news: skipped (plan does not need live news)")
        return {}
    tool = get_news_tool()
    if tool is None:
        log.warning("fetch_news: no news tool configured; continuing without news")
        return {"degraded": ["news"]}
    targets = state.plan.companies or [state.plan.subject]
    known = {e.reference for e in state.news_evidence}
    new: list[Evidence] = []
    try:
        for company in targets:
            for item in await tool.search_company_news(
                company, days=NEWS_DAYS, max_results=NEWS_PER_COMPANY
            ):
                if item["url"] in known:
                    continue
                known.add(item["url"])
                new.append(
                    Evidence(
                        source_type="news",
                        title=item["title"],
                        snippet=item["snippet"],
                        reference=item["url"],
                        period=item.get("published"),
                        company=company,
                    )
                )
    except Exception:
        # News is supplementary: degrade, never fail the run.
        log.exception("fetch_news: news tool failed; continuing without news")
        return {"degraded": ["news"]}
    log.info("fetch_news: %d new item(s)", len(new))
    return {"news_evidence": state.news_evidence + new}


# ---------- compact ----------


async def compact(state: ResearchState) -> dict[str, Any]:
    assert state.plan is not None
    evidence = state.filing_evidence + state.news_evidence
    if not evidence:
        return {"compacted_context": "(no evidence retrieved)"}
    brief = await complete_text(
        "compact",
        prompts.COMPACT_SYSTEM,
        prompts.compact_user(state.query, state.plan, evidence),
    )
    # Guarantee every reference id survives, even ones the model judged irrelevant.
    dropped = [e.reference for e in evidence if e.reference not in brief]
    if dropped:
        brief += "\n\nOther evidence (not summarised): " + ", ".join(
            f"[{r}]" for r in dropped
        )
    est_tokens = len(brief) // 4
    log.info(
        "compact: %d evidence item(s) -> ~%d tokens (%d refs not summarised)",
        len(evidence),
        est_tokens,
        len(dropped),
    )
    if est_tokens > COMPACT_TOKEN_BUDGET * 1.5:
        log.warning("compact: brief exceeds budget (~%d tokens)", est_tokens)
    return {"compacted_context": brief}


# ---------- approval (auto until Day 9–10 adds interrupt()) ----------


async def approve_gate(state: ResearchState) -> dict[str, Any]:
    # Day 9–10 replaces this with interrupt({...}) once a checkpointer exists;
    # interrupts need one to pause and resume.
    return {"approval": {"approved": True, "mode": "auto", "notes": None}}


# ---------- write ----------


def normalize_reference(ref: str) -> str:
    """Models often copy the "[ref]" brackets from the prompt; strip them."""
    return ref.strip().strip("[]").strip()


def hydrate(draft: DraftReport, evidence: list[Evidence]) -> Report:
    """Turn reference-only citations into full citations; drop any whose
    reference isn't in the evidence (the model can't cite what we didn't find)."""
    by_ref = {e.reference: e for e in evidence}
    sections: list[ReportSection] = []
    for s in draft.sections:
        citations: list[Citation] = []
        for c in s.citations:
            e = by_ref.get(normalize_reference(c.reference))
            if e is None:
                log.warning(
                    "write: dropped citation to unknown reference %r", c.reference
                )
                continue
            citations.append(
                Citation(
                    company=e.company,
                    doc_type=e.doc_type,
                    period=e.period,
                    source_blob=e.source_blob,
                    chunk_no=e.chunk_no,
                    page=e.page,
                    quote=c.quote,
                    source_type=e.source_type,
                    reference=e.reference,
                )
            )
        sections.append(
            ReportSection(heading=s.heading, body=s.body, citations=citations)
        )
    return Report(subject=draft.subject, summary=draft.summary, sections=sections)


async def write(state: ResearchState) -> dict[str, Any]:
    evidence = state.filing_evidence + state.news_evidence
    draft = await parse_structured(
        "write",
        prompts.WRITE_SYSTEM,
        prompts.write_user(
            state.query, state.compacted_context, evidence, state.degraded
        ),
        DraftReport,
    )
    report = hydrate(draft, evidence)
    log.info(
        "write: %d section(s), %d citation(s)",
        len(report.sections),
        sum(len(s.citations) for s in report.sections),
    )
    return {"report": report}


# ---------- critique ----------


def check_citations(report: Report, evidence: list[Evidence]) -> list[str]:
    """Deterministic checks — never trust the LLM alone for these."""
    refs = {e.reference for e in evidence}
    problems = [
        f"Section {s.heading!r} has no citations"
        for s in report.sections
        if not s.citations
    ]
    problems += [
        f"Section {s.heading!r} cites unknown reference {c.reference!r}"
        for s in report.sections
        for c in s.citations
        if c.reference not in refs
    ]
    if not report.sections:
        problems.append("Report has no sections")
    return problems


async def critique(state: ResearchState) -> dict[str, Any]:
    assert state.plan is not None and state.report is not None
    evidence = state.filing_evidence + state.news_evidence
    hard_problems = check_citations(state.report, evidence)
    review = await parse_structured(
        "critique",
        prompts.CRITIQUE_SYSTEM,
        prompts.critique_user(state.query, state.plan, state.report, state.degraded),
        Critique,
    )
    result = Critique(
        is_complete=review.is_complete and not hard_problems,
        missing=review.missing,
        citation_problems=list(dict.fromkeys(hard_problems + review.citation_problems)),
    )
    log.info(
        "critique (pass %d): complete=%s, %d gap(s), %d citation problem(s)",
        state.loop_count,
        result.is_complete,
        len(result.missing),
        len(result.citation_problems),
    )
    return {"critique": result}


def route_after_critique(state: ResearchState) -> str:
    if (
        state.critique
        and not state.critique.is_complete
        and state.loop_count < MAX_LOOPS
    ):
        return "plan"
    return END
