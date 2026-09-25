"""The compiled graph end to end, with the LLM and Search mocked."""

from app.graph.build import graph
from app.graph.nodes import MAX_LOOPS
from app.graph.state import Critique, ResearchState
from tests.conftest import GraphDeps


async def run(query: str = "Amazon operating income?") -> ResearchState:
    result = await graph.ainvoke(ResearchState(query=query, companies=["Amazon"]))
    return ResearchState.model_validate(result)


async def test_complete_on_first_pass_runs_each_node_once(
    graph_deps: GraphDeps,
) -> None:
    final = await run()

    assert graph_deps.llm.labels() == ["plan", "compact", "write", "critique"]
    assert final.loop_count == 1
    assert final.report is not None
    assert final.report.sections[0].citations[0].page == 27
    assert final.approval is not None and final.approval["approved"] is True


async def test_critique_loop_is_capped(graph_deps: GraphDeps) -> None:
    graph_deps.llm.responses["critique"] = Critique(
        is_complete=False, missing=["never satisfied"]
    )

    final = await run()

    assert final.loop_count == MAX_LOOPS
    assert graph_deps.llm.labels().count("plan") == MAX_LOOPS
    assert graph_deps.llm.labels().count("critique") == MAX_LOOPS
    assert final.report is not None  # still returns the best report it has


async def test_second_pass_narrows_and_accumulates_evidence(
    graph_deps: GraphDeps,
) -> None:
    from tests.conftest import HIT

    gap_hit = {**HIT, "id": "amzn-annual-report-10k-200", "chunk_no": 200}
    graph_deps.search.side_effect = [[HIT], [gap_hit]]
    graph_deps.llm.responses["critique"] = [
        Critique(is_complete=False, missing=["Amazon AWS margin"]),
        Critique(is_complete=True),
    ]

    final = await run()

    assert final.loop_count == 2
    assert [e.reference for e in final.filing_evidence] == [HIT["id"], gap_hit["id"]]
    second_plan_prompt = [u for label, u in graph_deps.llm.calls if label == "plan"][1]
    assert "Amazon AWS margin" in second_plan_prompt


async def test_news_outage_degrades_but_run_succeeds(graph_deps: GraphDeps) -> None:
    plan = graph_deps.llm.responses["plan"]
    graph_deps.llm.responses["plan"] = plan.model_copy(update={"needs_live_news": True})

    final = await run()  # no news tool configured

    assert final.degraded == ["news"]
    assert final.report is not None
