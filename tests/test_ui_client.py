"""The Streamlit app's HTTP client: errors must surface, never vanish."""

import httpx
import pytest

from ui.api_client import ApiClient, ApiError


def client_for(handler: object) -> ApiClient:
    return ApiClient("http://api.test", transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_api_errors_carry_status_and_detail() -> None:
    client = client_for(
        lambda r: httpx.Response(404, json={"detail": "Job x not found"})
    )
    with pytest.raises(ApiError) as err:
        client.get_job("x")
    assert (err.value.status_code, err.value.message) == (404, "Job x not found")


def test_non_json_error_bodies_are_kept() -> None:
    client = client_for(lambda r: httpx.Response(502, text="Bad Gateway from proxy"))
    with pytest.raises(ApiError) as err:
        client.list_jobs()
    assert err.value.message == "Bad Gateway from proxy"


def test_unreachable_api_is_status_0() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ApiError) as err:
        client_for(refuse).ops_status()
    assert err.value.status_code == 0
    assert "http://api.test" in err.value.message


def test_requests_go_through_one_header_function() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"job_id": "j1", "status": "queued"})

    client = client_for(record)
    client.submit("q", ["Amazon"])
    client.resume("j1", approved=False, notes="narrow it")

    assert all(r.headers["accept"] == "application/json" for r in seen)
    assert seen[1].url.path == "/api/v1/research/j1/resume"
    assert seen[1].content == b'{"approved":false,"notes":"narrow it"}'


def test_report_markdown_returns_source() -> None:
    client = client_for(
        lambda r: httpx.Response(
            200, text="# R", headers={"x-report-source": "archive"}
        )
    )
    assert client.report_markdown("j1") == ("# R", "archive")


def test_list_jobs_passes_filters() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    client_for(record).list_jobs(status="awaiting_approval", limit=10)
    assert dict(seen[0].url.params) == {"limit": "10", "status": "awaiting_approval"}
