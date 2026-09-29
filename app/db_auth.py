"""Keyless Postgres: an Entra ID access token as the password.

Azure Database for PostgreSQL accepts a token for the OSS RDBMS scope in place
of a password, for a role created with `pgaadauth_create_principal`. Both
drivers take a callable, so every new connection gets a fresh token and no
password exists anywhere:

    asyncpg (SQLAlchemy, jobs/audit)   connect_args={"password": <async fn>}
    psycopg (LangGraph checkpointer)   pool kwargs = <async fn returning kwargs>

DATABASE_AUTH=entra switches it on. Locally, DefaultAzureCredential resolves to
your `az login`; in Azure, DATABASE_CLIENT_ID picks the managed identity that
owns the database role (api and worker share it, so either may create/alter
the tables).
"""

from functools import lru_cache

from azure.identity.aio import DefaultAzureCredential

from app.config import get_settings

PG_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def entra_enabled() -> bool:
    return get_settings().database_auth == "entra"


@lru_cache
def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(
        managed_identity_client_id=get_settings().database_client_id,
        exclude_interactive_browser_credential=True,
    )


async def pg_token() -> str:
    """A token for Postgres; azure-identity caches it until close to expiry."""
    return (await _credential().get_token(PG_SCOPE)).token


async def close_credential() -> None:
    if _credential.cache_info().currsize:
        await _credential().close()
        _credential.cache_clear()
