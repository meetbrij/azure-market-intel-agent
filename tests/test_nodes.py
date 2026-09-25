"""One test group per node, with the LLM, Search and news tool mocked."""

from typing import Any

import pytest
from langgraph.graph import END

from app.graph import nodes
from app.graph.state import Critique, Evidence, ResearchPlan, ResearchState
from app.graph.tools import NewsItem, set_news_tool
from tests.conftest import (
    DEFAULT_PLAN,
    HIT,
    GraphDeps,
    make_citation,
    make_draft,
    make_report,
)

FILING = nodes.evidence_from_hit(HIT)
NEWS_URL = "https://example.com/aws-news"


def state(**overrides: Any) -> ResearchState:
    base: dict[str, Any] = {"query": "How did Amazon's operating income change?"}
    base.update(overrides)
    return ResearchState.model_validate(base)


class FakeNews:
    def __init__(self, items: list[NewsItem] | None = None, fail: bool = False):
        self.items = items or []
        self.fail = fail
        self.calls: list[str] = []

    async def search_company_news(
        self, company: str, days: int = 7, max_results: int = 5
    ) -> list[NewsItem]:
        self.calls.append(company)
        if self.fail:
            raise ConnectionError("news down")
        return self.items


# ---------- plan ----------


async def test_plan_first_pass_increments_loop_and_resolves_companies(
    graph_deps: GraphDeps,
) -> None:
    graph_deps.llm.responses["plan"] = DEFAULT_PLAN.model_copy(
        update={
            "companies": ["amazon", "Netflix"],
            "sub_questions": [f"q{i}" for i in range(8)],
        }
    )

    update = await nodes.plan(state())

    assert update["loop_count"] == 1
    assert update["plan"].companies == ["Amazon"]  # canonical; unknown dropped
    assert len(update["plan"].sub_questions) == nodes.MAX_SUB_QUESTIONS


async def test_plan_request_companies_override_model(graph_deps: GraphDeps) -> None:
    update = await nodes.plan(state(companies=["Alphabet", "Microsoft"]))
    assert update["plan"].companies == ["Alphabet", "Microsoft"]


async def test_plan_follow_up_pass_receives_critique_gaps(
    graph_deps: GraphDeps,
) -> None:
    critique = Critique(is_complete=False, missing=["Amazon AWS operating margin"])

    await nodes.plan(state(plan=DEFAULT_PLAN, critique=critique, loop_count=1))

    label, user = graph_deps.llm.calls[-1]
    assert label == "plan"
    assert "PREVIOUS CRITIQUE" in user
    assert "Amazon AWS operating margin" in user


# ---------- retrieve_filings ----------


async def test_retrieve_filings_builds_evidence_and_dedupes(
    graph_deps: GraphDeps,
) -> None:
    other = {**HIT, "id": "amzn-annual-report-10k-158", "chunk_no": 158}
    graph_deps.search.return_value = [HIT, other]

    update = await nodes.retrieve_filings(
        state(plan=DEFAULT_PLAN, filing_evidence=[FILING])
    )

    refs = [e.reference for e in update["filing_evidence"]]
    assert refs == [HIT["id"], other["id"]]  # existing kept, no duplicate
    new = update["filing_evidence"][1]
    assert (new.company, new.page, new.chunk_no) == ("Amazon", 27, 158)
    graph_deps.search.assert_awaited_once_with(
        DEFAULT_PLAN.sub_questions, ["Amazon"], k=4
    )


# ---------- fetch_news ----------


async def test_fetch_news_skipped_when_plan_says_no(graph_deps: GraphDeps) -> None:
    tool = FakeNews()
    set_news_tool(tool)
    assert await nodes.fetch_news(state(plan=DEFAULT_PLAN)) == {}
    assert tool.calls == []


async def test_fetch_news_without_tool_degrades(graph_deps: GraphDeps) -> None:
    plan = DEFAULT_PLAN.model_copy(update={"needs_live_news": True})
    assert await nodes.fetch_news(state(plan=plan)) == {"degraded": ["news"]}


async def test_fetch_news_failure_degrades_instead_of_raising(
    graph_deps: GraphDeps,
) -> None:
    set_news_tool(FakeNews(fail=True))
    plan = DEFAULT_PLAN.model_copy(update={"needs_live_news": True})
    assert await nodes.fetch_news(state(plan=plan)) == {"degraded": ["news"]}


async def test_fetch_news_returns_url_referenced_evidence(
    graph_deps: GraphDeps,
) -> None:
    item: NewsItem = {
        "title": "AWS news",
        "url": NEWS_URL,
        "published": "2026-09-20",
        "snippet": "AWS announced ...",
    }
    set_news_tool(FakeNews(items=[item, item]))
    plan = DEFAULT_PLAN.model_copy(update={"needs_live_news": True})

    update = await nodes.fetch_news(state(plan=plan))

    [news] = update["news_evidence"]  # duplicate URL dropped
    assert (news.source_type, news.reference, news.period) == (
        "news",
        NEWS_URL,
        "2026-09-20",
    )


# ---------- compact ----------


async def test_compact_keeps_every_reference(graph_deps: GraphDeps) -> None:
    other = FILING.model_copy(update={"reference": "amzn-annual-report-10k-999"})
    graph_deps.llm.responses["compact"] = f"Brief citing only [{FILING.reference}]"

    update = await nodes.compact(
        state(plan=DEFAULT_PLAN, filing_evidence=[FILING, other])
    )

    brief = update["compacted_context"]
    assert FILING.reference in brief
    assert "amzn-annual-report-10k-999" in brief  # appended, not lost


async def test_compact_with_no_evidence_skips_llm(graph_deps: GraphDeps) -> None:
    update = await nodes.compact(state(plan=DEFAULT_PLAN))
    assert update == {"compacted_context": "(no evidence retrieved)"}
    assert graph_deps.llm.calls == []


# ---------- approve_gate ----------


async def test_approve_gate_auto_approves_when_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("APPROVAL_REQUIRED", "false")
    get_settings.cache_clear()
    update = await nodes.approve_gate(state(plan=DEFAULT_PLAN))
    assert update["approval"] == {"approved": True, "mode": "auto", "notes": None}


def test_approval_request_is_small_and_json_safe() -> None:
    import json

    payload = nodes.approval_request(
        state(plan=DEFAULT_PLAN, filing_evidence=[FILING], loop_count=1)
    )
    assert payload["evidence_counts"] == {"filings": 1, "news": 0}
    assert FILING.snippet not in json.dumps(payload)  # counts, not documents


def test_route_after_approval() -> None:
    approved = {"approved": True, "mode": "human", "notes": None}
    rejected = {"approved": False, "mode": "human", "notes": "narrow it"}
    assert nodes.route_after_approval(state(approval=approved)) == "write"
    assert nodes.route_after_approval(state(approval=rejected)) == "plan"


async def test_plan_after_rejection_uses_reviewer_notes_not_critique(
    graph_deps: GraphDeps,
) -> None:
    rejected = {"approved": False, "mode": "human", "notes": "Only AWS, please"}
    critique = Critique(is_complete=False, missing=["stale gap"])

    await nodes.plan(
        state(plan=DEFAULT_PLAN, approval=rejected, critique=critique, loop_count=1)
    )

    user = graph_deps.llm.calls[-1][1]
    assert "Only AWS, please" in user
    assert "stale gap" not in user


# ---------- write ----------


async def test_write_fills_citation_metadata_and_drops_unknown_refs(
    graph_deps: GraphDeps,
) -> None:
    news = Evidence(
        source_type="news", title="AWS news", snippet="...", reference=NEWS_URL
    )
    graph_deps.llm.responses["write"] = make_draft(
        str(HIT["id"]), NEWS_URL, "made-up-ref"
    )

    update = await nodes.write(
        state(plan=DEFAULT_PLAN, filing_evidence=[FILING], news_evidence=[news])
    )

    citations = update["report"].sections[0].citations
    assert [c.reference for c in citations] == [HIT["id"], NEWS_URL]
    filing, news_cite = citations
    assert (filing.company, filing.page, filing.source_blob) == (
        "Amazon",
        27,
        "amzn_annual_report_10k.pdf",
    )
    assert news_cite.source_type == "news"


async def test_write_accepts_bracketed_references(graph_deps: GraphDeps) -> None:
    graph_deps.llm.responses["write"] = make_draft(f"[{HIT['id']}]", f" {HIT['id']} ")

    update = await nodes.write(state(plan=DEFAULT_PLAN, filing_evidence=[FILING]))

    refs = [c.reference for c in update["report"].sections[0].citations]
    assert refs == [HIT["id"], HIT["id"]]


async def test_write_tells_model_about_degraded_sources(
    graph_deps: GraphDeps,
) -> None:
    await nodes.write(
        state(plan=DEFAULT_PLAN, filing_evidence=[FILING], degraded=["news"])
    )
    assert "UNAVAILABLE SOURCES: news" in graph_deps.llm.calls[-1][1]


# ---------- critique ----------


async def test_critique_python_checks_override_llm(graph_deps: GraphDeps) -> None:
    graph_deps.llm.responses["critique"] = Critique(is_complete=True)
    report = make_report(make_citation(reference="not-in-evidence"))

    update = await nodes.critique(
        state(plan=DEFAULT_PLAN, filing_evidence=[FILING], report=report, loop_count=1)
    )

    result = update["critique"]
    assert result.is_complete is False
    assert any("not-in-evidence" in p for p in result.citation_problems)


async def test_critique_flags_uncited_sections(graph_deps: GraphDeps) -> None:
    update = await nodes.critique(
        state(plan=DEFAULT_PLAN, filing_evidence=[FILING], report=make_report())
    )
    assert update["critique"].is_complete is False
    assert "has no citations" in update["critique"].citation_problems[0]


async def test_critique_told_about_unavailable_sources(
    graph_deps: GraphDeps,
) -> None:
    await nodes.critique(
        state(
            plan=DEFAULT_PLAN,
            filing_evidence=[FILING],
            report=make_report(make_citation()),
            degraded=["news"],
        )
    )
    assert "UNAVAILABLE SOURCES: news" in graph_deps.llm.calls[-1][1]


async def test_critique_passes_clean_report(graph_deps: GraphDeps) -> None:
    update = await nodes.critique(
        state(
            plan=DEFAULT_PLAN,
            filing_evidence=[FILING],
            report=make_report(make_citation()),
        )
    )
    assert update["critique"] == Critique(is_complete=True)


# ---------- routing ----------


def test_route_loops_back_while_incomplete_and_under_cap() -> None:
    gaps = Critique(is_complete=False, missing=["x"])
    assert nodes.route_after_critique(state(critique=gaps, loop_count=1)) == "plan"


def test_route_stops_at_loop_cap() -> None:
    gaps = Critique(is_complete=False, missing=["x"])
    s = state(critique=gaps, loop_count=nodes.MAX_LOOPS)
    assert nodes.route_after_critique(s) == END


def test_route_stops_when_complete() -> None:
    done = Critique(is_complete=True)
    assert nodes.route_after_critique(state(critique=done, loop_count=1)) == END


def test_plan_model_rejects_missing_fields() -> None:
    try:
        ResearchPlan.model_validate({"subject": "x"})
    except ValueError:
        return
    raise AssertionError("ResearchPlan accepted a plan without sub-questions")
