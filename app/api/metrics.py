"""Prometheus metrics for the API: request latency per route, and jobs by
status (read from the database at scrape time, so it covers the worker's
work too). Served at GET /metrics.

Routes are labelled by their template (`/api/v1/research/{job_id}`), never the
raw path, so job ids can't blow up label cardinality.
"""

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    Histogram,
    generate_latest,
)

from app.jobs import store
from app.jobs.models import JobStatus

log = logging.getLogger("app.api.requests")

REGISTRY = CollectorRegistry()
REQUEST_SECONDS = Histogram(
    "http_request_duration_seconds",
    "API request latency",
    ["method", "route", "status"],
    registry=REGISTRY,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
JOBS = Gauge("research_jobs", "Research jobs by status", ["status"], registry=REGISTRY)
_QUIET_ROUTES = {"/metrics", "/health"}  # probes: counted, not logged


async def time_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = perf_counter()
    status = 500  # if the app raises, the client gets a 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        elapsed = perf_counter() - start
        route = getattr(request.scope.get("route"), "path", "unmatched")
        REQUEST_SECONDS.labels(request.method, route, str(status)).observe(elapsed)
        if route not in _QUIET_ROUTES:
            log.info(
                "request method=%s route=%s status=%d ms=%.1f",
                request.method,
                route,
                status,
                elapsed * 1000,
            )


async def render_metrics() -> Response:
    counts = await store.count_by_status()
    for status in JobStatus:  # report zeros too, so series don't vanish
        JOBS.labels(status.value).set(counts.get(status.value, 0))
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
