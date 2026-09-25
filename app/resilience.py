"""Retries for external calls (Search, Azure OpenAI).

One retry layer: the SDKs' own retries are switched off in azure_clients.py,
so attempts don't multiply. Retry 429, 5xx and timeouts; never other 4xx.
"""

import logging
from collections.abc import Awaitable, Callable

import openai
from azure.core.exceptions import (
    HttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from app.config import get_settings

log = logging.getLogger(__name__)


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, openai.APIStatusError):  # includes RateLimitError (429)
        return exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(exc, HttpResponseError):  # Azure SDKs
        status = exc.status_code or 0
        return status in (408, 429) or status >= 500
    return isinstance(exc, ServiceRequestError | ServiceResponseError | TimeoutError)


def _log_retry(label: str) -> Callable[[RetryCallState], None]:
    def log_it(state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        wait = state.next_action.sleep if state.next_action else 0
        log.warning(
            "%s: attempt %d failed (%s); retrying in %.1fs",
            label,
            state.attempt_number,
            type(exc).__name__,
            wait,
        )

    return log_it


async def with_retries[T](label: str, call: Callable[[], Awaitable[T]]) -> T:
    """Await `call()` with exponential backoff on transient errors."""
    s = get_settings()
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(is_transient),
        stop=stop_after_attempt(s.retry_attempts),
        wait=wait_random_exponential(multiplier=s.retry_base_wait_s, max=30),
        before_sleep=_log_retry(label),
        reraise=True,
    ):
        with attempt:
            return await call()
    raise AssertionError("unreachable")  # AsyncRetrying always returns or raises
