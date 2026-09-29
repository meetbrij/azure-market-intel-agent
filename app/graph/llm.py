"""Chat-model calls used by the graph nodes.

Each call retries transient errors (app/resilience.py). If the primary
deployment still fails with a 429 or a timeout, the call is repeated against
AZURE_OPENAI_CHAT_FALLBACK_DEPLOYMENT when one is configured. Every call logs
which deployment served it.
"""

import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

import openai

from app.azure_clients import get_async_aoai
from app.config import get_settings
from app.graph.state import LlmCall
from app.observability import chat_model_name, observe
from app.resilience import with_retries

log = logging.getLogger(__name__)

_recorded: ContextVar[list[LlmCall] | None] = ContextVar("llm_calls", default=None)


@contextmanager
def recording_calls() -> Iterator[list[LlmCall]]:
    """Collect the LLM calls made inside the block (one node's worth)."""
    calls: list[LlmCall] = []
    token = _recorded.set(calls)
    try:
        yield calls
    finally:
        _recorded.reset(token)


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def is_content_filtered(exc: BaseException) -> bool:
    """Azure OpenAI refused the prompt (content filter, including Prompt
    Shields' jailbreak detection). Never retried: the same prompt is refused
    again, on any deployment."""
    return (
        isinstance(exc, openai.BadRequestError)
        and getattr(exc, "code", None) == "content_filter"
    )


def _falls_back(exc: BaseException) -> bool:
    return isinstance(
        exc, openai.RateLimitError | openai.APITimeoutError | TimeoutError
    )


async def _chat[T](
    label: str, call: Callable[[str], Awaitable[T]], messages: list[dict[str, str]]
) -> T:
    """Run `call(deployment)` on the primary, then the fallback if warranted.
    Traced as one Langfuse generation (under the current node's span)."""
    s = get_settings()
    primary, fallback = (
        s.azure_openai_chat_deployment,
        s.azure_openai_chat_fallback_deployment,
    )
    with observe(
        label, "generation", model=chat_model_name(primary), input=messages
    ) as generation:
        try:
            try:
                result = await with_retries(label, lambda: call(primary))
                deployment = primary
            except Exception as e:
                if not fallback or not _falls_back(e):
                    raise
                log.warning(
                    "%s: %s still failing after retries (%s); falling back to %s",
                    label,
                    primary,
                    type(e).__name__,
                    fallback,
                )
                result = await with_retries(
                    f"{label} (fallback)", lambda: call(fallback)
                )
                deployment = fallback
        except Exception as e:
            if generation is not None:
                generation.update(level="ERROR", status_message=type(e).__name__)
            raise
        usage = getattr(result, "usage", None)
        _log_served(label, deployment, usage)
        if generation is not None:
            generation.update(
                model=chat_model_name(deployment),
                output=_output_text(result),
                usage_details=_usage_details(usage),
                metadata={"deployment": deployment},
            )
    return result


def _usage_details(usage: object) -> dict[str, int]:
    details = {
        "input": getattr(usage, "prompt_tokens", None),
        "output": getattr(usage, "completion_tokens", None),
    }
    return {k: v for k, v in details.items() if isinstance(v, int)}


def _output_text(result: object) -> str | None:
    try:
        return str(result.choices[0].message.content)  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        return None


async def parse_structured[T](label: str, system: str, user: str, schema: type[T]) -> T:
    """Chat call returning a validated `schema` instance (structured output)."""

    async def call(deployment: str) -> Any:
        # No temperature / max_tokens: GPT-5-family reasoning models reject them.
        return await get_async_aoai().chat.completions.parse(
            model=deployment,
            messages=_messages(system, user),  # type: ignore[arg-type]
            response_format=schema,
        )

    completion = await _chat(label, call, _messages(system, user))
    message = completion.choices[0].message
    if message.refusal:
        raise RuntimeError(f"{label}: model refused: {message.refusal}")
    if message.parsed is None:
        raise RuntimeError(f"{label}: model returned no parsable {schema.__name__}")
    parsed: T = message.parsed
    return parsed


async def complete_text(label: str, system: str, user: str) -> str:
    async def call(deployment: str) -> Any:
        return await get_async_aoai().chat.completions.create(
            model=deployment,
            messages=_messages(system, user),  # type: ignore[arg-type]
        )

    completion = await _chat(label, call, _messages(system, user))
    text = completion.choices[0].message.content
    if not text:
        raise RuntimeError(f"{label}: model returned no text")
    return str(text)


def _log_served(label: str, deployment: str, usage: object) -> None:
    calls = _recorded.get()
    if calls is not None:
        calls.append(
            LlmCall(
                node=label,
                deployment=deployment,
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
    log.info(
        "%s: served by %s, %s prompt + %s completion tokens",
        label,
        deployment,
        getattr(usage, "prompt_tokens", "?"),
        getattr(usage, "completion_tokens", "?"),
    )
