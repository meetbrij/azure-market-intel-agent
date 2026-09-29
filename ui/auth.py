"""Sign-in for the Streamlit client: Entra ID auth code flow with PKCE (MSAL).

    AUTH_TENANT_ID       the tenant
    AUTH_UI_CLIENT_ID    the UI's own app registration (a public client)
    AUTH_API_CLIENT_ID   the API's app registration; we ask for its
                         access_as_user scope, and the token carries the
                         caller's app roles
    AUTH_REDIRECT_URI    where Microsoft sends the browser back (default
                         http://localhost:8501; must be registered on mia-ui)

"Sign in" sends the browser to Microsoft, which redirects back here with a
one-time code; MSAL redeems it with the PKCE verifier (no client secret).
Device code sign-in isn't used: Entra security defaults block it.

The redirect lands in a new Streamlit session, so the pending flow (state,
PKCE verifier, nonce) is kept in this process, keyed by `state`, for a few
minutes and used once. Tokens live only in the user's session (never on
disk), and MSAL refreshes them silently until the session ends.

Without AUTH_UI_CLIENT_ID the UI runs in dev mode: it sends X-Dev-User and
X-Dev-Roles headers, which only an API started with DEV_AUTH_BYPASS=true
(local) accepts.
"""

import os
import threading
import time
from collections.abc import Mapping
from typing import Any

import msal

TENANT_ID = os.environ.get("AUTH_TENANT_ID", "")
UI_CLIENT_ID = os.environ.get("AUTH_UI_CLIENT_ID", "")
API_CLIENT_ID = os.environ.get("AUTH_API_CLIENT_ID", "")
REDIRECT_URI = os.environ.get("AUTH_REDIRECT_URI", "http://localhost:8501")
FLOW_TTL_S = 600

_pending: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(TENANT_ID and UI_CLIENT_ID and API_CLIENT_ID)


def scopes() -> list[str]:
    return [f"api://{API_CLIENT_ID}/access_as_user"]


def new_app() -> msal.PublicClientApplication:
    """One per browser session, with its own in-memory token cache."""
    return msal.PublicClientApplication(
        UI_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=msal.TokenCache(),
    )


def silent_token(app: msal.PublicClientApplication) -> str | None:
    """A valid access token from the session's cache (refreshed if needed),
    or None if the user must sign in."""
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(scopes(), account=accounts[0])
    return str(result["access_token"]) if result and "access_token" in result else None


def begin_sign_in(app: msal.PublicClientApplication) -> str:
    """Start an auth code + PKCE flow; returns the Microsoft sign-in URL."""
    flow: dict[str, Any] = app.initiate_auth_code_flow(
        scopes(), redirect_uri=REDIRECT_URI, prompt="select_account"
    )
    now = time.monotonic()
    with _lock:
        for state in [s for s, (at, _) in _pending.items() if now - at > FLOW_TTL_S]:
            del _pending[state]
        _pending[flow["state"]] = (now, flow)
    return str(flow["auth_uri"])


def finish_sign_in(
    app: msal.PublicClientApplication, params: Mapping[str, str]
) -> str | None:
    """Redeem the redirect's `code` for tokens (into `app`'s cache). Returns
    an error message, or None on success. Each pending flow is used once."""
    if "error" in params:
        return params.get("error_description") or params["error"]
    with _lock:
        entry = _pending.pop(params.get("state", ""), None)
    if entry is None or time.monotonic() - entry[0] > FLOW_TTL_S:
        return "This sign-in link has expired or was already used. Please sign in again."
    result = app.acquire_token_by_auth_code_flow(entry[1], dict(params))
    if "access_token" in result:
        return None
    return str(result.get("error_description") or result.get("error") or "Sign-in failed")


def sign_out(app: msal.PublicClientApplication) -> None:
    for account in app.get_accounts():
        app.remove_account(account)
