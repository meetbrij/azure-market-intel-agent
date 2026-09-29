"""Streamlit client for the research API — a demo harness, not a product.

    API_BASE_URL=http://localhost:8000 uv run --group ui streamlit run ui/app.py

Talks to the system only through api_client.ApiClient (HTTP). Users sign in
with Entra ID (auth code + PKCE, ui/auth.py); what they may do comes from the app
roles in their token, which the API enforces. The UI only hides what the API
would refuse anyway.
"""

import html
import os
import re
from datetime import UTC, datetime
from typing import Any

import auth
import streamlit as st
from api_client import ApiClient, ApiError

st.set_page_config(
    page_title="Market Intelligence Agent", page_icon="📑", layout="wide"
)

DEV_ROLES = ["analyst", "approver"]
ACTIVE = {"queued", "running"}
BADGE = {
    "queued": "gray",
    "running": "blue",
    "awaiting_approval": "orange",
    "completed": "green",
    "failed": "red",
}
STAGES = [  # progress list: (key, label)
    ("plan", "Plan"),
    ("evidence", "Retrieve filings + news"),
    ("compact", "Compact evidence"),
    ("approve_gate", "Reviewer approval"),
    ("write", "Write report"),
    ("critique", "Critique"),
]
PRESETS = [
    "Compare Amazon and Microsoft cloud revenue growth and recent cloud news",
    "How did Amazon's operating income change in fiscal 2025?",
    "What drove Alphabet's Google Cloud profitability in fiscal 2025?",
]


def _auth_headers() -> dict[str, str]:
    if auth.enabled():
        token = auth.silent_token(st.session_state.msal)
        return {"Authorization": f"Bearer {token}"} if token else {}
    # Dev mode: only an API with DEV_AUTH_BYPASS=true (local) accepts these.
    return {
        "X-Dev-User": st.session_state.get("dev_user") or "dev-user",
        "X-Dev-Roles": ",".join(st.session_state.get("dev_roles", DEV_ROLES)),
    }


def api() -> ApiClient:
    """One client per browser session: each carries its own user's token."""
    if "client" not in st.session_state:
        if auth.enabled():
            st.session_state.msal = auth.new_app()
        st.session_state.client = ApiClient(
            os.environ.get("API_BASE_URL", "http://api:8000"),
            auth_headers=_auth_headers,
        )
    client: ApiClient = st.session_state.client
    return client


def show_error(e: ApiError) -> None:
    if e.status_code == 0:
        st.error(f"**API unreachable.** {e.message}")
    else:
        st.error(f"**API error {e.status_code}:** {e.message}")


_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#!|])")


def md_escape(text: str) -> str:
    """Evidence titles come from web pages and filings: show them literally."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def badge(status: str) -> str:
    return f":{BADGE.get(status, 'gray')}-badge[{status.replace('_', ' ')}]"


def parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def elapsed(job: dict[str, Any]) -> str:
    end = datetime.now(UTC) if job["status"] in ACTIVE else parse_ts(job["updated_at"])
    seconds = int((end - parse_ts(job["created_at"])).total_seconds())
    return f"{seconds // 60}m {seconds % 60:02d}s"


def open_job(job_id: str) -> None:
    st.session_state.job_id = job_id


# ---------- sign-in, identity and navigation ----------


def sign_in_screen() -> None:
    st.title("📑 Market Intelligence Agent")
    params = st.query_params.to_dict()
    if "code" in params or "error" in params:  # back from Microsoft
        error = auth.finish_sign_in(st.session_state.msal, params)
        st.query_params.clear()  # the code is single-use; drop it from the URL
        if error is None:
            st.rerun()
        st.error(f"Sign-in failed: {error}")
    st.write("Sign in with your organisation account to continue.")
    if "sign_in_url" not in st.session_state:
        st.session_state.sign_in_url = auth.begin_sign_in(st.session_state.msal)
    # Same tab, so Microsoft redirects back into this app. The URL is built
    # by MSAL for our own registration (not user content), hence the HTML.
    url = html.escape(st.session_state.sign_in_url, quote=True)
    st.markdown(
        f'<a href="{url}" target="_self" style="display:inline-block;'
        "padding:0.5rem 1rem;border-radius:0.5rem;background:#2f6fde;"
        'color:white;text-decoration:none;font-weight:600">Sign in with Microsoft</a>',
        unsafe_allow_html=True,
    )


api()  # creates this session's client (and MSAL app)
if auth.enabled() and auth.silent_token(st.session_state.msal) is None:
    sign_in_screen()
    st.stop()

if not auth.enabled():
    st.sidebar.warning(
        "**Dev mode:** Entra sign-in is not configured. Identity comes from the "
        "fields below and works only against an API with DEV_AUTH_BYPASS=true."
    )
    st.sidebar.text_input("Dev user", value="dev-user", key="dev_user")
    st.sidebar.multiselect("Dev roles", DEV_ROLES, default=DEV_ROLES, key="dev_roles")

try:
    ME = api().me()
except ApiError as e:
    show_error(e)
    st.stop()
ROLES = set(ME["roles"])
IS_APPROVER = "approver" in ROLES
st.sidebar.markdown(f"Signed in as **{md_escape(ME['name'])}**")
st.sidebar.caption("Roles: " + (", ".join(sorted(ROLES)) or "none"))
if auth.enabled() and st.sidebar.button("Sign out"):
    auth.sign_out(st.session_state.msal)
    st.session_state.clear()
    st.rerun()
if not ROLES & set(DEV_ROLES):
    st.warning(
        "Your account has no app role for this service (analyst or approver). "
        "Ask an administrator to assign one."
    )
    st.stop()

pages = ["Submit", "Jobs"] + (["Operations"] if IS_APPROVER else [])
current = st.session_state.get("page", "Submit")
page = st.sidebar.radio(
    "Go to", pages, index=pages.index(current) if current in pages else 0
)
if page != current:  # navigating away leaves the job view
    st.session_state.pop("job_id", None)
st.session_state.page = page
st.sidebar.divider()
st.sidebar.caption(f"API: `{api().base_url}`")


# ---------- screens ----------


def submit_screen() -> None:
    st.header("Submit research")
    st.caption("Examples:")
    cols = st.columns(len(PRESETS))
    for col, preset in zip(cols, PRESETS, strict=True):
        col.button(
            preset,
            on_click=lambda p=preset: st.session_state.update(query=p),
            use_container_width=True,
        )
    query = st.text_area("Question", key="query", height=100)
    if "companies" not in st.session_state:
        try:
            st.session_state.companies = api().companies()
        except ApiError as e:
            show_error(e)
            st.session_state.companies = []
    companies = st.multiselect(
        "Companies (optional — leave empty to let the planner choose)",
        st.session_state.companies,
    )
    if st.button("Submit", type="primary", disabled=len(query.strip()) < 3):
        try:
            created = api().submit(query.strip(), companies)
        except ApiError as e:
            show_error(e)
            return
        open_job(created["job_id"])
        st.rerun()


def jobs_screen() -> None:
    st.header("Jobs")
    status = st.selectbox("Status", ["all", *BADGE], index=0)
    try:
        jobs = api().list_jobs(None if status == "all" else status, limit=50)
    except ApiError as e:
        show_error(e)
        return
    reviewer = IS_APPROVER
    if reviewer:  # the reviewer's queue comes first
        jobs.sort(key=lambda j: j["status"] != "awaiting_approval")
        waiting = sum(j["status"] == "awaiting_approval" for j in jobs)
        if waiting:
            st.warning(f"🔔 {waiting} job(s) awaiting your approval")
    if not jobs:
        st.info("No jobs yet.")
        return
    for job in jobs:
        c1, c2, c3, c4 = st.columns([2, 7, 2, 1])
        flag = " 🔔" if reviewer and job["status"] == "awaiting_approval" else ""
        c1.markdown(badge(job["status"]) + flag)
        title = job["subject"] or job["query"]
        c2.markdown(md_escape(title if len(title) <= 110 else title[:107] + "…"))
        when = parse_ts(job["created_at"]).strftime("%Y-%m-%d %H:%M UTC")
        by = job.get("submitted_by_name")
        c3.caption(f"{when} · {md_escape(by)}" if reviewer and by else when)
        c4.button(
            "View",
            key=f"view-{job['job_id']}",
            on_click=open_job,
            args=(job["job_id"],),
        )


def progress(job: dict[str, Any]) -> None:
    status, last = job["status"], job.get("last_node")
    keys = [k for k, _ in STAGES]
    last_key = "evidence" if last in ("retrieve_filings", "fetch_news") else last
    if status == "completed":
        done_upto = len(STAGES) - 1
    elif status == "awaiting_approval":
        done_upto = keys.index("compact")
    elif last_key == "critique":
        done_upto = -1  # still running after a critique: a new pass has begun
    else:
        done_upto = keys.index(last_key) if last_key in keys else -1
    lines = []
    for i, (key, label) in enumerate(STAGES):
        if i <= done_upto:
            mark = "✅"
        elif key == "approve_gate" and status == "awaiting_approval":
            mark = "⏸️"
        elif i == done_upto + 1 and status in ACTIVE:
            mark = "⏳"
        elif status == "failed" and i == done_upto + 1:
            mark = "❌"
        else:
            mark = "⬜"
        lines.append(f"{mark} {label}")
    st.markdown("  \n".join(lines))


def approval_panel(job: dict[str, Any]) -> None:
    request = job.get("interrupt") or {}
    st.subheader("⏸️ Plan awaiting approval")
    counts = request.get("evidence_counts", {})
    c1, c2, c3 = st.columns(3)
    c1.metric("Filing evidence", counts.get("filings", 0))
    c2.metric("News evidence", counts.get("news", 0))
    c3.metric("Pass", request.get("pass", "?"))
    st.markdown(f"**Subject:** {md_escape(request.get('subject', '—'))}")
    st.markdown(
        f"**Companies:** {', '.join(request.get('companies', [])) or 'any'} · "
        f"**Live news requested:** {'yes' if request.get('needs_live_news') else 'no'}"
    )
    st.markdown("**Sub-questions:**")
    for i, q in enumerate(request.get("sub_questions", []), 1):
        st.markdown(f"{i}. {md_escape(q)}")
    for source in request.get("degraded", []):
        st.warning(f"Source unavailable for this plan: **{source}**")

    if request.get("final_pass"):
        st.info("Final pass: rejecting ends the job instead of re-planning.")
    if not IS_APPROVER:
        st.info("Awaiting reviewer approval.")
        return
    if job.get("submitted_by") == ME["oid"]:
        st.info("You submitted this job, so another approver must decide it.")
        return
    job_id, pass_no = job["job_id"], request.get("pass")
    notes = st.text_area(
        "Notes (required to reject; the planner revises the plan using them)",
        key=f"notes-{job_id}-{pass_no}",
    )
    a, r, _ = st.columns([1, 1, 4])
    try:
        if a.button("Approve", type="primary", key=f"approve-{job_id}-{pass_no}"):
            api().resume(job_id, approved=True, expected_pass=pass_no)
            st.rerun()
        if r.button(
            "Reject", disabled=not notes.strip(), key=f"reject-{job_id}-{pass_no}"
        ):
            api().resume(
                job_id, approved=False, notes=notes.strip(), expected_pass=pass_no
            )
            st.rerun()
    except ApiError as e:
        show_error(e)


def report_tab(job: dict[str, Any]) -> None:
    report = job.get("report")
    if not report:
        st.info("No report yet." if job["status"] != "failed" else "The job failed.")
        return
    if report.get("data_gaps"):
        st.warning(
            "**Data gaps — this report could not use:** "
            + "; ".join(report["data_gaps"])
        )
    cache_key = f"report-md-{job['job_id']}"
    if cache_key not in st.session_state:
        try:
            st.session_state[cache_key] = api().report_markdown(job["job_id"])
        except ApiError as e:
            show_error(e)
            return
    markdown, source = st.session_state[cache_key]
    if source == "archive":
        st.caption(f"Archived at `reports/{job.get('archive_prefix')}`")
    else:
        st.caption("⚠️ Not found in the archive; rendered from the job record.")
    st.download_button(
        "Download report (Markdown)",
        markdown,
        file_name=f"report-{job['job_id']}.md",
        mime="text/markdown",
    )
    st.markdown(markdown)


def plan_tab(job: dict[str, Any]) -> None:
    plan = job.get("plan")
    if not plan:
        st.info("No plan yet.")
        return
    st.markdown(f"**Subject:** {md_escape(plan['subject'])}")
    st.markdown(
        f"**Companies:** {', '.join(plan['companies']) or 'any'} · "
        f"**Live news requested:** {'yes' if plan['needs_live_news'] else 'no'} · "
        f"**Passes:** {job.get('loop_count', 0)}"
    )
    for i, q in enumerate(plan["sub_questions"], 1):
        st.markdown(f"{i}. {md_escape(q)}")


def evidence_tab(job: dict[str, Any]) -> None:
    filings, news = job.get("filing_evidence", []), job.get("news_evidence", [])
    with st.expander(f"Filing evidence ({len(filings)})", expanded=bool(filings)):
        for e in filings:
            st.markdown(f"**{md_escape(e['title'])}** · `{e['reference']}`")
            st.text(e["snippet"])  # plain text: never rendered as Markdown
    with st.expander(f"News evidence ({len(news)})", expanded=bool(news)):
        if "news" in job.get("degraded", []):
            st.warning("Live news was unavailable for this job.")
        for e in news:
            url = e["reference"].replace("(", "%28").replace(")", "%29")
            title = md_escape(e["title"])
            st.markdown(f"**[{title}]({url})** · {e.get('period') or ''}")
            st.text(e["snippet"])  # untrusted web content: plain text only


def critique_tab(job: dict[str, Any]) -> None:
    critique = job.get("critique")
    if not critique:
        st.info("No critique yet.")
        return
    if critique["is_complete"]:
        st.success("The critic judged the report complete.")
    else:
        st.warning("The critic found gaps (the loop is capped at two passes).")
    st.markdown("**Missing**")
    # The critic read web content: its text is shown literally, never as links.
    missing = critique["missing"]
    st.markdown("\n".join(f"- {md_escape(m)}" for m in missing) or "_none_")
    st.markdown("**Citation problems**")
    problems = critique["citation_problems"]
    st.markdown("\n".join(f"- {md_escape(p)}" for p in problems) or "_none_")


def job_screen(job_id: str) -> None:
    st.button("← Back to jobs", on_click=lambda: st.session_state.pop("job_id", None))
    try:
        job = api().get_job(job_id)
    except ApiError as e:
        show_error(e)
        return
    polling = job["status"] in ACTIVE

    @st.fragment(run_every=2 if polling else None)
    def live() -> None:
        try:
            current = api().get_job(job_id)
        except ApiError as e:
            show_error(e)
            return
        if polling and current["status"] not in ACTIVE:
            st.rerun()  # settled: re-render the whole page without polling
        render_job(current)

    live()


def render_job(job: dict[str, Any]) -> None:
    st.header(job.get("subject") or job["query"])
    if job.get("subject"):
        st.caption(job["query"])
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"**Status**  \n{badge(job['status'])}")
    c2.metric("Elapsed", elapsed(job))
    c3.metric("Current node", job.get("last_node") or "—")
    c4.metric("Pass", job.get("loop_count") or "—")
    for source in job.get("degraded", []):
        st.warning(f"Source unavailable: **{source}** — see data gaps in the report.")
    if job.get("error"):
        st.error(job["error"])
    left, right = st.columns([1, 3])
    with left:
        st.markdown("**Progress**")
        progress(job)
    with right:
        if job["status"] == "awaiting_approval":
            approval_panel(job)
    tabs = st.tabs(["Report", "Plan", "Evidence", "Critique", "Raw"])
    with tabs[0]:
        report_tab(job)
    with tabs[1]:
        plan_tab(job)
    with tabs[2]:
        evidence_tab(job)
    with tabs[3]:
        critique_tab(job)
    with tabs[4], st.expander("Full job JSON"):
        st.json(job)


def operations_screen() -> None:
    st.header("Operations")
    try:
        ops = api().ops_status()
    except ApiError as e:
        show_error(e)
        return
    for problem in ops.get("errors", []):
        st.warning(f"Could not fetch {problem}")
    ingestion = ops.get("last_ingestion") or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("Search index", ops["index_name"])
    c2.metric(
        "Indexed chunks",
        ops.get("document_count") if ops.get("document_count") is not None else "?",
    )
    c3.metric(
        "Last ingestion",
        parse_ts(ingestion["ingested_at"]).strftime("%Y-%m-%d %H:%M UTC")
        if ingestion.get("ingested_at")
        else "unknown",
    )
    if ingestion:
        st.caption(
            f"{ingestion.get('documents')} filing(s), {ingestion.get('chunks')} chunks, "
            f"embedding deployment `{ingestion.get('embedding_deployment')}`"
        )
    st.subheader("Jobs by status")
    counts = ops.get("jobs_by_status", {})
    cols = st.columns(len(BADGE))
    for col, status in zip(cols, BADGE, strict=True):
        col.metric(status.replace("_", " "), counts.get(status, 0))
    st.subheader("Currently running")
    running = ops.get("running_jobs", [])
    if not running:
        st.caption("None.")
    for job in running:
        c1, c2, c3 = st.columns([6, 3, 1])
        c1.markdown(md_escape(job["subject"] or job["query"]))
        c2.caption(
            f"last node `{job.get('last_node')}` · updated {job['updated_at'][:19]}"
        )
        c3.button(
            "View",
            key=f"ops-view-{job['job_id']}",
            on_click=open_job,
            args=(job["job_id"],),
        )
    st.caption(f"Reports archive container: `{ops.get('reports_container')}`")


if st.session_state.get("job_id"):
    job_screen(st.session_state.job_id)
elif page == "Submit":
    submit_screen()
elif page == "Jobs":
    jobs_screen()
elif page == "Operations" and IS_APPROVER:
    operations_screen()
