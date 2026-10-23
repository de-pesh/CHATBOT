"""Splitting a document's text into chunks, and shaping them for the vector store.

Two routing paths, chosen by content type:

1. Markdown with ATX headings (PDF pages, DOCX/PPTX/XLSX/HTML, vision LLM output)
   → heading-based splitter: splits at # boundaries, heading kept in each chunk.
     Control IDs (AC-1, CC6.1, A.9.1.1) are extracted and stored in metadata
     for exact-match payload filtering alongside semantic search.

2. Plain text / e-mail (.txt blobs)
   → RecursiveCharacterTextSplitter: character-window fallback.
"""
import re
from collections.abc import Sequence
from typing import Any

from app.logic.text_formats import PAGE_BREAK
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ---------------------------------------------------------------------------
# GRC control ID extraction
# ---------------------------------------------------------------------------
# Patterns cover the most common GRC frameworks used in the Middle East and
# globally: NIST SP 800-53 (AC-1), ISO/IEC 27001 (A.9.1.1), SOC 2 (CC6.1),
# NIST CSF (ID.AM-1), and PCI-DSS (Req 8.1). The patterns are intentionally
# conservative to avoid false positives on version numbers or IP addresses.
_CONTROL_ID_RE = re.compile(
    r'\b('
    r'[A-Z]{2,4}-\d+(?:\.\d+)*'         # NIST 800-53: AC-1, SA-3.1, PM-10
    r'|A\.\d{1,2}\.\d{1,2}(?:\.\d+)*'   # ISO 27001:   A.9.1.1
    r'|CC\d+\.\d+'                        # SOC 2:       CC6.1, CC7.2
    r'|(?:Req(?:uirement)?\.?\s*)\d+\.\d+(?:\.\d+)*'  # PCI-DSS: Req 8.1.1
    r')\b'
)


def _extract_control_ids(text: str) -> list[str]:
    """Deduplicated list of GRC control IDs found in `text`, order-preserving."""
    return list(dict.fromkeys(_CONTROL_ID_RE.findall(text)))


# ---------------------------------------------------------------------------
# Path 1 — Heading-based splitter for markdown
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"(?m)^(#{1,6} .+)$")


def _split_at_headings(text: str, chunk_size: int, chunk_overlap: int) -> list[dict[str, Any]]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_parts: list[str] = []

    for line in text.splitlines(keepends=True):
        if _HEADING_RE.match(line.rstrip()):
            if current_heading or current_parts:
                sections.append((current_heading, "".join(current_parts)))
            current_heading = line.rstrip()
            current_parts = []
        else:
            current_parts.append(line)

    if current_heading or current_parts:
        sections.append((current_heading, "".join(current_parts)))

    chunks: list[dict[str, Any]] = []
    for heading, body in sections:
        section_text = (f"{heading}\n{body}" if heading else body).strip()
        if not section_text:
            continue
        sub_chunks = (
            [section_text] if len(section_text) <= chunk_size
            else splitter.split_text(section_text)
        )
        for sub in sub_chunks:
            metadata: dict[str, Any] = {"section_heading": heading}
            control_ids = _extract_control_ids(sub)
            if control_ids:
                metadata["control_ids"] = control_ids
            chunks.append({"text": sub, "metadata": metadata})
    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def split_document(text: str, chunk_size: int, chunk_overlap: int, by_page: bool) -> list[dict[str, Any]]:
    """`[{"text", "metadata"}]`, one per chunk.

    Routing:
    - ATX markdown headings → heading-based splitter (PDF pages, DOCX/PPTX/XLSX/HTML,
                              vision LLM output). Control IDs added to metadata.
    - Plain text            → RecursiveCharacterTextSplitter with optional
                              page-number tagging when by_page is True.

    chunk_size / chunk_overlap affect both paths.
    """
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError(
            f"CHUNK_OVERLAP ({chunk_overlap}) must be at least 0 and smaller than CHUNK_SIZE ({chunk_size})."
        )

    if _HEADING_RE.search(text):
        return _split_at_headings(text, chunk_size, chunk_overlap)

    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    pages = text.split(PAGE_BREAK) if by_page else [text]
    return [
        {"text": chunk, "metadata": {"page_number": number} if by_page else {}}
        for number, page in enumerate(pages, start=1)
        if page.strip()
        for chunk in splitter.split_text(page)
    ]


def chunk_records(chunks: Sequence[dict[str, Any]], vectors: Any, model_id: str) -> list[dict[str, Any]]:
    """The stored form of each chunk and its embedding."""
    if vectors.ndim != 2 or vectors.shape[0] != len(chunks):
        raise ValueError(
            f"Embedding shape mismatch. Expected ({len(chunks)}, vector_dim), but got {vectors.shape}."
        )
    return [
        {
            "textContent": chunk["text"],
            "vectorContent": vector.tolist(),
            "embeddingModel": model_id,
            "metadata": chunk["metadata"],
        }
        for chunk, vector in zip(chunks, vectors)
    ]


def stamp_metadata(
    chunk_metadata: dict[str, Any],
    caller_metadata: dict[str, Any],
    *,
    uuid: str,
    source_file_id: str,
    original_file_path: str,
) -> dict[str, Any]:
    """Final metadata: chunk's own fields under caller's fields under the shared identifiers."""
    return {
        **chunk_metadata,
        **caller_metadata,
        "uuid": uuid,
        "source_file_id": source_file_id,
        "original_file_path": original_file_path,
        "isactive": True,
    }
