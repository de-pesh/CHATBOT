"""The numbered passages a prompt shows the model, and the metadata a citation is built from."""

from app.logic.prompts.common import _as_data
from app.logic.versions import is_older, version_tag


def _page_of(metadata: dict):
    """A chunk's page number as an int, or None when it has none.

    Only policy-style documents carry one (see indexing/prepare.py in the compute service). It arrives
    as a float - protobuf Struct has no integer type - so 3.0 becomes 3.
    """
    page = metadata.get("page_number")
    if isinstance(page, (int, float)) and not isinstance(page, bool):
        return int(page)
    return page or None


def _origin(metadata: dict):
    """(display name, stored path) for a chunk.

    Chunks indexed before file_name was recorded carry only original_file_path,
    "<blob key>/texts/<chunk>.txt", where the blob key is "<uuid>_<file name>".
    The name is recovered from that, and the key is returned as the stored path
    so a download link can be made without looking the name up. The path is only
    returned in that case - a chunk with a real file_name keeps resolving by name,
    unless it is a version of a document (`stored_file_path`): two versions share a name, so each
    links straight to its own file.
    """
    name = metadata.get("file_name") or ""
    if name:
        return name, metadata.get("stored_file_path") or ""
    stored = (metadata.get("original_file_path") or "").split("/texts/")[0]
    if not stored:
        return "", ""
    base = stored.rsplit("/", 1)[-1]
    head, sep, tail = base.partition("_")
    return (tail if sep and len(head) == 36 else base), stored


def build_sources(results: list, show_versions: bool = False) -> list:
    """Numbers the retrieved passages, 1-based, in the order they are shown to the model.

    The model cites a passage by that number and controllers/chat_citations.py maps
    it back here, so the file name and page in a citation come from the retrieved
    metadata rather than from whatever the model chose to write.

    `show_versions` (the question reached older versions) makes each versioned passage carry its version,
    "v2", so a citation and the model both say which version a statement came from.
    """
    sources = []
    for n, hit in enumerate(results, start=1):
        metadata = hit.get("metadata") or {}
        file_name, stored_path = _origin(metadata)
        sources.append({
            "n": n,
            "file_name": file_name,
            "stored_path": stored_path,
            "page": _page_of(metadata),
            "version": version_tag(metadata) if show_versions else "",
            "older": is_older(metadata),
        })
    return sources


def _format_context(results: list, show_versions: bool = False) -> str:
    """The retrieved passages as numbered blocks the model can cite by number."""
    blocks = []
    for source, hit in zip(build_sources(results, show_versions), results):
        # A document is untrusted twice over: its text is whatever someone
        # uploaded, and so is the name they uploaded it under. Only the copies
        # going into the prompt are defanged - build_sources keeps the real name,
        # which is what citations and download links are resolved from.
        text = _as_data((hit.get("metadata") or {}).get("textContent", ""), "context")
        header = f"[{source['n']}] {_as_data(source['file_name'], 'context')}"
        if source["version"]:
            header += f", Version: {_as_data(source['version'], 'context')}" + (" (older version)" if source["older"] else " (latest)")
        if source["page"] is not None:
            header += f", Page: {source['page']}"
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)
