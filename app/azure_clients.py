"""Cached, keyless Azure client factories. Patterns mirror scripts/smoke_test.py."""

from functools import lru_cache

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.keyvault.secrets import SecretClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.storage.blob import ContainerClient
from openai import AzureOpenAI

from app.config import get_settings


@lru_cache
def get_credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


@lru_cache
def get_aoai() -> AzureOpenAI:
    s = get_settings()
    token_provider = get_bearer_token_provider(
        get_credential(), "https://cognitiveservices.azure.com/.default"
    )
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
