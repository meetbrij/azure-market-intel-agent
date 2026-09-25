import pytest
from pydantic import ValidationError

from app.graph.state import Report
from tests.conftest import make_citation

SAMPLE = {
    "subject": "Amazon operating income",
    "summary": "Operating income rose from $68.6B in 2024 to $80.0B in 2025.",
    "sections": [
        {
            "heading": "Consolidated operating income",
            "body": "Operating income increased year over year.",
            "citations": [
                {
                    "company": "Amazon",
                    "doc_type": "10-K",
                    "period": "2025-12-31",
                    "source_blob": "amzn_annual_report_10k.pdf",
                    "chunk_no": 157,
                    "page": 27,
                    "quote": "Operating income was $68.6 billion and $80.0 billion.",
                    "source_type": "filing",
                    "reference": "amzn-annual-report-10k-157",
                }
            ],
        }
    ],
}


def test_sample_report_validates() -> None:
    report = Report.model_validate(SAMPLE)
    assert report.sections[0].citations[0].page == 27


def test_report_requires_citation_fields() -> None:
    bad = {**SAMPLE, "sections": [{**SAMPLE["sections"][0], "citations": [{}]}]}  # type: ignore[dict-item]
    with pytest.raises(ValidationError):
        Report.model_validate(bad)


def test_long_quote_is_trimmed_to_25_words() -> None:
    citation = make_citation(quote=" ".join(f"w{i}" for i in range(40)))
    assert len(citation.quote.rstrip("…").split()) == 25


def test_news_citation_needs_no_filing_fields() -> None:
    citation = make_citation(
        source_type="news",
        reference="https://example.com/a",
        company=None,
        doc_type=None,
        source_blob=None,
        chunk_no=None,
        page=None,
    )
    assert citation.page is None
