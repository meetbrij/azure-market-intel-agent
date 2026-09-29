"""The UI's sign-in flow bookkeeping (auth code + PKCE): the pending flow
survives the redirect into a new session, and is used exactly once."""

from typing import Any

from ui import auth


class FakeMsal:
    def __init__(self) -> None:
        self.redeemed: list[tuple[dict[str, Any], dict[str, str]]] = []

    def initiate_auth_code_flow(self, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
        return {"state": "s1", "auth_uri": "https://login.test/authorize?state=s1",
                "code_verifier": "v", **kwargs}

    def acquire_token_by_auth_code_flow(
        self, flow: dict[str, Any], params: dict[str, str]
    ) -> dict[str, Any]:
        self.redeemed.append((flow, params))
        return {"access_token": "t"}


def test_flow_started_in_one_session_completes_in_another() -> None:
    url = auth.begin_sign_in(FakeMsal())  # type: ignore[arg-type]
    after_redirect = FakeMsal()  # Streamlit's new session has a new MSAL app

    error = auth.finish_sign_in(after_redirect, {"state": "s1", "code": "c"})  # type: ignore[arg-type]

    assert url.startswith("https://login.test/") and error is None
    [(flow, params)] = after_redirect.redeemed
    assert flow["code_verifier"] == "v" and params["code"] == "c"


def test_a_code_cannot_be_replayed_or_forged() -> None:
    auth.begin_sign_in(FakeMsal())  # type: ignore[arg-type]
    app = FakeMsal()
    assert auth.finish_sign_in(app, {"state": "s1", "code": "c"}) is None  # type: ignore[arg-type]

    replay = auth.finish_sign_in(app, {"state": "s1", "code": "c"})  # type: ignore[arg-type]
    forged = auth.finish_sign_in(app, {"state": "made-up", "code": "c"})  # type: ignore[arg-type]

    assert replay and "expired or was already used" in replay
    assert forged and "expired or was already used" in forged
    assert len(app.redeemed) == 1


def test_errors_from_microsoft_are_shown() -> None:
    error = auth.finish_sign_in(
        FakeMsal(),  # type: ignore[arg-type]
        {"error": "access_denied", "error_description": "AADSTS50105: not assigned"},
    )
    assert error == "AADSTS50105: not assigned"
