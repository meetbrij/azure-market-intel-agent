"""Print an access token for the research API (device code sign-in), for curl.

    export TOKEN=$(uv run --group ui python scripts/get_token.py)
    curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/me

Uses the UI's public client registration (AUTH_TENANT_ID, AUTH_UI_CLIENT_ID,
AUTH_API_CLIENT_ID from .env). The sign-in prompt goes to stderr, the token
to stdout. Nothing is cached on disk.
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
    app = auth.new_app()
    flow = auth.start_device_flow(app)
    print(flow["message"], file=sys.stderr)
    error = auth.finish_device_flow(app, flow)
    token = auth.silent_token(app)
    if error or token is None:
        print(f"Sign-in failed: {error}", file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
