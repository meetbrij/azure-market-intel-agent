"""StateGraph assembly: START -> retrieve -> write -> END.

uv run python -m app.graph.build "question" [--companies Amazon Alphabet]
"""

import argparse
import asyncio

from langgraph.graph import END, START, StateGraph

from app.azure_clients import close_async_clients
from app.graph.nodes import retrieve, write
from app.graph.state import ResearchState
from app.logging_setup import configure_logging

builder = StateGraph(ResearchState)
builder.add_node("retrieve", retrieve)
builder.add_node("write", write)
builder.add_edge(START, "retrieve")
builder.add_edge("retrieve", "write")
builder.add_edge("write", END)
graph = builder.compile()


async def _run(query: str, companies: list[str]) -> None:
    try:
        result = await graph.ainvoke(ResearchState(query=query, companies=companies))
    finally:
        await close_async_clients()
    state = ResearchState.model_validate(result)
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
