"""Resolves the [n] citation markers in an answer to download URLs and links them.

Markers are numbered against the sources `prompts.build_sources` returns, so the
file name and page in a citation come from the retrieved metadata, not from text
the model wrote (the rendering itself is `logic/citations.py`).

URLs come from the configured backend via `get_download_url`, which returns None
for backends that have no public URL - a mounted volume, for instance. When it
does, the marker is left as plain text rather than emitting a link that would
not resolve. The cited file is found in the `files` table by name, since every
upload gets a randomly-prefixed blob key and a file name never maps to a fixed path.
"""
import asyncio
import logging

from app.config import Settings
from app.logic.citations import (
    LEGACY_CITED_BLOCK,
    cited_url_keys,
    render_markers,
    url_key,
)
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)


async def _resolve_citation_url(
    storage: FileStorageService, db_service: DatabaseService, file_name: str, stored_path: str = "",
) -> str | None:
    """Maps a cited document name to a fetchable URL, or None if there is none.

    Looks the name up in the files table to get the actual stored path (the blob
    key includes a per-upload random prefix, not just the original file name),
    then asks the storage backend for a URL to it. Matches on the most recently
    created active file whose name contains file_name, the same substring rule
    GET /files uses - good enough here since the name comes from the metadata of
    a passage that was retrieved from that very file. A chunk that carries where
    its file is stored (`stored_path`) is linked straight to it, no lookup.
    """
    if not file_name:
        return None
    try:
        if not stored_path:
            results = await db_service.call_procedure(
                StoredProcedures.FILTER_FILES, [None, file_name, None, True, 1, 1],
            )
            rows = results[0] if results else []
            stored_path = rows[0].get("file_path") if rows else None
            if not stored_path:
                return None
        return storage.get_download_url(stored_path)
    except Exception:
        logger.exception("Failed to build a download URL for %s", stored_path or file_name)
        return None


async def _resolve_urls(documents: dict, storage: FileStorageService, db_service: DatabaseService) -> dict:
    """{url key: URL or None} for each entry of `documents` ({url key: (file name, stored path or "")}), looked up at once."""
    resolved = await asyncio.gather(
        *(_resolve_citation_url(storage, db_service, name, path) for name, path in documents.values())
    )
    return dict(zip(documents, resolved))


def _documents(sources: list) -> dict:
    """{url key: (file name, stored path)} of the cited documents: one entry per file, so two versions of a
    document that share a name each get their own link."""
    return {url_key(s): (s["file_name"], s.get("stored_path", "")) for s in sources if s.get("file_name")}


async def prefetch_citation_urls(sources: list, storage: FileStorageService, db_service: DatabaseService) -> dict:
    """Resolves a download URL for every document among `sources`, at once.

    Started as soon as the passages are known - while the model is still writing
    the answer - so that by the time the answer is complete the links are already
    in hand and `linkify_citations` has nothing left to wait for. Looks up
    documents the answer may end up not citing; that is a handful of cheap
    lookups run alongside a generation that takes seconds.
    """
    return await _resolve_urls(_documents(sources), storage, db_service)


async def linkify_citations(
    response: str,
    sources: list,
    storage: FileStorageService,
    db_service: DatabaseService,
    settings: Settings,
    prefetched: asyncio.Future | None = None,
) -> str:
    """Replaces the inline [n] markers in a response with links to the cited documents.

    `sources` are the numbered passages the answer was built from, as
    {n, file_name, page}; a marker whose number is not in there is one the model
    made up, and is dropped. Where the backend has no public URL for a document
    the marker is left as plain text. `prefetched` is the task running
    `prefetch_citation_urls`, if one was started; anything it did not cover is
    looked up here.
    """
    if not response:
        return response

    text = LEGACY_CITED_BLOCK.sub("", response).rstrip()
    by_n = {s["n"]: s for s in sources if s.get("file_name")}

    urls: dict = {}
    if prefetched is not None:
        try:
            urls = dict(await prefetched)
        except Exception:
            logger.exception("Prefetching citation URLs failed; resolving them now.")
    documents = _documents(by_n.values())
    missing = {key: documents[key] for key in cited_url_keys(text, by_n) if key not in urls}
    urls.update(await _resolve_urls(missing, storage, db_service))

    return render_markers(text, by_n, urls, settings.CITATION_LINK_STYLE, settings.CITATION_LABEL_NAME_WIDTH)
