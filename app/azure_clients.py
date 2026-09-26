"""Cached, keyless Azure client factories. Patterns mirror scripts/smoke_test.py.

Async clients bind to the event loop that first uses them, so each process
should run a single loop (the CLI's asyncio.run, the API, or the arq worker).
"""

from functools import lru_cache

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from azure.identity.aio import (
    get_bearer_token_provider as get_async_bearer_token_provider,
)
from azure.keyvault.secrets import SecretClient
from azure.search.documents import SearchClient
from azure.search.documents.aio import SearchClient as AsyncSearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.storage.blob import ContainerClient
from azure.storage.blob.aio import ContainerClient as AsyncContainerClient
from openai import AsyncAzureOpenAI, AzureOpenAI

from app.config import get_settings

AOAI_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache
def get_credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


@lru_cache
def get_aoai() -> AzureOpenAI:
    s = get_settings()
    token_provider = get_bearer_token_provider(get_credential(), AOAI_SCOPE)
    return AzureOpenAI(
        azure_endpoint=s.azure_openai_endpoint,
        api_version=s.azure_openai_api_version,
        azure_ad_token_provider=token_provider,
        max_retries=0,  # retries are handled explicitly by callers
    )


@lru_cache
def get_search_index_client() -> SearchIndexClient:
    return SearchIndexClient(get_settings().azure_search_endpoint, get_credential())


@lru_cache
def get_search_client() -> SearchClient:
    s = get_settings()
    return SearchClient(s.azure_search_endpoint, s.azure_search_index, get_credential())


@lru_cache
def get_container_client() -> ContainerClient:
    s = get_settings()
    return ContainerClient(
        s.azure_storage_account_url,
        s.azure_storage_container,
        credential=get_credential(),
    )


@lru_cache
def get_secret_client() -> SecretClient:
    return SecretClient(
        vault_url=get_settings().azure_keyvault_url, credential=get_credential()
    )


# ---------- async (graph runtime) ----------


@lru_cache
def get_async_credential() -> AsyncDefaultAzureCredential:
    return AsyncDefaultAzureCredential(exclude_interactive_browser_credential=True)


@lru_cache
def get_async_aoai() -> AsyncAzureOpenAI:
    s = get_settings()
    return AsyncAzureOpenAI(
        azure_endpoint=s.azure_openai_endpoint,
        api_version=s.azure_openai_api_version,
        azure_ad_token_provider=get_async_bearer_token_provider(
            get_async_credential(), AOAI_SCOPE
        ),
        max_retries=0,  # retries live in app/resilience.py (one layer)
    )


@lru_cache
def get_async_search_client() -> AsyncSearchClient:
    s = get_settings()
    return AsyncSearchClient(
        s.azure_search_endpoint,
        s.azure_search_index,
        get_async_credential(),
        retry_total=0,  # retries live in app/resilience.py (one layer)
    )


_async_containers: dict[str, AsyncContainerClient] = {}


def get_async_container_client(container: str) -> AsyncContainerClient:
    if container not in _async_containers:
        _async_containers[container] = AsyncContainerClient(
            get_settings().azure_storage_account_url,
            container,
            credential=get_async_credential(),
        )
    return _async_containers[container]


async def close_async_clients() -> None:
    """Close cached async clients (call on process shutdown)."""
    while _async_containers:
        await _async_containers.popitem()[1].close()
    if get_async_search_client.cache_info().currsize:
        await get_async_search_client().close()
    if get_async_aoai.cache_info().currsize:
        await get_async_aoai().close()
    if get_async_credential.cache_info().currsize:
        await get_async_credential().close()
