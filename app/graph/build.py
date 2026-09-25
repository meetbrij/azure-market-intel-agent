"""StateGraph assembly.

    START -> plan -> (retrieve_filings || fetch_news) -> compact
          -> approve_gate -(approved)-> write -> critique -> (plan | END)
                          -(rejected)-> plan

uv run python -m app.graph.build "question" [--companies Amazon Alphabet] [--yes]

The CLI uses an in-memory checkpointer; the worker uses Postgres
(app/graph/checkpoint.py). approve_gate pauses via interrupt(), which needs a
checkpointer to resume.
"""

import argparse
import asyncio
import json
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.azure_clients import close_async_clients
from app.config import get_settings
from app.graph.nodes import (
    approve_gate,
    compact,
    critique,
    fetch_news,
    plan,
    retrieve_filings,
    route_after_approval,
    route_after_critique,
    write,
)
from app.graph.state import ResearchState
from app.graph.tools import NewsToolRunner
from app.logging_setup import configure_logging


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> CompiledStateGraph[Any]:
    builder = StateGraph(ResearchState)
    # LLM nodes get LangGraph's wall-clock timeout (NodeTimeoutError fails the
    # job). Evidence nodes bound their own calls and degrade instead of
    # failing. approve_gate only waits on a human, which happens between runs.
    llm_timeout = get_settings().node_timeout_s
    builder.add_node("plan", plan, timeout=llm_timeout)
    builder.add_node("retrieve_filings", retrieve_filings)
    builder.add_node("fetch_news", fetch_news)
    builder.add_node("compact", compact, timeout=llm_timeout)
    builder.add_node("approve_gate", approve_gate)
    builder.add_node("write", write, timeout=llm_timeout)
    builder.add_node("critique", critique, timeout=llm_timeout)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "retrieve_filings")  # parallel fan-out
    builder.add_edge("plan", "fetch_news")
    builder.add_edge(["retrieve_filings", "fetch_news"], "compact")  # waits for both
    builder.add_edge("compact", "approve_gate")
    builder.add_conditional_edges(
        "approve_gate", route_after_approval, ["write", "plan"]
    )
    builder.add_edge("write", "critique")
    builder.add_conditional_edges("critique", route_after_critique, ["plan", END])
    return builder.compile(checkpointer=checkpointer)


def thread_config(thread_id: str) -> RunnableConfig:
    # thread_id is the job UUID: unique, stable across restarts, < 255 chars.
    return RunnableConfig(configurable={"thread_id": thread_id})


def _describe(node: str, update: dict[str, Any] | None) -> str:
    u = update or {}
    if node == "plan":
        p = u["plan"]
        qs = "".join(f"\n      - {q}" for q in p.sub_questions)
        return (
            f"pass {u['loop_count']}: companies={p.companies} "
            f"news={p.needs_live_news}{qs}"
        )
    if node == "retrieve_filings":
        return f"{len(u['filing_evidence'])} filing evidence item(s) in state"
    if node == "fetch_news":
        if not u:
            return "skipped"
        if "degraded" in u:
            return f"degraded={u['degraded']}"
        return f"{len(u['news_evidence'])} news item(s) in state"
    if node == "compact":
        return f"brief ~{len(u['compacted_context']) // 4} tokens"
    if node == "approve_gate":
        return f"approval={u['approval']}"

    if node == "write":
        r = u["report"]
        n = sum(len(s.citations) for s in r.sections)
        return f"{len(r.sections)} section(s), {n} citation(s)"
    if node == "critique":
        c = u["critique"]
        lines = [f"complete={c.is_complete}"]
        lines += [f"\n      missing: {m}" for m in c.missing]
        lines += [f"\n      citation problem: {p}" for p in c.citation_problems]
        return "".join(lines)
    return str(u)


def _ask_approval(payload: dict[str, Any], auto: bool) -> dict[str, Any]:
    print("\n== approval requested:\n" + json.dumps(payload, indent=2), flush=True)
    if auto:
        print("== auto-approved (--yes)")
        return {"approved": True, "notes": None}
    answer = input(
        "Approve? [y = approve / anything else = reject with that as notes]: "
    )
    if answer.strip().lower() in {"y", "yes"}:
        return {"approved": True, "notes": None}
    return {"approved": False, "notes": answer.strip() or None}


async def _run(query: str, companies: list[str], auto_approve: bool) -> None:
    graph = build_graph(InMemorySaver())
    config = thread_config(str(uuid.uuid4()))
    news = NewsToolRunner()
    await news.start()
    pending: Any = ResearchState(query=query, companies=companies)
    try:
        while True:
            async for update in graph.astream(pending, config, stream_mode="updates"):
                for node, change in update.items():
                    if node != "__interrupt__":
                        print(f">> {node}: {_describe(node, change)}", flush=True)
            snapshot = await graph.aget_state(config)
            if not snapshot.interrupts:
                break
            decision = _ask_approval(snapshot.interrupts[0].value, auto_approve)
            pending = Command(resume=decision)
    finally:
        await news.stop()
        await close_async_clients()
    state = ResearchState.model_validate(snapshot.values)
    print(f"\n== loops={state.loop_count} degraded={state.degraded}")
    if state.report is None:
        raise SystemExit(f"No report produced: {state.error}")
    print(state.report.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--companies", nargs="*", default=[])
    parser.add_argument("--yes", action="store_true", help="auto-approve the plan")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(_run(args.query, args.companies, args.yes))


if __name__ == "__main__":
    main()
