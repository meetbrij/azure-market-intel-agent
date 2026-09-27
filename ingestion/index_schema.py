"""AI Search index definition for SEC filing chunks."""

from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

HNSW_CONFIG = "hnsw-default"
VECTOR_PROFILE = "vector-hnsw"

# EDM type names (the SDK enum is poorly typed for mypy).
STRING = "Edm.String"
INT32 = "Edm.Int32"
VECTOR = "Collection(Edm.Single)"


def build_index(name: str, dimensions: int, semantic_config: str) -> SearchIndex:
    """semantic_config names the configuration the hybrid_semantic retrieval
    mode queries (Settings.semantic_configuration)."""
    fields = [
        SimpleField(name="id", type=STRING, key=True),
        SearchableField(name="content", type=STRING),
        # "Amazon 10-K 2025-12-31 p.27": gives BM25 and the semantic ranker the
        # filing and page each chunk comes from.
        SearchableField(name="title", type=STRING),
        SearchField(
            name="content_vector",
            type=VECTOR,
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
            # Not stored/retrievable: halves vector storage, which matters on the
            # Free tier (~50 MB). Vectors are still searchable.
            stored=False,
            hidden=True,
        ),
        SimpleField(name="company", type=STRING, filterable=True, facetable=True),
        SimpleField(name="ticker", type=STRING, filterable=True),
        SimpleField(name="doc_type", type=STRING, filterable=True),
        SimpleField(name="period", type=STRING, filterable=True, sortable=True),
        SimpleField(name="source_blob", type=STRING),
        SimpleField(name="chunk_no", type=INT32),
        SimpleField(name="page", type=INT32, filterable=True),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=HNSW_CONFIG)],
        profiles=[
            VectorSearchProfile(
                name=VECTOR_PROFILE, algorithm_configuration_name=HNSW_CONFIG
            )
        ],
    )
    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=semantic_config,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ]
    )
    return SearchIndex(
        name=name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )
