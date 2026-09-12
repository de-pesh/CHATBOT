"""Turns citation lines in an answer into links, when the storage backend can
mint a URL for the cited document.

This used to import `generate_blob_sas` from the Azure SDK and build the URL
itself, which made a "pluggable" storage layer a fiction: switching
STORAGE_TYPE left the chat path still calling Azure. URLs now come from the
configured backend via `get_download_url`, which returns None for backends that
have no public URL - a mounted volume, for instance. When it does, the citation
is left as plain text rather than emitting a link that would not resolve.

Functions:
    - linkify_citations: Replaces file mentions in response text with links.
"""
import logging
import re
from typing import Optional

from app.services.storage_service import FileStorageService

logger = logging.getLogger(__name__)

pdf_url_map = {
    "CYBER INCIDENT RESPONSE FRAMEWORK.pdf": "GENAI/CPX/uploads/policy_pdfs/CYBER INCIDENT RESPONSE FRAMEWORK.pdf",
    "CYBER INCIDENT RESPONSE PLAN.pdf": "GENAI/CPX/uploads/policy_pdfs/CYBER INCIDENT RESPONSE PLAN.pdf",
    "CYBER SECURITY INFORMATION SHARING FRAMEWORK.pdf": "GENAI/CPX/uploads/policy_pdfs/CYBER SECURITY INFORMATION SHARING FRAMEWORK.pdf",
    "NATIONAL CLOUD SECURITY POLICY.pdf": "GENAI/CPX/uploads/policy_pdfs/NATIONAL CLOUD SECURITY POLICY.pdf",
    "NATIONAL IoT SECURITY POLICY.pdf": "GENAI/CPX/uploads/policy_pdfs/NATIONAL IoT SECURITY POLICY.pdf",
    "CRITICAL INFORMATION INFRASTRUCTURE PROTECTION (CIIP) POLICY.pdf": "GENAI/CPX/uploads/policy_pdfs/CRITICAL INFORMATION INFRASTRUCTURE PROTECTION (CIIP) POLICY.pdf",
}

# "<file name>, Page: <n>" - the tail of a citation line, once the "Cited:" or
# "- " prefix has been stripped. Stars are the model's bolding.
CITATION_LINE = re.compile(r"^\**(.+?)\**,\s*Page[:\s]*(.+)$")


def _resolve_citation_url(storage: Optional[FileStorageService], file_name: str) -> Optional[str]:
    """Maps a cited document name to a fetchable URL, or None if there is none."""
    blob_name = (
        pdf_url_map.get(file_name)
        or pdf_url_map.get(f"{file_name}.pdf")
        or pdf_url_map.get(file_name.replace(".pdf", "").strip())
    )
    if not blob_name or storage is None:
        return None
    try:
        return storage.get_download_url(blob_name)
    except Exception:
        logger.exception("Failed to build a download URL for %s", file_name)
        return None


def linkify_citations(response: str, storage: Optional[FileStorageService] = None) -> str:
    """
    Replace citation references in a response with links to the cited document.

    Parses lines following a "Cited:" marker or bullet points, maps the file
    name onto a stored document and, when the storage backend can produce a
    fetchable URL, replaces the name with a hyperlink.

    Args:
        response (str): The original text response containing citations.
        storage (Optional[FileStorageService]): The configured storage backend.
            When None, or when the backend has no public URL for the document,
            the line is left exactly as the model wrote it.

    Returns:
        str: The response, with citations linked where that was possible.
    """
    if not response:
        return response

    lines = response.strip().splitlines()
    new_lines = []
    in_cited_block = False

    for line in lines:
        stripped = line.strip()

        if stripped == "Cited:":
            new_lines.append("Cited:")
            in_cited_block = True
            continue

        # "Cited: <file>, Page: n" on one line, or "- <file>, Page: n" under a
        # "Cited:" header. Same substitution either way.
        if stripped.startswith("Cited:"):
            prefix, body = "Cited: ", stripped[len("Cited:"):].strip()
        elif in_cited_block and stripped.startswith("- "):
            prefix, body = "- ", stripped[2:].strip()
        else:
            in_cited_block = False
            new_lines.append(line)
            continue

        match = CITATION_LINE.match(body)
        if not match:
            new_lines.append(line)
            continue

        file_name, page_info = match.group(1).strip(), match.group(2).strip()
        url = _resolve_citation_url(storage, file_name)
        if not url:
            new_lines.append(line)
            continue

        link = f"<a target='_blank' style='color:#eed202' href='{url}'>{file_name}</a>"
        new_lines.append(f"{prefix}{link}, Page: {page_info}")

    return "\n".join(new_lines)
