"""System prompts and user-message builders for the graph's LLM calls."""

from app.graph.state import Critique, Evidence, Report, ResearchPlan

PLAN_SYSTEM = """\
You plan research over SEC 10-K annual reports, optionally supplemented by \
recent news.

Produce a research plan:
- subject: a short title for the report.
- sub_questions: 3-5 specific, self-contained questions. Each is used as a \
search query against 10-K excerpts, so name the company and the metric \
(e.g. "Amazon AWS segment net sales 2024 and 2025"). One company per question.
- companies: only names from the AVAILABLE COMPANIES list.
- needs_live_news: true only if the question needs developments after the \
latest fiscal year (recent events, guidance, market reaction). Annual \
financials alone do not need news.

If a PREVIOUS CRITIQUE is given, this is a follow-up pass: write sub-questions \
ONLY for the listed gaps and citation problems, not for what is already \
covered.
"""

COMPACT_SYSTEM = """\
You condense research evidence into a brief for a report writer.

Rules:
- Organise the brief by sub-question. Under each, list the relevant facts.
- Keep numbers, units, periods and names exactly as stated. Never compute or \
infer new numbers.
- Tag every fact with its reference id in square brackets, e.g. [ref-id].
- Drop boilerplate and anything irrelevant to the sub-questions.
- Say explicitly when a sub-question has no supporting evidence.
- Stay under about 1,500 words.
"""

WRITE_SYSTEM = """\
You are a financial research analyst writing a short report from the provided \
brief and evidence.

Rules:
- Use ONLY the brief and the evidence below. No outside knowledge.
- Never invent, estimate or compute numbers that are not stated in the evidence.
- Every section must carry at least one citation.
- A citation is a reference id copied exactly from an evidence block header, \
plus a short quote (at most 25 words) copied verbatim from that same block.
- Cite news evidence where it adds recent context; filings remain the source \
for reported financials.
- If the evidence does not answer part of the question, say so explicitly \
rather than guessing. State such limitations (missing data, unavailable \
sources) in the summary; do not create a section only to report an absence.
"""

CRITIQUE_SYSTEM = """\
You review a research report against the plan it was written from.

Return:
- is_complete: true only if every sub-question is answered or explicitly \
marked as not covered by the evidence, and every section is supported by \
citations.
- missing: specific gaps a follow-up search could fill (phrase each one as \
something to look for, naming the company and metric). Do not list gaps the \
report already says the evidence cannot answer, unless a different search \
might answer them.
- citation_problems: sections whose claims are not supported by their \
citations.
Never list gaps that only an UNAVAILABLE SOURCE could fill; the report cannot \
fix those by searching again. Be strict but concise.
"""


def format_evidence(evidence: list[Evidence]) -> str:
    if not evidence:
        return "(no evidence)"
    blocks = []
    for e in evidence:
        header = f"[{e.reference}] ({e.source_type}) {e.title}"
        if e.period:
            header += f" | period {e.period}"
        blocks.append(f"{header}\n{e.snippet}")
    return "\n\n".join(blocks)


def plan_user(
    query: str,
    requested: list[str],
    available: list[str],
    previous: ResearchPlan | None,
    critique: Critique | None,
) -> str:
    parts = [
        f"QUESTION: {query}",
        f"AVAILABLE COMPANIES: {', '.join(available) or '(unknown)'}",
    ]
    if requested:
        parts.append(f"REQUESTED COMPANIES (use exactly these): {', '.join(requested)}")
    if previous and critique:
        parts.append(
            "PREVIOUS SUB-QUESTIONS:\n"
            + "\n".join(f"- {q}" for q in previous.sub_questions)
        )
        parts.append(
            "PREVIOUS CRITIQUE:\n"
            + "\n".join(f"- missing: {m}" for m in critique.missing)
            + "\n"
            + "\n".join(f"- citation problem: {p}" for p in critique.citation_problems)
        )
    return "\n\n".join(parts)


def compact_user(query: str, plan: ResearchPlan, evidence: list[Evidence]) -> str:
    return (
        f"QUESTION: {query}\n\n"
        "SUB-QUESTIONS:\n"
        + "\n".join(f"- {q}" for q in plan.sub_questions)
        + f"\n\nEVIDENCE:\n{format_evidence(evidence)}"
    )


def write_user(
    query: str, brief: str, evidence: list[Evidence], degraded: list[str]
) -> str:
    notes = ""
    if degraded:
        notes = (
            "\n\nUNAVAILABLE SOURCES: "
            + ", ".join(degraded)
            + " (mention this limitation in the summary)"
        )
    return (
        f"QUESTION: {query}{notes}\n\nBRIEF:\n{brief}\n\n"
        f"EVIDENCE:\n{format_evidence(evidence)}"
    )


def critique_user(
    query: str, plan: ResearchPlan, report: Report, degraded: list[str]
) -> str:
    unavailable = f"UNAVAILABLE SOURCES: {', '.join(degraded)}\n\n" if degraded else ""
    return (
        f"QUESTION: {query}\n\n{unavailable}"
        "SUB-QUESTIONS:\n"
        + "\n".join(f"- {q}" for q in plan.sub_questions)
        + f"\n\nREPORT:\n{report.model_dump_json(indent=1)}"
    )
