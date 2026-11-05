"""Document versions: stamping an upload with its version, electing the latest one, and mirroring that onto the chunks.

The files table is the source of truth (scripts/mssql/10_document_versions.sql). The vector store's chunks carry a copy
(`metadata.is_latest`) because that is what a search filters on, so every change to which version is latest ends in
`sync_document`, which pushes the files table's answer onto the chunks. It pushes the whole document, not only what
changed, so running it again after a failure is always correct.
"""
import asyncio
import logging
from datetime import date

from app.config import Settings
from app.logic.versions import clean_label, split_document_name
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import ALL_ROWS, StoredProcedures
from app.services.kb import kb_service

logger = logging.getLogger(__name__)

PUSH_ATTEMPTS = 3
PUSH_RETRY_SECONDS = 1.0


async def stamp_upload(
    db_service: DatabaseService, file_id: int, file_name: str, version_label: str | None, effective_date: date | None,
    document_key_override: str | None = None,
) -> dict:
    """Gives a just-created file record its document key and version number. Returns them as the
    `document_key`, `version_no`, `version_label` fields the ingest task carries to the worker.

    The label is the caller's if given, else the one in the file name ("Policy_v3.2.pdf" -> "3.2"), else none.
    document_key_override lets the caller bypass the filename-derived key to group files that don't follow the
    naming convention (e.g. "policy_2024.pdf" and "policy_2025.pdf" can share key "policy").
    The number is assigned inside the database in one locked step, so two uploads of a document at the same
    moment get different versions. Raises ValueError for a label with characters a prompt should not be handed."""
    derived_key, name_label = split_document_name(file_name)
    document_key = document_key_override or derived_key
    label = clean_label(version_label) or name_label
    result = await db_service.call_procedure(
        StoredProcedures.ASSIGN_FILE_VERSION, [file_id, document_key, label, effective_date],
    )
    version_no = int(result[0][0]["version_number"])
    return {"document_key": document_key, "version_no": version_no, "version_label": label}


async def _push_version(settings: Settings, row: dict) -> None:
    """Writes one version's fields onto its chunks - the latest flag and, so chunks indexed before versioning
    carry them too, its number, label and document key - retrying a transient failure of the compute service."""
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            await kb_service.set_document_version(
                settings, row["vector_store_file_id"], is_latest=bool(row["is_latest"]), version_no=row.get("version_number"),
                version_label=row.get("version_label"), document_key=row.get("document_key"),
            )
            return
        except Exception:
            if attempt == PUSH_ATTEMPTS:
                raise
            logger.warning("Could not write the version of %s (attempt %d); retrying.", row["vector_store_file_id"], attempt, exc_info=True)
            await asyncio.sleep(PUSH_RETRY_SECONDS)


async def sync_document(db_service: DatabaseService, settings: Settings, document_key: str | None) -> int:
    """Elects the latest version of a document and mirrors it onto the chunks of every ingested version.
    Returns how many versions were written. Raises if the vector store cannot be reached after a few tries."""
    if not document_key:
        return 0
    result = await db_service.call_procedure(StoredProcedures.PROMOTE_LATEST_VERSION, [document_key])
    versions = result[0] if result else []
    # The new latest first, so there is never a moment with no latest version (briefly two is harmless).
    for row in sorted(versions, key=lambda r: not r["is_latest"]):
        await _push_version(settings, row)
    return len(versions)


async def sync_quietly(db_service: DatabaseService, settings: Settings, document_key: str | None) -> None:
    """sync_document for callers that must not fail (a delete that has already happened): logs instead of raising.
    POST /admin/versions/sync repairs whatever this leaves behind."""
    try:
        await sync_document(db_service, settings, document_key)
    except Exception:
        logger.warning("Could not re-elect the latest version of '%s'.", document_key, exc_info=True)


async def sync_all(db_service: DatabaseService, settings: Settings) -> dict:
    """Brings every document up to date: gives rows from before versioning their key and version number (oldest
    upload first), then re-elects and mirrors the latest of each document. Safe to run any time."""
    rows = (await db_service.call_procedure(StoredProcedures.FILTER_FILES, [None, None, None, None, 1, ALL_ROWS]))[0]
    by_key: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: (r["created_on"], r["id"])):
        if row.get("document_key") is None:
            key, label = split_document_name(row["name"])
            row["document_key"], row["_label"] = key, label
            row["_new"] = True
        by_key.setdefault(row["document_key"], []).append(row)

    stamped = 0
    for key, group in by_key.items():
        for row in group:  # oldest upload first, so each gets the next number of its document
            if not row.get("_new"):
                continue
            await db_service.call_procedure(
                StoredProcedures.ASSIGN_FILE_VERSION, [row["id"], key, row.get("_label"), row.get("effective_date")],
            )
            stamped += 1
    synced = 0
    for key in by_key:
        synced += await sync_document(db_service, settings, key)
    return {"documents": len(by_key), "stamped": stamped, "versions_synced": synced}
