"""Reconcile an eval run's cost: the harness's own figure vs Langfuse's.

    uv run python -m evals.run --smoke --trace --name smoke-traced
    uv run python -m evals.reconcile results/smoke-traced.json

The harness prices each question from token counts and evals/pricing.yaml.
Langfuse prices the same calls from its own model price table. If the two
agree, neither the harness nor the tracing is miscounting tokens or cost.
Expect a small gap from embeddings: the harness estimates embedding tokens
(characters / 4) while Langfuse records the real count.

Exits 1 if the totals differ by more than --tolerance (default 5%).
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.logging_setup import configure_logging
from app.observability import eval_trace_id, get_langfuse, init_tracing
from evals.answer import Sample
from evals.golden import load_pricing
from evals.metrics import cost_usd


def trace_cost(trace_id: str, since: datetime) -> float | None:
    """Sum of the trace's observation costs, via the v2 observations API
    (the only read API for new Langfuse Cloud organisations)."""
    client = get_langfuse()
    assert client is not None
    response = client.api.observations.get_many(
        trace_id=trace_id,
        fields="core,usage",
        limit=1000,
        from_start_time=since,
        to_start_time=datetime.now(UTC),
    )
    costs = [o.total_cost for o in response.data if o.total_cost]
    return sum(costs) if costs else None


def langfuse_costs(
    run: str, item_ids: list[str], since: datetime, wait_s: float
) -> dict[str, float | None]:
    """Each question's trace cost, waiting for ingestion to finish (Langfuse
    processes spans asynchronously)."""
    costs: dict[str, float | None] = dict.fromkeys(item_ids)
    deadline = time.monotonic() + wait_s
    while True:
        for item_id in item_ids:
            if costs[item_id] is None:
                costs[item_id] = trace_cost(eval_trace_id(run, item_id), since)
        if all(costs.values()) or time.monotonic() > deadline:
            return costs
        time.sleep(5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="an evals.run --trace output JSON")
    parser.add_argument("--tolerance", type=float, default=0.05)
    parser.add_argument("--wait", type=float, default=120, help="seconds to wait for ingestion")
    args = parser.parse_args()
    configure_logging()

    run: dict[str, Any] = json.loads(args.results.read_text())
    session = run["meta"].get("langfuse_session")
    if not session:
        print("This run was not traced (use evals.run --trace)", file=sys.stderr)
        return 1
    if init_tracing() is None:
        print("Langfuse is unavailable (keys?)", file=sys.stderr)
        return 1

    pricing = run["meta"].get("pricing") or load_pricing()
    samples = [Sample.model_validate(s) for s in run["samples"]]
    ours = {s.id: cost_usd(s, pricing) for s in samples}
    since = datetime.fromisoformat(run["meta"]["timestamp"]) - timedelta(minutes=5)
    theirs = langfuse_costs(session, list(ours), since, args.wait)

    print("| question | harness ($) | Langfuse ($) | diff |\n|---|---|---|---|")
    for item_id, cost in ours.items():
        lf = theirs.get(item_id)
        diff = f"{(lf - cost) / cost:+.1%}" if lf and cost else "—"
        print(f"| {item_id} | {cost:.6f} | {lf:.6f} | {diff} |" if lf else f"| {item_id} | {cost:.6f} | missing | — |")
    total_ours = sum(ours.values())
    total_lf = sum(v for v in theirs.values() if v)
    gap = (total_lf - total_ours) / total_ours if total_ours else 0.0
    print(f"| **total** | {total_ours:.6f} | {total_lf:.6f} | {gap:+.1%} |")
    missing = [k for k, v in theirs.items() if not v]
    if missing:
        print(f"Missing in Langfuse (or priced at 0): {', '.join(missing)}", file=sys.stderr)
        return 1
    if abs(gap) > args.tolerance:
        print(f"Costs differ by {gap:+.1%} (> {args.tolerance:.0%})", file=sys.stderr)
        return 1
    print(f"Reconciled: within {args.tolerance:.0%}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
