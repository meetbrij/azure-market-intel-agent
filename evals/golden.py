"""Golden set and thresholds: loading and validation."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

EVALS_DIR = Path(__file__).resolve().parent


class GoldenItem(BaseModel):
    id: str
    question: str
    ground_truth: str
    expected_sources: list[str] = []
    # One source: a list of pages. Several: {source: [pages]}, each needed.
    expected_pages: list[int] | dict[str, list[int]] = []
    companies: list[str] = []
    category: Literal["factual", "comparative", "temporal", "unanswerable"]
    answerable: bool

    @model_validator(mode="after")
    def _consistent(self) -> "GoldenItem":
        if self.answerable == (self.category == "unanswerable"):
            raise ValueError(
                f"{self.id}: answerable must be false exactly for 'unanswerable'"
            )
        if self.answerable and not self.expected_sources:
            raise ValueError(f"{self.id}: answerable questions need expected_sources")
        if isinstance(self.expected_pages, list) and len(self.expected_sources) > 1:
            raise ValueError(f"{self.id}: map expected_pages to each expected source")
        if isinstance(self.expected_pages, dict) and set(self.expected_pages) != set(
            self.expected_sources
        ):
            raise ValueError(f"{self.id}: expected_pages keys must be expected_sources")
        return self

    def pages_by_source(self) -> dict[str, list[int]]:
        if isinstance(self.expected_pages, dict):
            return self.expected_pages
        return {src: self.expected_pages for src in self.expected_sources}


class Thresholds(BaseModel):
    smoke_ids: list[str]
    faithfulness: float
    citation_validity: float
    abstention_rate: float


def load_golden(path: Path = EVALS_DIR / "golden_set.yaml") -> list[GoldenItem]:
    items = [GoldenItem.model_validate(x) for x in yaml.safe_load(path.read_text())]
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate ids in golden set")
    return items


def load_thresholds(path: Path = EVALS_DIR / "thresholds.yaml") -> Thresholds:
    return Thresholds.model_validate(yaml.safe_load(path.read_text()))


def load_pricing(
    path: Path = EVALS_DIR / "pricing.yaml",
) -> dict[str, dict[str, float | str]]:
    data: dict[str, dict[str, float | str]] = yaml.safe_load(path.read_text())
    return data
