"""AI Search index definition for SEC filing chunks."""

from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchIndex,
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


def build_index(name: str, dimensions: int) -> SearchIndex:
    fields = [
        SimpleField(name="id", type=STRING, key=True),
        SearchableField(name="content", type=STRING),
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
    return SearchIndex(name=name, fields=fields, vector_search=vector_search)
