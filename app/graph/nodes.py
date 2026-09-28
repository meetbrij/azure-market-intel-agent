"""Graph nodes. Each returns a partial state update.

plan -> (retrieve_filings || fetch_news) -> compact -> approve_gate -> write
-> critique -> (plan | END)
"""

import asyncio
import logging
import re
from collections.abc import Awaitable
from typing import Any

from langgraph.graph import END
from langgraph.types import interrupt

from app.config import get_settings
from app.graph import prompts
from app.graph.llm import (
    complete_text,
    is_content_filtered,
    parse_structured,
    recording_calls,
)
from app.graph.retrieval import list_companies, search_many
from app.graph.state import (
    Citation,
    Critique,
    DraftReport,
    Evidence,
    InjectionScreen,
    LlmCall,
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
DATA_GAP_LABELS = {
    "news": "live news unavailable",
    "filings": "filing search unavailable",
    "news_screened": "some news items were withheld (possible prompt injection)",
}


async def tracked[T](call: Awaitable[T]) -> tuple[T, list[LlmCall]]:
    """Await an LLM call and return it with the calls it made (for provenance)."""
    with recording_calls() as calls:
        return await call, calls


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


def rejected(state: ResearchState) -> bool:
    return state.approval is not None and not state.approval.get("approved", False)


async def plan(state: ResearchState) -> dict[str, Any]:
    try:
        available = await list_companies()
    except Exception:
        # Degrade, don't fail: keep the request's own filter. If Search is
        # really down, retrieve_filings records the gap.
        log.exception("plan: could not list indexed companies; using the request's")
        available = list(state.companies)
    # A reviewer rejection takes precedence over the critique: revise the whole
    # plan per their notes rather than narrowing to the critic's gaps.
    reviewer_notes = (
        ((state.approval or {}).get("notes") or "") if rejected(state) else None
    )
    follow_up = state.critique if state.loop_count > 0 and not rejected(state) else None
    result, calls = await tracked(
        parse_structured(
            "plan",
            prompts.PLAN_SYSTEM,
            prompts.plan_user(
                state.query,
                state.companies,
                available,
                state.plan,
                follow_up,
                reviewer_notes,
            ),
            ResearchPlan,
        )
    )
    requested = state.companies
    if rejected(state) and requested:
        # A reviewer may narrow the request's filter ("Amazon only"), never widen it.
        wanted = {c.lower() for c in requested}
        requested = [c for c in result.companies if c.lower() in wanted] or requested
    result.companies = resolve_companies(result.companies, requested, available)
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
    update: dict[str, Any] = {
        "plan": result,
        "loop_count": state.loop_count + 1,
        "llm_calls": calls,
    }
    if rejected(state):
        # The reviewer turned the previous plan down: its evidence (e.g. for
        # companies they asked to drop) must not reach the writer. A critic
        # loop, by contrast, keeps and extends what it already found.
        update |= {"filing_evidence": [], "news_evidence": []}
    return update


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
    try:
        async with asyncio.timeout(get_settings().node_timeout_s):
            hits = await search_many(
                state.plan.sub_questions,
                state.plan.companies,
                k=get_settings().retrieval_top_k,
            )
    except Exception:  # includes TimeoutError
        # Retries are exhausted: degrade rather than fail; the report says so.
        log.exception("retrieve_filings: search failed; continuing without new filings")
        return {"degraded": ["filings"]}
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
    update: dict[str, Any] = {}
    if new and get_settings().news_screen_enabled:
        try:
            async with asyncio.timeout(get_settings().node_timeout_s):
                new, flagged, calls = await screen_news(new)
        except Exception:
            # Fail closed: news that couldn't be screened never reaches a prompt.
            log.exception("fetch_news: injection screen failed; dropping all news")
            return {"degraded": ["news"]}
        update["llm_calls"] = calls
        if flagged:
            update["screened_out"] = flagged
            update["degraded"] = ["news_screened"]
    log.info("fetch_news: %d new item(s)", len(new))
    return update | {"news_evidence": state.news_evidence + new}


PROMPT_SHIELD_REASON = "blocked by Azure OpenAI Prompt Shields (jailbreak detected)"


async def _screen_batch(items: list[Evidence]) -> tuple[dict[int, str], list[LlmCall]]:
    """{item number: reason} for the items the classifier flags."""
    verdict, calls = await tracked(
        parse_structured(
            "screen",
            prompts.SCREEN_SYSTEM,
            prompts.screen_user([(e.title, e.snippet) for e in items]),
            InjectionScreen,
        )
    )
    reasons = {f.item: f.reason for f in verdict.flagged if 1 <= f.item <= len(items)}
    return reasons, calls


async def screen_news(
    items: list[Evidence],
) -> tuple[list[Evidence], list[dict[str, str]], list[LlmCall]]:
    """Drop items flagged as instruction-like (prompt injection). Two layers:
    our classifier call, and Azure's own Prompt Shields, which refuse a prompt
    that carries a jailbreak. A refused batch is screened item by item, and an
    item Azure refuses is flagged. Returns (kept, flagged records, calls);
    every flag is logged."""
    try:
        reasons, calls = await _screen_batch(items)
    except Exception as exc:
        if not is_content_filtered(exc):
            raise
        log.warning("fetch_news: Azure refused the screening batch; screening items one by one")
        reasons, calls = {}, []
        for i, item in enumerate(items, 1):
            try:
                one, one_calls = await _screen_batch([item])
            except Exception as item_exc:
                if not is_content_filtered(item_exc):
                    raise
                reasons[i] = PROMPT_SHIELD_REASON
                continue
            calls += one_calls
            if one:
                reasons[i] = next(iter(one.values()))
    kept, flagged = [], []
    for i, e in enumerate(items, 1):
        if i not in reasons:
            kept.append(e)
            continue
        log.warning(
            "fetch_news: screen flagged %s (%s): %s", e.reference, e.company, reasons[i]
        )
        flagged.append(
            {"url": e.reference, "company": e.company or "", "reason": reasons[i]}
        )
    return kept, flagged, calls


# ---------- compact ----------


async def compact(state: ResearchState) -> dict[str, Any]:
    assert state.plan is not None
    evidence = state.filing_evidence + state.news_evidence
    if not evidence:
        return {"compacted_context": "(no evidence retrieved)"}
    brief, calls = await tracked(
        complete_text(
            "compact",
            prompts.COMPACT_SYSTEM,
            prompts.compact_user(state.query, state.plan, evidence),
        )
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
    return {"compacted_context": brief, "llm_calls": calls}


# ---------- approval (human in the loop) ----------


def approval_request(state: ResearchState) -> dict[str, Any]:
    """The interrupt payload: what a reviewer needs to approve the plan.
    JSON-safe, since it is exposed on GET /research/{job_id}."""
    assert state.plan is not None
    return {
        "pass": state.loop_count,
        # Rejecting on the final pass ends the run (D-27: rejections count
        # toward MAX_LOOPS), so the reviewer is told up front.
        "final_pass": state.loop_count >= MAX_LOOPS,
        "subject": state.plan.subject,
        "companies": state.plan.companies,
        "sub_questions": state.plan.sub_questions,
        "needs_live_news": state.plan.needs_live_news,
        "evidence_counts": {
            "filings": len(state.filing_evidence),
            "news": len(state.news_evidence),
        },
        "degraded": state.degraded,
    }


async def approve_gate(state: ResearchState) -> dict[str, Any]:
    if not get_settings().approval_required:
        return {"approval": {"approved": True, "mode": "auto", "notes": None}}
    # Pauses the run here; the checkpointer persists state until someone calls
    # POST /research/{id}/resume. On resume this node re-runs from the top and
    # interrupt() returns the resume payload, so nothing before it may have
    # side effects.
    decision = interrupt(approval_request(state))
    if not isinstance(decision, dict):
        decision = {"approved": bool(decision)}
    approved = bool(decision.get("approved", False))
    notes = decision.get("notes") or None
    log.info("approve_gate: approved=%s notes=%r", approved, notes)
    update: dict[str, Any] = {
        "approval": {
            "approved": approved,
            "mode": "human",
            "notes": notes,
            # Who decided (from their Entra token, via POST /resume).
            "reviewer": decision.get("reviewer"),
        }
    }
    if not approved and state.loop_count >= MAX_LOOPS:
        log.warning("approve_gate: rejected on the final pass; ending the run")
        if state.report is None:
            update["error"] = "plan rejected on the final pass (loop cap reached)"
    return update


def route_after_approval(state: ResearchState) -> str:
    if not rejected(state):
        return "write"
    # Same cap as the critic loop. An earlier pass's report, if any, stands.
    return "plan" if state.loop_count < MAX_LOOPS else END


# ---------- write ----------


def normalize_reference(ref: str) -> str:
    """Models often copy the "[ref]" brackets from the prompt; strip them."""
    return ref.strip().strip("[]").strip()


_WORD = re.compile(r"\w+")
_ELLIPSIS = re.compile(r"\.\.\.|…")


def _words(text: str) -> str:
    """Case, punctuation and whitespace folded away, for quote matching."""
    return " ".join(_WORD.findall(text.casefold()))


def quote_in_snippet(quote: str, snippet: str) -> bool:
    """The quote's text appears in the snippet, in order. An ellipsis in the
    quote may skip text; everything else must match word for word."""
    haystack = f" {_words(snippet)} "
    pos = 0
    for fragment in _ELLIPSIS.split(quote):
        needle = _words(fragment)
        if not needle:
            continue
        found = haystack.find(f" {needle} ", pos)
        if found < 0:
            return False
        pos = found + len(needle) + 1
    return True


def hydrate(draft: DraftReport, evidence: list[Evidence]) -> Report:
    """Turn reference-only citations into full citations. Drop any whose
    reference isn't in the evidence (the model can't cite what we didn't find)
    or whose quote isn't in that evidence's text (nor misquote what we did)."""
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
            if not quote_in_snippet(c.quote, e.snippet):
                log.warning(
                    "write: dropped citation to %r: quote not in the source: %r",
                    e.reference,
                    c.quote,
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
    draft, calls = await tracked(
        parse_structured(
            "write",
            prompts.WRITE_SYSTEM,
            prompts.write_user(
                state.query, state.compacted_context, evidence, state.degraded
            ),
            DraftReport,
        )
    )
    report = hydrate(draft, evidence)
    report.data_gaps = [
        DATA_GAP_LABELS.get(d, f"{d} unavailable") for d in state.degraded
    ]
    log.info(
        "write: %d section(s), %d citation(s)",
        len(report.sections),
        sum(len(s.citations) for s in report.sections),
    )
    return {"report": report, "llm_calls": calls}


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
    review, calls = await tracked(
        parse_structured(
            "critique",
            prompts.CRITIQUE_SYSTEM,
            prompts.critique_user(
                state.query, state.plan, state.report, state.degraded
            ),
            Critique,
        )
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
    return {"critique": result, "llm_calls": calls}


def route_after_critique(state: ResearchState) -> str:
    if (
        state.critique
        and not state.critique.is_complete
        and state.loop_count < MAX_LOOPS
    ):
        return "plan"
    return END
