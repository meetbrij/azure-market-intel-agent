"""Page text -> fixed-size chunks. Phase 3 compares this with section-aware chunking."""

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ingestion.parse import PageText


@dataclass(frozen=True)
class Chunk:
    chunk_no: int
    page: int
    text: str


def chunk_pages(
    pages: list[PageText], chunk_size: int = 1000, overlap: int = 150
) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=overlap
    )
    chunks: list[Chunk] = []
    # Split per page so every chunk carries an exact page number for citations.
    for p in pages:
        for piece in splitter.split_text(p.text):
            chunks.append(Chunk(chunk_no=len(chunks), page=p.page, text=piece))
    return chunks
