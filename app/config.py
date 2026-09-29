"""All environment access lives here."""

from functools import lru_cache
from typing import Literal

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

    # Model names behind the deployments, for tracing: Langfuse prices a call
    # by model name, and a deployment name ("chat-mini") means nothing to it.
    azure_openai_chat_model: str = "gpt-5-mini"
    azure_openai_chat_fallback_model: str | None = None
    azure_openai_embed_model: str = "text-embedding-3-small"

    # Langfuse tracing (Day 16). Keys live in Key Vault; without them (or with
    # TRACING_ENABLED=false) everything runs untraced.
    tracing_enabled: bool = True
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key_secret: str = "langfuse-public-key"
    langfuse_secret_key_secret: str = "langfuse-secret-key"

    # Entra ID auth (Day 15). The API validates v2 access tokens issued by
    # AUTH_TENANT_ID for the AUTH_API_CLIENT_ID app registration (see
    # infra/entra/setup.sh). DEV_AUTH_BYPASS skips validation and takes the
    # caller's identity from X-Dev-User / X-Dev-Roles headers; startup refuses
    # it unless ENVIRONMENT is "local".
    environment: str = "local"
    dev_auth_bypass: bool = False
    auth_tenant_id: str | None = None
    auth_api_client_id: str | None = None

    # Screen news for instruction-like content (one cheap model call per
    # fetch) before it can reach a prompt. Fails closed: no screen, no news.
    news_screen_enabled: bool = True

    # Local defaults; docker-compose overrides the hostnames.
    database_url: str = "postgresql+asyncpg://mia:mia@localhost:5432/mia"
    redis_url: str = "redis://localhost:6379/0"
    # "password": credentials in DATABASE_URL (local containers).
    # "entra": keyless; an Entra token is the password (Azure, app/db_auth.py).
    database_auth: Literal["password", "entra"] = "password"
    # Managed identity that owns the database role (Azure); unset = default.
    database_client_id: str | None = None

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

    # How chunks are retrieved: vector only, hybrid (BM25 + vector fused with
    # RRF), or hybrid_semantic (hybrid, then the semantic ranker reranks).
    # See docs/retrieval-benchmark.md for the measured trade-off.
    retrieval_mode: Literal["vector", "hybrid", "hybrid_semantic"] = "hybrid_semantic"
    # Semantic configuration defined in the index (ingestion/index_schema.py).
    semantic_configuration: str = "filings-semantic"

    # Chunks per sub-question (per company when several are requested).
    retrieval_top_k: int = 4

    chunk_size: int = 1000
    chunk_overlap: int = 150
    embed_batch_size: int = 64
    upload_batch_size: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env
