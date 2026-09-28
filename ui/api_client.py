"""The UI's only way to reach the system: a thin wrapper over the HTTP API.

The UI never imports the graph or job models and never touches Postgres.
"""

from collections.abc import Callable
from typing import Any

import httpx


class ApiError(Exception):
    """An API call failed. status_code is 0 when the API couldn't be reached."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"{status_code}: {message}")
        self.status_code = status_code
        self.message = message


class ApiClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,  # tests inject a mock
        auth_headers: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # Called per request, so a refreshed token is always the one sent.
        self._auth_headers = auth_headers
        self._http = httpx.Client(
            base_url=self.base_url, timeout=timeout, transport=transport
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._auth_headers is not None:
            headers |= self._auth_headers()
        return headers

    def me(self) -> dict[str, Any]:
        """The caller's identity and app roles, as the API sees them."""
        result: dict[str, Any] = self._request("GET", "/api/v1/me").json()
        return result

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(
                method, path, headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as e:
            raise ApiError(0, f"Cannot reach the API at {self.base_url}: {e}") from e
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(response.status_code, str(detail) or response.reason_phrase)
        return response

    # ---------- research jobs ----------

    def submit(self, query: str, companies: list[str]) -> dict[str, Any]:
        body = {"query": query, "companies": companies}
        result: dict[str, Any] = self._request(
            "POST", "/api/v1/research", json=body
        ).json()
        return result

    def list_jobs(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        result: list[dict[str, Any]] = self._request(
            "GET", "/api/v1/research", params=params
        ).json()
        return result

    def get_job(self, job_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._request(
            "GET", f"/api/v1/research/{job_id}"
        ).json()
        return result

    def resume(
        self,
        job_id: str,
        approved: bool,
        notes: str | None = None,
        expected_pass: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"approved": approved, "notes": notes}
        if expected_pass is not None:
            body["expected_pass"] = expected_pass
        result: dict[str, Any] = self._request(
            "POST", f"/api/v1/research/{job_id}/resume", json=body
        ).json()
        return result

    def report_markdown(self, job_id: str) -> tuple[str, str]:
        """(markdown, source) where source is "archive" or "rendered"."""
        response = self._request("GET", f"/api/v1/research/{job_id}/report.md")
        return response.text, response.headers.get("x-report-source", "unknown")

    # ---------- reference data / operations ----------

    def companies(self) -> list[str]:
        result: list[str] = self._request("GET", "/api/v1/companies").json()[
            "companies"
        ]
        return result

    def ops_status(self) -> dict[str, Any]:
        result: dict[str, Any] = self._request("GET", "/api/v1/ops/status").json()
        return result
