"""StateGraph assembly.

    START -> plan -> (retrieve_filings || fetch_news) -> compact
          -> approve_gate -> write -> critique -> (plan | END)

uv run python -m app.graph.build "question" [--companies Amazon Alphabet]
"""

import argparse
import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.azure_clients import close_async_clients
from app.graph.nodes import (
    approve_gate,
    compact,
    critique,
    fetch_news,
    plan,
    retrieve_filings,
    route_after_critique,
    write,
)
from app.graph.state import ResearchState
from app.graph.tools import NewsToolRunner
from app.logging_setup import configure_logging

builder = StateGraph(ResearchState)
builder.add_node("plan", plan)
builder.add_node("retrieve_filings", retrieve_filings)
builder.add_node("fetch_news", fetch_news)
builder.add_node("compact", compact)
builder.add_node("approve_gate", approve_gate)
builder.add_node("write", write)
builder.add_node("critique", critique)

builder.add_edge(START, "plan")
builder.add_edge("plan", "retrieve_filings")  # parallel fan-out
builder.add_edge("plan", "fetch_news")
builder.add_edge(["retrieve_filings", "fetch_news"], "compact")  # waits for both
builder.add_edge("compact", "approve_gate")
builder.add_edge("approve_gate", "write")
builder.add_edge("write", "critique")
builder.add_conditional_edges("critique", route_after_critique, ["plan", END])

graph = builder.compile()


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


async def _run(query: str, companies: list[str]) -> None:
    final: dict[str, Any] = {}
    news = NewsToolRunner()
    await news.start()
    try:
        async for mode, chunk in graph.astream(
            ResearchState(query=query, companies=companies),
            stream_mode=["updates", "values"],
        ):
            if not isinstance(chunk, dict):
                continue
            if mode == "updates":
                for node, update in chunk.items():
                    print(f">> {node}: {_describe(node, update)}", flush=True)
            else:
                final = chunk
    finally:
        await news.stop()
        await close_async_clients()
    state = ResearchState.model_validate(final)
    print(f"\n== loops={state.loop_count} degraded={state.degraded}")
    if state.report is None:
        raise SystemExit(f"No report produced: {state.error}")
    print(state.report.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--companies", nargs="*", default=[])
    args = parser.parse_args()
    configure_logging()
    asyncio.run(_run(args.query, args.companies))


if __name__ == "__main__":
    main()
