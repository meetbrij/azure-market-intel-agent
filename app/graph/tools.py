"""External tools the graph can call: the MCP news server.

The MCP session is opened once per process (worker startup / CLI run) and kept
open — the handshake, and for stdio a Python subprocess, are not free. If the
server can't start, no tool is registered and `fetch_news` degrades.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Protocol, TypedDict

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.client.stdio import get_default_environment

from app.config import get_settings
from mcp_news.sanitize import MAX_SNIPPET_CHARS, MAX_TITLE_CHARS, clean_text, iso_date

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT_S = 30.0
CALL_TIMEOUT_S = 30.0
# The stdio server only needs Key Vault access; pass nothing else through.
_SERVER_ENV_PREFIXES = ("AZURE_", "IDENTITY_", "MSI_", "NEWS_MCP_", "TAVILY_")


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


# ---------- MCP-backed implementation ----------


def to_news_items(payload: Any) -> list[NewsItem]:
    """Re-sanitise server output: the server may be remote (Phase 4), so the
    client never assumes its content is clean."""
    rows = payload.get("result", []) if isinstance(payload, dict) else payload
    items: list[NewsItem] = []
    for r in rows or []:
        url = str(r.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        items.append(
            NewsItem(
                title=clean_text(r.get("title"), MAX_TITLE_CHARS),
                url=url,
                published=iso_date(r.get("published")),
                snippet=clean_text(r.get("snippet"), MAX_SNIPPET_CHARS),
            )
        )
    return items


class McpNewsTool:
    def __init__(self, tool: BaseTool) -> None:
        self._tool = tool

    async def search_company_news(
        self, company: str, days: int = 7, max_results: int = 5
    ) -> list[NewsItem]:
        call = {
            "name": self._tool.name,
            "args": {"company": company, "days": days, "max_results": max_results},
            "id": f"news-{company}",
            "type": "tool_call",
        }
        message = await asyncio.wait_for(self._tool.ainvoke(call), CALL_TIMEOUT_S)
        if getattr(message, "status", None) == "error":
            raise RuntimeError(f"news tool error: {message.content}")
        artifact = getattr(message, "artifact", None) or {}
        return to_news_items(artifact.get("structured_content"))


def server_connection() -> Connection:
    url = get_settings().news_mcp_url
    if url:
        return {"transport": "streamable_http", "url": url}
    env = get_default_environment()
    env.update(
        {k: v for k, v in os.environ.items() if k.startswith(_SERVER_ENV_PREFIXES)}
    )
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "mcp_news.server"],
        "env": env,
        "cwd": str(PROJECT_ROOT),
    }


class NewsToolRunner:
    """Holds the MCP session open in its own task (anyio cancel scopes must be
    entered and exited by the same task) and registers the tool."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> bool:
        if not get_settings().news_enabled:
            log.info("News tool disabled (NEWS_ENABLED=false)")
            return False
        self._task = asyncio.create_task(self._run(), name="news-mcp-session")
        try:
            await asyncio.wait_for(self._ready.wait(), STARTUP_TIMEOUT_S)
        except TimeoutError:
            log.error("News MCP server did not start within %.0fs", STARTUP_TIMEOUT_S)
            await self.stop()
        return get_news_tool() is not None

    async def _run(self) -> None:
        client = MultiServerMCPClient({"news": server_connection()})
        try:
            async with client.session("news") as session:
                tools = {t.name: t for t in await load_mcp_tools(session)}
                set_news_tool(McpNewsTool(tools["search_company_news"]))
                log.info("News MCP server connected; tools: %s", sorted(tools))
                self._ready.set()
                await self._stop.wait()
        except Exception:
            log.exception("News MCP server unavailable; runs will be degraded")
        finally:
            set_news_tool(None)
            self._ready.set()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 10)
            except TimeoutError:
                self._task.cancel()
            self._task = None
