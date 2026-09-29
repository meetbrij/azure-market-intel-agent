"""Print an access token for the research API, for curl.

    export TOKEN=$(uv run --group ui python scripts/get_token.py)
    curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/me

Opens your browser to sign in (auth code + PKCE, redirected back to a
one-off http://localhost port), using the UI's public client registration
(AUTH_TENANT_ID, AUTH_UI_CLIENT_ID, AUTH_API_CLIENT_ID from .env). The token
goes to stdout. Nothing is cached on disk.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "ui"))

import auth  # after load_dotenv: ui/auth.py reads the environment at import


def main() -> int:
    if not auth.enabled():
        print(
            "Set AUTH_TENANT_ID, AUTH_UI_CLIENT_ID and AUTH_API_CLIENT_ID "
            "(see infra/entra/setup.sh)",
            file=sys.stderr,
        )
        return 1
    print("Opening your browser to sign in...", file=sys.stderr)
    result = auth.new_app().acquire_token_interactive(
        auth.scopes(), prompt="select_account"
    )
    if "access_token" not in result:
        error = result.get("error_description") or result.get("error")
        print(f"Sign-in failed: {error}", file=sys.stderr)
        return 1
    print(result["access_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
