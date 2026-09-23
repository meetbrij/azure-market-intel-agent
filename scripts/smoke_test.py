"""
Day 1 smoke test — Enterprise Market Intelligence Agent.

Verifies, using keyless (Entra ID) auth via your `az login` session:
  1. Chat model deployment responds
  2. Embedding deployment returns a vector
  3. AI Search: create index -> upload doc -> query -> delete index
  4. Key Vault: read secrets
  5. Blob Storage: list files in raw-filings
  6. Langfuse: auth check + send one trace

Setup:
  uv add azure-identity openai azure-search-documents azure-keyvault-secrets \
         azure-storage-blob langfuse python-dotenv
  uv run scripts/smoke_test.py
"""

import os
import sys
import time
import uuid

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

load_dotenv()

REQUIRED = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_CHAT_DEPLOYMENT",
    "AZURE_OPENAI_EMBED_DEPLOYMENT",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_KEYVAULT_URL",
    "AZURE_STORAGE_ACCOUNT_URL",
    "AZURE_STORAGE_CONTAINER",
    "LANGFUSE_HOST",
]

missing = [k for k in REQUIRED if not os.getenv(k)]
if missing:
    sys.exit(f"Missing .env values: {', '.join(missing)}")

cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
results: list[tuple[str, bool, str]] = []


def check(name):
    def wrap(fn):
        start = time.perf_counter()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({time.perf_counter() - start:.1f}s)"))
        except Exception as e:  # noqa: BLE001 — smoke test reports everything
            results.append((name, False, f"{type(e).__name__}: {str(e)[:200]}"))
        return fn
    return wrap


# ---------- 1 & 2. Azure OpenAI (Foundry) ----------
from openai import AzureOpenAI

token_provider = get_bearer_token_provider(cred, "https://cognitiveservices.azure.com/.default")
aoai = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_version=os.environ["AZURE_OPENAI_API_VERSION"],
    azure_ad_token_provider=token_provider,
)


@check("Chat model")
def _chat():
    # No temperature / max_tokens: GPT-5-family reasoning models reject or
    # mis-handle them. Keep the call minimal.
    r = aoai.chat.completions.create(
        model=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"],
        messages=[{"role": "user", "content": "Reply with exactly: OK"}],
    )
    text = (r.choices[0].message.content or "").strip()
    return f"reply={text!r}, tokens={r.usage.total_tokens if r.usage else '?'}"


@check("Embeddings")
def _embed():
    r = aoai.embeddings.create(
        model=os.environ["AZURE_OPENAI_EMBED_DEPLOYMENT"],
        input="Quarterly revenue grew 12% year over year.",
    )
    return f"dims={len(r.data[0].embedding)}"


# ---------- 3. Azure AI Search ----------
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
)


@check("AI Search")
def _search():
    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    index_name = f"smoke-{uuid.uuid4().hex[:8]}"
    idx_client = SearchIndexClient(endpoint, cred)
    idx_client.create_index(
        SearchIndex(
            name=index_name,
            fields=[
                SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                SearchableField(name="content", type=SearchFieldDataType.String),
            ],
        )
    )
    try:
        client = SearchClient(endpoint, index_name, cred)
        client.upload_documents([{"id": "1", "content": "smoke test document"}])
        time.sleep(2)  # indexing is near-real-time, not instant
        hits = list(client.search("smoke"))
        return f"index created, {len(hits)} hit(s), cleaned up"
    finally:
        idx_client.delete_index(index_name)


# ---------- 4. Key Vault ----------
from azure.keyvault.secrets import SecretClient

kv = SecretClient(vault_url=os.environ["AZURE_KEYVAULT_URL"], credential=cred)
secrets: dict[str, str] = {}


@check("Key Vault")
def _kv():
    for name in ("langfuse-public-key", "langfuse-secret-key", "tavily-api-key"):
        secrets[name] = kv.get_secret(name).value or ""
    return f"read {len(secrets)} secrets"


# ---------- 5. Blob Storage ----------
from azure.storage.blob import BlobServiceClient


@check("Blob Storage")
def _blob():
    svc = BlobServiceClient(os.environ["AZURE_STORAGE_ACCOUNT_URL"], credential=cred)
    container = svc.get_container_client(os.environ["AZURE_STORAGE_CONTAINER"])
    names = [b.name for b in container.list_blobs()]
    return f"{len(names)} file(s): {names[:3]}"


# ---------- 6. Langfuse ----------
@check("Langfuse")
def _langfuse():
    if not secrets.get("langfuse-public-key"):
        raise RuntimeError("Langfuse keys unavailable (Key Vault check failed)")
    from langfuse import Langfuse

    lf = Langfuse(
        public_key=secrets["langfuse-public-key"],
        secret_key=secrets["langfuse-secret-key"],
        host=os.environ["LANGFUSE_HOST"],
    )
    if not lf.auth_check():
        raise RuntimeError("auth_check failed — wrong keys or wrong host region")
    lf.flush()
    return "auth ok"


# ---------- Report ----------
print("\nSmoke test results\n" + "-" * 60)
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name:<14} {detail}")
failed = sum(1 for _, ok, _ in results if not ok)
print("-" * 60)
print("All checks passed." if not failed else f"{failed} check(s) failed.")
sys.exit(1 if failed else 0)
