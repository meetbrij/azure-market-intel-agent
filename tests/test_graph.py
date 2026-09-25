"""The compiled graph end to end (LLM and Search mocked), checkpointed in
memory so approval interrupts pause and resume exactly as in the worker."""

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde import _msgpack
from langgraph.types import Command

from app.graph.build import build_graph, thread_config
from app.graph.nodes import MAX_LOOPS
from app.graph.state import Critique, ResearchPlan, ResearchState
from tests.conftest import HIT, GraphDeps

APPROVE = {"approved": True, "notes": None}


async def run(
    decisions: list[dict[str, Any]] | None = None,
    query: str = "Amazon operating income?",
) -> tuple[ResearchState, list[dict[str, Any]]]:
    """Run to completion, answering each approval interrupt in turn (approve
    by default). Returns the final state and every approval request seen."""
    graph = build_graph(InMemorySaver())
    config = thread_config("job-1")
    pending: Any = ResearchState(query=query, companies=["Amazon"])
    requests: list[dict[str, Any]] = []
    while True:
        await graph.ainvoke(pending, config)
        snapshot = await graph.aget_state(config)
        if not snapshot.interrupts:
            return ResearchState.model_validate(snapshot.values), requests
        requests.append(snapshot.interrupts[0].value)
        pending = Command(resume=decisions.pop(0) if decisions else APPROVE)


def test_checkpoints_use_strict_msgpack() -> None:
    assert _msgpack.STRICT_MSGPACK_ENABLED


async def test_pauses_for_approval_then_completes(graph_deps: GraphDeps) -> None:
    final, requests = await run()

    [request] = requests
    assert request["sub_questions"] == ["Amazon operating income 2024 and 2025"]
    assert request["evidence_counts"] == {"filings": 1, "news": 0}
    assert graph_deps.llm.labels() == ["plan", "compact", "write", "critique"]
    assert final.approval == {"approved": True, "mode": "human", "notes": None}
    assert final.report is not None
    assert final.report.sections[0].citations[0].page == 27


async def test_nothing_is_written_before_approval(graph_deps: GraphDeps) -> None:
    graph = build_graph(InMemorySaver())
    config = thread_config("job-1")
    await graph.ainvoke(ResearchState(query="q", companies=["Amazon"]), config)

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("approve_gate",)
    assert snapshot.values.get("report") is None
    assert "write" not in graph_deps.llm.labels()


async def test_rejection_with_notes_replans(graph_deps: GraphDeps) -> None:
    graph_deps.llm.responses["plan"] = [
        ResearchPlan(
            subject="s",
            sub_questions=["first"],
            companies=["Amazon"],
            needs_live_news=False,
        ),
        ResearchPlan(
            subject="s",
            sub_questions=["revised"],
            companies=["Amazon"],
            needs_live_news=False,
        ),
    ]
    rejection = {"approved": False, "notes": "Focus on AWS margins"}

    final, requests = await run(decisions=[rejection, APPROVE])

    assert [r["sub_questions"] for r in requests] == [["first"], ["revised"]]
    assert graph_deps.llm.labels().count("write") == 1  # only after approval
    replan_prompt = [u for label, u in graph_deps.llm.calls if label == "plan"][1]
    assert "REJECTED" in replan_prompt
    assert "Focus on AWS margins" in replan_prompt
    assert final.report is not None


async def test_auto_approval_when_disabled(
    graph_deps: GraphDeps, monkeypatch: Any
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("APPROVAL_REQUIRED", "false")
    get_settings.cache_clear()

    final, requests = await run()

    assert requests == []
    assert final.approval is not None and final.approval["mode"] == "auto"


async def test_critique_loop_is_capped(graph_deps: GraphDeps) -> None:
    graph_deps.llm.responses["critique"] = Critique(
        is_complete=False, missing=["never satisfied"]
    )

    final, requests = await run()

    assert final.loop_count == MAX_LOOPS
    assert len(requests) == MAX_LOOPS  # one approval per pass
    assert graph_deps.llm.labels().count("critique") == MAX_LOOPS
    assert final.report is not None


async def test_second_pass_narrows_and_accumulates_evidence(
    graph_deps: GraphDeps,
) -> None:
    gap_hit = {**HIT, "id": "amzn-annual-report-10k-200", "chunk_no": 200}
    graph_deps.search.side_effect = [[HIT], [gap_hit]]
    graph_deps.llm.responses["critique"] = [
        Critique(is_complete=False, missing=["Amazon AWS margin"]),
        Critique(is_complete=True),
    ]

    final, _ = await run()

    assert final.loop_count == 2
    assert [e.reference for e in final.filing_evidence] == [HIT["id"], gap_hit["id"]]
    second_plan_prompt = [u for label, u in graph_deps.llm.calls if label == "plan"][1]
    assert "Amazon AWS margin" in second_plan_prompt


async def test_news_outage_degrades_but_run_succeeds(graph_deps: GraphDeps) -> None:
    plan = graph_deps.llm.responses["plan"]
    graph_deps.llm.responses["plan"] = plan.model_copy(update={"needs_live_news": True})

    final, requests = await run()  # no news tool configured

    assert final.degraded == ["news"]
    assert requests[0]["degraded"] == ["news"]
    assert final.report is not None
