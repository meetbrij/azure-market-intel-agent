"""Retrieval evaluation harness.

    uv run python -m evals.run [--smoke] [--variant vector] [--name NAME] [--out results/]

For each golden question: retrieve, answer once from those chunks, then score
with RAGAS (context precision/recall, faithfulness, answer relevancy) in the
isolated evals/ragas environment, plus citation validity and abstention in
Python. Writes <out>/<name>.json and <name>.md.

--smoke runs the 5 questions in evals/thresholds.yaml, scores faithfulness
only (to keep CI cheap), and exits 1 if faithfulness or citation validity is
below its threshold.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.azure_clients import close_async_clients
from app.config import get_settings
from app.graph.retrieval import search_many
from app.logging_setup import configure_logging
from evals.answer import Sample, answer_item
from evals.golden import EVALS_DIR, load_golden, load_pricing, load_thresholds
from evals.metrics import RAGAS_METRICS, summarize

ROOT = EVALS_DIR.parent
VARIANTS = ["vector"]  # Day 14 adds hybrid and hybrid_semantic


async def generate(items: list[Any], k: int, concurrency: int) -> list[Sample]:
    # Warm up first: the first call waits for an Entra token (seconds with the
    # Azure CLI credential), which would otherwise land in retrieval latency.
    await search_many(["warm-up"], [], k=1)
    gate = asyncio.Semaphore(concurrency)

    async def one(item: Any) -> Sample:
        async with gate:
            sample = await answer_item(item, k)
            flag = (
                "abstained" if sample.abstained else f"{len(sample.citations)} cite(s)"
            )
            print(f"  {item.id} [{item.category}] {flag}", file=sys.stderr, flush=True)
            return sample

    try:
        return list(await asyncio.gather(*(one(i) for i in items)))
    finally:
        await close_async_clients()


def score_with_ragas(
    samples: list[Sample], metrics: list[str], judge_model: str
) -> dict[str, Any]:
    """Run evals/ragas/score.py in its own environment (RAGAS needs openai<3)."""
    s = get_settings()
    rows = [
        {
            "id": x.id,
            "user_input": x.question,
            "retrieved_contexts": x.contexts,
            "response": x.answer,
            "reference": x.ground_truth,
        }
        for x in samples
        if x.answerable  # RAGAS scores need a real answer to compare against
    ]
    with tempfile.TemporaryDirectory() as tmp:
        inp, out = Path(tmp, "dataset.jsonl"), Path(tmp, "scores.json")
        inp.write_text("\n".join(json.dumps(r) for r in rows))
        parent_env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        env = {
            **parent_env,
            "AZURE_OPENAI_ENDPOINT": s.azure_openai_endpoint,
            "AZURE_OPENAI_API_VERSION": s.azure_openai_api_version,
            "AZURE_OPENAI_CHAT_DEPLOYMENT": s.azure_openai_chat_deployment,
            "AZURE_OPENAI_EMBED_DEPLOYMENT": s.azure_openai_embed_deployment,
            "EVAL_JUDGE_MODEL": judge_model,
        }
        subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(EVALS_DIR / "ragas"),
                "python",
                str(EVALS_DIR / "ragas" / "score.py"),
                "--in",
                str(inp),
                "--out",
                str(out),
                "--metrics",
                ",".join(metrics),
            ],
            env=env,
            check=True,
        )
        result: dict[str, Any] = json.loads(out.read_text())
        return result


def git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def fmt(value: Any, pct: bool = False) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1%}" if pct else f"{value:.3f}"
    return str(value)


def markdown(run: dict[str, Any]) -> str:
    m, meta = run["summary"], run["meta"]
    lines = [
        f"# Eval: {meta['name']}",
        "",
        (
            f"- **When:** {meta['timestamp']} · **commit:** `{meta['git_commit']}` · "
            f"**variant:** `{meta['variant']}` · **top-k:** {meta['top_k']} · "
            f"**index:** `{meta['search_index']}`"
        ),
        (
            f"- **Answer model:** `{meta['chat_deployment']}` · **embeddings:** "
            f"`{meta['embed_deployment']}` · **judge:** {meta['judge_model']} via "
            f"`{meta['chat_deployment']}` (RAGAS {meta['ragas_version']})"
        ),
        (
            f"- **Questions:** {m['n']} ({m['n_answerable']} answerable, "
            f"{m['n_unanswerable']} unanswerable)"
        ),
        "",
        "| metric | value |",
        "|---|---|",
    ]
    rows = [
        ("context precision", fmt(m["context_precision"])),
        ("context recall", fmt(m["context_recall"])),
        ("faithfulness", fmt(m["faithfulness"])),
        ("answer relevancy", fmt(m["answer_relevancy"])),
        ("citation validity", fmt(m["citation_validity"], pct=True)),
        ("abstention (unanswerable)", fmt(m["abstention_rate"], pct=True)),
        ("false abstention (answerable)", fmt(m["false_abstention_rate"], pct=True)),
        ("expected filing retrieved", fmt(m["source_hit_rate"], pct=True)),
        (
            "expected page retrieved (page-level; a page spans chunks)",
            fmt(m["page_hit_rate"], pct=True),
        ),
        (
            "retrieval p50 / p95 (ms)",
            f"{fmt(m['retrieval_p50_ms'])} / {fmt(m['retrieval_p95_ms'])}",
        ),
        ("end-to-end p95 (ms)", fmt(m["end_to_end_p95_ms"])),
        (
            "cost / query (USD)",
            f"{m['cost_per_query_usd']:.5f}" if m["cost_per_query_usd"] else "—",
        ),
    ]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += [
        "",
        "## By category",
        "",
        "| category | n | precision | recall | faithfulness | relevancy | citation validity | abstention |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cat, r in m["by_category"].items():
        lines.append(
            f"| {cat} | {r['n']} | {fmt(r.get('context_precision'))} | "
            f"{fmt(r.get('context_recall'))} | {fmt(r.get('faithfulness'))} | "
            f"{fmt(r.get('answer_relevancy'))} | {fmt(r.get('citation_validity'), pct=True)} | "
            f"{fmt(r.get('abstention_rate'), pct=True)} |"
        )
    lines += [
        "",
        "## Per question",
        "",
        "| id | category | abstained | citations (valid/total) | faithfulness | recall |",
        "|---|---|---|---|---|---|",
    ]
    for s in run["samples"]:
        sc = run["ragas"].get(s["id"], {})
        lines.append(
            f"| {s['id']} | {s['category']} | {'yes' if s['abstained'] else 'no'} | "
            f"{len(s['valid_citations'])}/{len(s['citations'])} | "
            f"{fmt(sc.get('faithfulness'))} | {fmt(sc.get('context_recall'))} |"
        )
    if run["ragas_errors"]:
        lines += ["", f"RAGAS errors: {len(run['ragas_errors'])} (see JSON)."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--variant", choices=VARIANTS, default="vector")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="questions in flight; >1 is faster but skews latency figures",
    )
    parser.add_argument(
        "--name", help="output file stem (default: <variant>-<timestamp>)"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    parser.add_argument(
        "--judge-model",
        default=os.environ.get("EVAL_JUDGE_MODEL", "gpt-5-mini"),
        help="model family behind the chat deployment (RAGAS maps reasoning-model params by name)",
    )
    args = parser.parse_args()
    configure_logging()

    s = get_settings()
    items = load_golden()
    thresholds = load_thresholds()
    if args.smoke:
        items = [i for i in items if i.id in set(thresholds.smoke_ids)]
    metrics = ["faithfulness"] if args.smoke else RAGAS_METRICS
    started = datetime.now(UTC)
    print(
        f"Answering {len(items)} question(s) [{args.variant}, top-{args.top_k}]...",
        file=sys.stderr,
    )
    samples = asyncio.run(generate(items, args.top_k, args.concurrency))
    print(f"Scoring with RAGAS ({', '.join(metrics)})...", file=sys.stderr)
    scored = score_with_ragas(samples, metrics, args.judge_model)
    ragas_errors, ragas_meta = scored.pop("_errors", {}), scored.pop("_meta", {})
    summary = summarize(samples, {i.id: i for i in items}, scored, load_pricing())

    name = (
        args.name
        or f"{'smoke' if args.smoke else args.variant}-{started:%Y%m%dT%H%M%SZ}"
    )
    run = {
        "meta": {
            "name": name,
            "timestamp": started.isoformat(timespec="seconds"),
            "git_commit": git_commit(),
            "variant": args.variant,
            "top_k": args.top_k,
            "concurrency": args.concurrency,
            "search_index": s.azure_search_index,
            "chat_deployment": s.azure_openai_chat_deployment,
            "embed_deployment": s.azure_openai_embed_deployment,
            "api_version": s.azure_openai_api_version,
            "judge_model": args.judge_model,
            "ragas_version": ragas_meta.get("ragas_version"),
            "metrics": metrics,
            "smoke": args.smoke,
            "pricing": load_pricing(),
        },
        "summary": summary,
        "ragas": scored,
        "ragas_errors": ragas_errors,
        "samples": [x.model_dump() for x in samples],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{name}.json").write_text(json.dumps(run, indent=2, default=str))
    (args.out / f"{name}.md").write_text(markdown(run))
    print(markdown(run))
    print(f"Wrote {args.out / name}.json and .md", file=sys.stderr)

    if not args.smoke:
        return 0
    failures = []
    if (summary["faithfulness"] or 0) < thresholds.faithfulness:
        failures.append(
            f"faithfulness {fmt(summary['faithfulness'])} < {thresholds.faithfulness}"
        )
    if (summary["citation_validity"] or 0) < thresholds.citation_validity:
        failures.append(
            f"citation validity {fmt(summary['citation_validity'])} < {thresholds.citation_validity}"
        )
    if failures:
        print("EVAL GATE FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("Eval gate passed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
