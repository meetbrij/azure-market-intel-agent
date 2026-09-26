"""RAGAS scoring, run in its own environment (evals/ragas/pyproject.toml).

RAGAS 0.4 needs openai<3 (via instructor); the app uses openai 3.x, so the
judge runs here, isolated, and evals/run.py calls it as a subprocess:

    uv run --project evals/ragas python evals/ragas/score.py \
        --in dataset.jsonl --out scores.json [--metrics faithfulness,...]

Input: one JSON object per line with id, user_input, retrieved_contexts,
response, reference. Output: {id: {metric: value | null}, "_errors": {...}}.

Configuration comes only from the environment (set by evals/run.py):
AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_CHAT_DEPLOYMENT,
AZURE_OPENAI_EMBED_DEPLOYMENT, EVAL_JUDGE_MODEL (model family, e.g.
gpt-5-mini; RAGAS maps reasoning-model parameters by name).
"""

import argparse
import asyncio
import json
import os
import sys
import warnings
from typing import Any

warnings.filterwarnings("ignore")

from azure.identity import (
    DefaultAzureCredential,
    get_bearer_token_provider,
)
from openai import AsyncAzureOpenAI
from ragas.embeddings import OpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
)

ALL_METRICS = [
    "context_precision",
    "context_recall",
    "faithfulness",
    "answer_relevancy",
]
CONCURRENCY = 4
MAX_TOKENS = 16000  # reasoning tokens count against this; RAGAS's 1024 truncates


def azure_client(deployment: str) -> AsyncAzureOpenAI:
    # azure_deployment pins the URL to the deployment, so the `model` string
    # RAGAS sends is only used for its reasoning-model parameter mapping.
    token = get_bearer_token_provider(
        DefaultAzureCredential(exclude_interactive_browser_credential=True),
        "https://cognitiveservices.azure.com/.default",
    )
    return AsyncAzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.environ["AZURE_OPENAI_API_VERSION"],
        azure_deployment=deployment,
        azure_ad_token_provider=token,
        max_retries=4,
    )


def build_metrics(names: list[str]) -> dict[str, Any]:
    judge = os.environ.get("EVAL_JUDGE_MODEL", "gpt-5-mini")
    llm = llm_factory(
        judge,
        provider="openai",
        client=azure_client(os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"]),
        max_tokens=MAX_TOKENS,
    )
    metrics: dict[str, Any] = {}
    if "context_precision" in names:
        metrics["context_precision"] = ContextPrecision(llm=llm)
    if "context_recall" in names:
        metrics["context_recall"] = ContextRecall(llm=llm)
    if "faithfulness" in names:
        metrics["faithfulness"] = Faithfulness(llm=llm)
    if "answer_relevancy" in names:
        embeddings = OpenAIEmbeddings(
            client=azure_client(os.environ["AZURE_OPENAI_EMBED_DEPLOYMENT"]),
            model=os.environ["AZURE_OPENAI_EMBED_DEPLOYMENT"],
        )
        metrics["answer_relevancy"] = AnswerRelevancy(llm=llm, embeddings=embeddings)
    return metrics


async def score_one(
    row: dict[str, Any], metrics: dict[str, Any], errors: dict[str, str]
) -> dict[str, float | None]:
    args = {
        "context_precision": {
            "user_input": row["user_input"],
            "reference": row["reference"],
            "retrieved_contexts": row["retrieved_contexts"],
        },
        "context_recall": {
            "user_input": row["user_input"],
            "retrieved_contexts": row["retrieved_contexts"],
            "reference": row["reference"],
        },
        "faithfulness": {
            "user_input": row["user_input"],
            "response": row["response"],
            "retrieved_contexts": row["retrieved_contexts"],
        },
        "answer_relevancy": {
            "user_input": row["user_input"],
            "response": row["response"],
        },
    }
    scores: dict[str, float | None] = {}
    for name, metric in metrics.items():
        try:
            result = await metric.ascore(**args[name])
            scores[name] = None if result.value is None else float(result.value)
        except Exception as e:  # noqa: BLE001 — one bad sample must not sink the run
            scores[name] = None
            errors[f"{row['id']}:{name}"] = f"{type(e).__name__}: {str(e)[:300]}"
    return scores


async def score_all(
    rows: list[dict[str, Any]], names: list[str]
) -> tuple[dict[str, Any], dict[str, str]]:
    metrics = build_metrics(names)
    errors: dict[str, str] = {}
    gate = asyncio.Semaphore(CONCURRENCY)

    async def scored(row: dict[str, Any]) -> tuple[str, dict[str, float | None]]:
        async with gate:
            return row["id"], await score_one(row, metrics, errors)

    return dict(await asyncio.gather(*(scored(r) for r in rows))), errors


def main() -> int:
    import ragas

    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--metrics", default=",".join(ALL_METRICS))
    args = parser.parse_args()
    names = [m.strip() for m in args.metrics.split(",") if m.strip()]
    unknown = set(names) - set(ALL_METRICS)
    if unknown:
        print(f"unknown metrics: {sorted(unknown)}", file=sys.stderr)
        return 2

    with open(args.inp) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    results, errors = asyncio.run(score_all(rows, names))
    meta = {"ragas_version": ragas.__version__, "metrics": names}
    with open(args.out, "w") as f:
        json.dump({**results, "_errors": errors, "_meta": meta}, f, indent=2)
    print(
        f"scored {len(rows)} sample(s), {len(errors)} metric error(s)", file=sys.stderr
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
