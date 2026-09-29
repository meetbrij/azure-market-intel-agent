"""Container health check, for the image's HEALTHCHECK and Kubernetes exec
probes. One image runs three components, so MIA_COMPONENT says which:

    api     GET /livez answers (the process is serving; dependencies are
            /health's job, used for readiness)
    worker  arq's health key in Redis is fresh (the worker loop is alive)
    news    the MCP server's port accepts connections

    python -m app.healthcheck        exit 0 = healthy, 1 = not
"""

import asyncio
import os
import socket
import sys
import urllib.request

TIMEOUT_S = 3.0


def check_api(port: int = 8000) -> bool:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/livez", timeout=TIMEOUT_S) as r:
        return bool(r.status == 200)


def check_worker() -> bool:
    # Deliberately doesn't import the app (the graph, SDKs...): a probe runs
    # every few seconds and must stay cheap.
    from arq.connections import RedisSettings
    from arq.worker import async_check_health

    redis = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    # 0 = arq's health key exists (the worker refreshes it every 30 s).
    return asyncio.run(async_check_health(redis, None, None)) == 0


def check_news(port: int | None = None) -> bool:
    port = port or int(os.environ.get("NEWS_MCP_PORT", "8001"))
    with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT_S):
        return True


CHECKS = {"api": check_api, "worker": check_worker, "news": check_news}


def main() -> int:
    component = os.environ.get("MIA_COMPONENT", "api")
    check = CHECKS.get(component)
    if check is None:
        print(f"unknown MIA_COMPONENT {component!r}", file=sys.stderr)
        return 1
    try:
        return 0 if check() else 1
    except Exception as e:  # noqa: BLE001 — any failure means unhealthy
        print(f"{component} unhealthy: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
