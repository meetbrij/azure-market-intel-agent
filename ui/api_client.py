"""The UI's only way to reach the system: a thin wrapper over the HTTP API.

The UI never imports the graph or job models and never touches Postgres.
"""

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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url, timeout=timeout, transport=transport
        )

    def _headers(self) -> dict[str, str]:
        # Day 15: add {"Authorization": f"Bearer {token}"} here (MSAL device code).
        return {"Accept": "application/json"}

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
        self, job_id: str, approved: bool, notes: str | None = None
    ) -> dict[str, Any]:
        body = {"approved": approved, "notes": notes}
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
