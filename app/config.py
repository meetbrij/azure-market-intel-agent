"""All environment access lives here."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_openai_endpoint: str
    azure_openai_api_version: str
    azure_openai_chat_deployment: str
    azure_openai_embed_deployment: str

    azure_search_endpoint: str
    azure_search_index: str = "filings-v1"

    azure_keyvault_url: str

    azure_storage_account_url: str
    azure_storage_container: str = "raw-filings"

    langfuse_host: str = "https://cloud.langfuse.com"

    # Local defaults; docker-compose overrides the hostnames.
    database_url: str = "postgresql+asyncpg://mia:mia@localhost:5432/mia"
    redis_url: str = "redis://localhost:6379/0"

    # News MCP server: unset URL = spawn the local stdio server (mcp_news);
    # set it (e.g. http://news:8001/mcp) to use a streamable-HTTP deployment.
    news_enabled: bool = True
    news_mcp_url: str | None = None

    # Pause before `write` for human approval (interrupt). Checkpoints live in
    # the jobs database, in their own schema.
    approval_required: bool = True
    checkpoint_schema: str = "langgraph"

    # Chunks per sub-question (per company when several are requested).
    retrieval_top_k: int = 4

    chunk_size: int = 1000
    chunk_overlap: int = 150
    embed_batch_size: int = 64
    upload_batch_size: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env
