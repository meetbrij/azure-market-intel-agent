"""Chat-model calls used by the graph nodes. One place for model config, so
retries and the fallback deployment (Day 11) land here."""

import logging

from app.azure_clients import get_async_aoai
from app.config import get_settings

log = logging.getLogger(__name__)


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def parse_structured[T](label: str, system: str, user: str, schema: type[T]) -> T:
    """Chat call returning a validated `schema` instance (structured output)."""
    # No temperature / max_tokens: GPT-5-family reasoning models reject them.
    completion = await get_async_aoai().chat.completions.parse(
        model=get_settings().azure_openai_chat_deployment,
        messages=_messages(system, user),  # type: ignore[arg-type]
        response_format=schema,
    )
    _log_usage(label, completion.usage)
    message = completion.choices[0].message
    if message.refusal:
        raise RuntimeError(f"{label}: model refused: {message.refusal}")
    if message.parsed is None:
        raise RuntimeError(f"{label}: model returned no parsable {schema.__name__}")
    return message.parsed


async def complete_text(label: str, system: str, user: str) -> str:
    completion = await get_async_aoai().chat.completions.create(
        model=get_settings().azure_openai_chat_deployment,
        messages=_messages(system, user),  # type: ignore[arg-type]
    )
    _log_usage(label, completion.usage)
    text = completion.choices[0].message.content
    if not text:
        raise RuntimeError(f"{label}: model returned no text")
    return text


def _log_usage(label: str, usage: object) -> None:
    if usage is None:
        return
    log.info(
        "%s: %s prompt + %s completion tokens",
        label,
        getattr(usage, "prompt_tokens", "?"),
        getattr(usage, "completion_tokens", "?"),
    )
