"""Langfuse tracing: one trace per research job, id derived from the job id.

    trace research-job (trace id = job id without dashes)
      span job:start / job:resume / job:recover     one per worker run
        LangGraph run -> node spans (LangChain callback handler)
          generation  plan / compact / write / critique / screen
                      (model name, token usage; Langfuse prices it)
          embedding   embed
          retriever   search (mode requested and used, hits)
          tool        search_company_news (the MCP tool, via the handler)
      span job:outcome                           tags outcome:<...>

Trace attributes: user = submitter's Entra oid, session = job id, tags
`variant:<retrieval mode>`, `model:<chat model>`, `outcome:<completed|failed>`.

Keys come from Key Vault (keyless like everything else). If they're missing
or Langfuse is unreachable, tracing is off and nothing else changes:
observability must never fail a job.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse, propagate_attributes
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace

from app.config import get_settings

log = logging.getLogger(__name__)

_client: Langfuse | None = None


def init_tracing() -> Langfuse | None:
    """Create the process-wide Langfuse client (blocking: call once, at
    startup, off the event loop). Returns None when tracing is off."""
    global _client
    s = get_settings()
    if not s.tracing_enabled:
        log.info("Tracing disabled (TRACING_ENABLED=false)")
        return None
    try:
        from app.azure_clients import get_secret_client

        secrets = get_secret_client()
        public_key = secrets.get_secret(s.langfuse_public_key_secret).value
        secret_key = secrets.get_secret(s.langfuse_secret_key_secret).value
    except Exception as e:  # noqa: BLE001 — no keys, no tracing; never fatal
        log.warning("Tracing off: Langfuse keys unavailable (%s)", type(e).__name__)
        return None
    _client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=s.langfuse_host,
        environment=s.environment,
    )
    log.info("Tracing to Langfuse at %s", s.langfuse_host)
    return _client


def get_langfuse() -> Langfuse | None:
    return _client


def set_langfuse(client: Langfuse | None) -> None:
    """For tests (a client with an in-memory exporter) and shutdown."""
    global _client
    _client = client


def shutdown_tracing() -> None:
    if _client is not None:
        _client.flush()
        _client.shutdown()
        set_langfuse(None)


def trace_id_for(job_id: str) -> str:
    """Langfuse trace ids are 32 lowercase hex chars: a UUID job id minus its
    dashes, so a job's trace can be found from its id (and vice versa)."""
    candidate = job_id.replace("-", "").lower()
    if len(candidate) == 32 and all(c in "0123456789abcdef" for c in candidate):
        return candidate
    return Langfuse.create_trace_id(seed=job_id)


def chat_model_name(deployment: str) -> str:
    s = get_settings()
    if deployment == s.azure_openai_chat_fallback_deployment:
        return s.azure_openai_chat_fallback_model or deployment
    return s.azure_openai_chat_model


@contextmanager
def job_trace(
    job_id: str,
    *,
    segment: str,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> Iterator[list[Any]]:
    """A span for one worker run of a job, in the job's trace. Yields the
    LangChain callbacks to pass to the graph (empty when tracing is off)."""
    client = get_langfuse()
    if client is None:
        yield []
        return
    from langfuse.langchain import CallbackHandler

    with (
        client.start_as_current_observation(
            trace_context={"trace_id": trace_id_for(job_id)},
            name=f"job:{segment}",
            as_type="span",
            metadata={"job_id": job_id, **(metadata or {})},
        ),
        propagate_attributes(
            trace_name="research-job",
            session_id=job_id,
            user_id=user_id,
            tags=tags,
            metadata={"job_id": job_id},
        ),
    ):
        yield [CallbackHandler()]


@contextmanager
def eval_trace(run: str, item_id: str, variant: str) -> Iterator[None]:
    """One trace per golden question in an eval run (session = run name), so
    Langfuse's cost can be reconciled against the harness's (evals/reconcile.py)."""
    client = get_langfuse()
    if client is None:
        yield
        return
    with (
        client.start_as_current_observation(
            trace_context={"trace_id": eval_trace_id(run, item_id)},
            name=f"eval:{item_id}",
            as_type="span",
        ),
        propagate_attributes(
            trace_name="eval-question",
            session_id=run,
            tags=["eval", f"variant:{variant}", f"model:{get_settings().azure_openai_chat_model}"],
            metadata={"item_id": item_id},
        ),
    ):
        yield


def eval_trace_id(run: str, item_id: str) -> str:
    return Langfuse.create_trace_id(seed=f"{run}:{item_id}")


def record_outcome(job_id: str, outcome: str, detail: dict[str, Any]) -> None:
    """Tag the job's trace with how it ended (for failure-rate views)."""
    client = get_langfuse()
    if client is None:
        return
    try:
        with (
            client.start_as_current_observation(
                trace_context={"trace_id": trace_id_for(job_id)},
                name="job:outcome",
                as_type="span",
                level="ERROR" if outcome == "failed" else "DEFAULT",
                output=detail,
            ),
            propagate_attributes(
                trace_name="research-job",
                session_id=job_id,
                tags=[f"outcome:{outcome}"],
            ),
        ):
            pass
    except Exception:  # tracing never fails a job
        log.exception("Could not record the outcome in the trace")


def _current_node_span() -> Any | None:
    """The Langfuse span of the graph node we're running in, if any.

    LangGraph runs each node in a task whose context was copied before the
    callback handler made the node's span current, so a plain "current span"
    would be the job's root span. The node's callback manager knows the node's
    run id, and the handler maps run ids to spans. (`_runs` is the handler's
    internal map; tests/test_observability.py pins this nesting.)"""
    try:
        from langgraph.config import get_config

        manager = get_config().get("callbacks")
    except RuntimeError:  # not inside a graph run
        return None
    run_id = getattr(manager, "parent_run_id", None)
    for handler in getattr(manager, "handlers", None) or []:
        runs = getattr(handler, "_runs", None)
        if isinstance(runs, dict) and run_id in runs:
            return runs[run_id]
    return None


@contextmanager
def observe(
    name: str, as_type: str = "span", **attributes: Any
) -> Iterator[Any | None]:
    """A child observation of the current graph node's span (or of whatever
    span is current outside the graph). Yields the observation, to
    `.update(...)` with results, or None when tracing is off."""
    client = get_langfuse()
    if client is None:
        yield None
        return
    node = _current_node_span()
    token = (
        otel_context.attach(otel_trace.set_span_in_context(node._otel_span))
        if node is not None
        else None
    )
    try:
        with client.start_as_current_observation(  # type: ignore[call-overload]
            name=name, as_type=as_type, **attributes
        ) as observation:
            yield observation
    finally:
        if token is not None:
            otel_context.detach(token)
