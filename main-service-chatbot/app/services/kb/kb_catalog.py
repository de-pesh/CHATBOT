"""One record of what the knowledge base holds: every document, with a short
description of it.

The chatbot needs to be able to say what it knows. Without this, "what do you
have?" is a search over the documents' contents, and a refusal ("I can not
respond") tells the user nothing about what to ask instead. With it, the intent
step (see `chat_steps.ask_intent`) can answer those directly, and the refusal
can name what the knowledge base does cover.

It is a single JSON file in the file store - the same store the uploads live in,
so it is durable, backend-neutral and shared by every replica - and it is
*derived*, never the source of truth. The files table says which documents exist;
the catalog only adds a description to each. So it can always be rebuilt:
`refresh()` lists the indexed files, keeps the descriptions it already has,
writes one for any document that lacks it, and drops the ones that are gone.
That makes a lost or stale catalog, a failed description, and two replicas
racing, all self-healing on the next refresh.

When it refreshes: after a document finishes indexing (kb_worker), after a
delete (routers/file.py), and once at startup, which also describes documents
that were indexed before this existed.
"""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from io import BytesIO

from app.config import Settings
from app.logic.catalog import (
    catalog_entry,
    clip,
    description_from_reply,
    documents_changed,
    empty_catalog,
    existing_documents,
    indexed_rows,
    parse_catalog,
    render_catalog,
    reusable_description,
    serialize_catalog,
)
from app.logic.prompts import fetch_describe_prompt
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import ALL_ROWS, StoredProcedures
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)

# (file name, start of its text) -> a short description, or None if it could not
# be written. Supplied by the app, so this module holds no model client.
Describer = Callable[[str, str], Awaitable[str | None]]


def make_describer(llm) -> Describer:
    """A Describer that asks the model configured for the "describe" step (an
    ChatLLM: LLM_DESCRIBE_MODEL, else LLM_HELPER_MODEL, else the main model)."""
    async def describe(name: str, text: str) -> str | None:
        return description_from_reply(await llm.invoke_helper(fetch_describe_prompt(name, text), "describe"))
    return describe


class KbCatalog:
    def __init__(
        self, storage: FileStorageService, db_service: DatabaseService, describe: Describer, settings: Settings,
    ):
        """Limits come from Settings.KB_CATALOG_*: where the file lives in the store, how long a read is
        cached, how much text goes into a prompt and how wide a name, how much of a document is read to
        describe it, and the longest description kept."""
        self.storage = storage
        self.db_service = db_service
        self.describe = describe
        self.settings = settings
        self._cached: dict | None = None
        self._cached_at = 0.0
        # One refresh at a time in this process; across processes the refresh is
        # idempotent, so the worst a race does is repeat some work.
        self._lock = asyncio.Lock()

    async def _read(self) -> dict:
        """The stored catalog, or an empty one if there is none (or it is unreadable)."""
        try:
            if await self.storage.exists(self.settings.KB_CATALOG_KEY):
                return parse_catalog((await self.storage.download_file(self.settings.KB_CATALOG_KEY)).read())
        except Exception:
            logger.warning("Could not read the knowledge-base catalog; treating it as empty.", exc_info=True)
        return empty_catalog()

    async def load(self) -> dict:
        """The catalog, cached for a short time: every chat turn asks for it, and
        another replica may have changed it since."""
        if self._cached is None or time.monotonic() - self._cached_at > self.settings.KB_CATALOG_CACHE_SECONDS:
            self._cached = await self._read()
            self._cached_at = time.monotonic()
        return self._cached

    async def render(self) -> tuple[str, str]:
        """(the catalog as prompt text, the document names on their own) - see render_catalog."""
        return render_catalog(
            (await self.load()).get("documents", []),
            self.settings.KB_CATALOG_RENDER_MAX_CHARS, self.settings.KB_CATALOG_NAME_WIDTH,
        )

    async def refresh(self) -> dict:
        """Brings the catalog in line with the files table. Never raises: it runs
        after an upload or a delete, and neither may fail because of it."""
        async with self._lock:
            try:
                return await self._refresh()
            except Exception:
                logger.exception("Refreshing the knowledge-base catalog failed; it is left as it was.")
                return self._cached or empty_catalog()

    async def _refresh(self) -> dict:
        rows = indexed_rows(await self.db_service.call_procedure(
            StoredProcedures.FILTER_FILES, [None, None, None, True, 1, ALL_ROWS]
        ))
        existing = existing_documents(await self._read())

        documents = [
            catalog_entry(row, reusable_description(existing, row) or await self._describe(row)) for row in rows
        ]
        catalog = {"updated_at": datetime.now(UTC).isoformat(timespec="seconds"), "documents": documents}
        if documents_changed(documents, existing):
            await self.storage.upload_file(BytesIO(serialize_catalog(catalog)), self.settings.KB_CATALOG_KEY)
            logger.info("Knowledge-base catalog updated: %d document(s).", len(documents))
        self._cached, self._cached_at = catalog, time.monotonic()
        return catalog

    async def _describe(self, row: dict) -> str | None:
        """A description for one indexed file, from the start of its extracted text.
        None if that text cannot be read or the model gives nothing usable; the
        file is then retried on the next refresh."""
        parsed_path = row.get("extracted_text_path")
        if not parsed_path:
            return None
        try:
            raw = (await self.storage.download_file(parsed_path)).read()
            text = raw.decode("utf-8", errors="replace").strip()[:self.settings.KB_CATALOG_DESCRIBE_CHARS]
            if not text:
                return None
            description = await self.describe(row["name"], text)
        except Exception:
            logger.warning("Could not describe '%s' for the catalog.", row.get("name"), exc_info=True)
            return None
        return clip(description, self.settings.KB_CATALOG_DESCRIPTION_MAX_CHARS) if description else None
