"""Keyless Postgres (DATABASE_AUTH=entra): a fresh Entra token is the
password for every new connection, for both drivers."""

import pytest

from app.config import get_settings
from app.graph import checkpoint
from app.jobs import store

AZURE_URL = (
    "postgresql+asyncpg://id-mia-db@psql-mia.postgres.database.azure.com:5432/mia"
    "?ssl=require"
)


@pytest.fixture
def entra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_AUTH", "entra")
    monkeypatch.setenv("DATABASE_URL", AZURE_URL)
    get_settings.cache_clear()
    store.get_engine.cache_clear()
    tokens = iter(["token-1", "token-2"])

    async def fake_token() -> str:
        return next(tokens)

    monkeypatch.setattr(checkpoint, "pg_token", fake_token)
    monkeypatch.setattr(store, "pg_token", fake_token)


def test_checkpoint_conninfo_is_libpq_form_without_password(entra: None) -> None:
    conninfo = checkpoint.checkpoint_conninfo()
    assert conninfo.startswith("postgresql://id-mia-db@psql-mia.postgres.database.azure.com")
    assert "sslmode=require" in conninfo and "ssl=require" not in conninfo.replace("sslmode", "")
    assert ":" not in conninfo.split("//")[1].split("@")[0]  # no password in the URL


async def test_each_checkpoint_connection_gets_a_fresh_token(entra: None) -> None:
    assert await checkpoint._password_kwargs() == {"password": "token-1"}
    assert await checkpoint._password_kwargs() == {"password": "token-2"}


def test_sqlalchemy_engine_passes_the_token_callable(
    entra: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake_engine(url: str, **kwargs: object) -> object:
        seen.update(url=url, **kwargs)
        return object()

    monkeypatch.setattr(store, "create_async_engine", fake_engine)
    store.get_engine()

    # asyncpg awaits a callable `password` for every new connection.
    assert seen["connect_args"] == {"password": store.pg_token}
    assert seen["url"] == AZURE_URL


def test_password_mode_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_AUTH", "password")
    get_settings.cache_clear()
    import asyncio

    assert asyncio.run(checkpoint._password_kwargs()) == {}
