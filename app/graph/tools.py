"""External tools the graph can call.

Day 8 adds the MCP news server and loads its tools here at worker startup.
Until then no news tool is configured, and `fetch_news` records "news" as a
degraded source instead of failing the run.
"""

from typing import Protocol, TypedDict


class NewsItem(TypedDict):
    title: str
    url: str
    published: str | None
    snippet: str


class NewsTool(Protocol):
    async def search_company_news(
        self, company: str, days: int = 7, max_results: int = 5
    ) -> list[NewsItem]: ...


_news_tool: NewsTool | None = None


def set_news_tool(tool: NewsTool | None) -> None:
    global _news_tool
    _news_tool = tool


def get_news_tool() -> NewsTool | None:
    return _news_tool
