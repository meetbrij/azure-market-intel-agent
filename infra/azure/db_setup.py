"""One-off: the Postgres role the apps log in as (keyless).

    uv run python infra/azure/db_setup.py --host <server>.postgres.database.azure.com \
        --admin <your Entra login> --role id-mia-db --object-id <identity principal id>

Connects as the server's Entra admin (you: a token from `az login`, no
password), creates a role mapped to the managed identity, and lets it own
what it creates in database `mia` (the jobs/audit tables and LangGraph's
checkpoint schema). Idempotent.
"""

import argparse
import asyncio
import re
import sys

import asyncpg
from azure.identity import AzureCliCredential

PG_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
_ROLE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_GUID = re.compile(r"^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$")


async def connect(host: str, admin: str, database: str) -> asyncpg.Connection:
    token = AzureCliCredential().get_token(PG_SCOPE).token
    return await asyncpg.connect(
        host=host, user=admin, password=token, database=database, ssl="require"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--admin", required=True, help="the Entra admin's login name")
    parser.add_argument("--role", required=True)
    parser.add_argument("--object-id", required=True, help="the identity's principal id")
    args = parser.parse_args()
    # Identifiers can't be bound as parameters; validate them instead.
    if not _ROLE.match(args.role) or not _GUID.match(args.object_id):
        print("invalid role name or object id", file=sys.stderr)
        return 1
    role = f'"{args.role}"'

    conn = await connect(args.host, args.admin, "postgres")
    try:
        if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", args.role):
            print(f"role {args.role} exists")
        else:
            await conn.execute(
                "SELECT * FROM pgaadauth_create_principal_with_oid($1, $2, 'service', false, false)",
                args.role,
                args.object_id,
            )
            print(f"created role {args.role} for managed identity {args.object_id}")
    finally:
        await conn.close()

    conn = await connect(args.host, args.admin, "mia")
    try:
        await conn.execute(f"GRANT ALL PRIVILEGES ON DATABASE mia TO {role}")
        # Postgres 15+ no longer lets everyone create in `public`.
        await conn.execute(f"GRANT ALL ON SCHEMA public TO {role}")
        print(f"granted {args.role}: database mia, schema public")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
