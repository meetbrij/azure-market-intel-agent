"""All environment access lives here."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_openai_endpoint: str
    azure_openai_api_version: str
    azure_openai_chat_deployment: str
    azure_openai_embed_deployment: str
    # Used when the primary chat deployment keeps returning 429 or timing out.
    azure_openai_chat_fallback_deployment: str | None = None

    azure_search_endpoint: str
    azure_search_index: str = "filings-v1"

    azure_keyvault_url: str

    azure_storage_account_url: str
    azure_storage_container: str = "raw-filings"
    # Immutable report archive: {reports_container}/{yyyy}/{mm}/{job_id}/
    reports_container: str = "reports"
    # Written by the ingest CLI into the filings container; read by /ops/status.
    ingestion_manifest_blob: str = "_manifest/last-ingestion.json"

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

    # Resilience: attempts per external call (exponential backoff between),
    # and a wall-clock cap per graph node.
    retry_attempts: int = 4
    retry_base_wait_s: float = 1.0
    node_timeout_s: float = 300.0

    # Chunks per sub-question (per company when several are requested).
    retrieval_top_k: int = 4

    chunk_size: int = 1000
    chunk_overlap: int = 150
    embed_batch_size: int = 64
    upload_batch_size: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env
