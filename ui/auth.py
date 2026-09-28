"""Sign-in for the Streamlit client: Entra ID device code flow via MSAL.

    AUTH_TENANT_ID       the tenant
    AUTH_UI_CLIENT_ID    the UI's own app registration (a public client)
    AUTH_API_CLIENT_ID   the API's app registration; we ask for its
                         access_as_user scope, and the token carries the
                         caller's app roles

The token cache lives in the user's Streamlit session only (never on disk),
and MSAL refreshes access tokens silently until the session ends. Without
AUTH_UI_CLIENT_ID the UI runs in dev mode: it sends X-Dev-User/X-Dev-Roles
headers, which only an API started with DEV_AUTH_BYPASS=true (local) accepts.
"""

import os
from typing import Any

import msal

TENANT_ID = os.environ.get("AUTH_TENANT_ID", "")
UI_CLIENT_ID = os.environ.get("AUTH_UI_CLIENT_ID", "")
API_CLIENT_ID = os.environ.get("AUTH_API_CLIENT_ID", "")


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


def start_device_flow(app: msal.PublicClientApplication) -> dict[str, Any]:
    flow: dict[str, Any] = app.initiate_device_flow(scopes=scopes())
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description") or "Could not start sign-in")
    return flow


def finish_device_flow(
    app: msal.PublicClientApplication, flow: dict[str, Any]
) -> str | None:
    """Blocks until the user completes sign-in in their browser, or the code
    expires. Returns an error message, or None on success."""
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        return None
    return str(result.get("error_description") or result.get("error") or "Sign-in failed")


def sign_out(app: msal.PublicClientApplication) -> None:
    for account in app.get_accounts():
        app.remove_account(account)
