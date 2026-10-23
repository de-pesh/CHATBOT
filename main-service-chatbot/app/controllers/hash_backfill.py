"""Gives files uploaded before content hashing (scripts/mssql/09_audit_trace_hash.sql) their content hash.

Without one, a byte-identical re-upload of such a file is not refused as a duplicate (routers/file.py): the duplicate
check finds files by hash. Run by POST /admin/files/backfill-hashes; safe to run again, it only touches files that
still have none."""
import hashlib
import logging

from app.logic.files import extract_blob_path_from_url
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)


async def backfill_content_hashes(db_service: DatabaseService, storage: FileStorageService) -> dict:
    """Hashes every active file that has no hash, one at a time (each is read whole, like an upload).

    A file that cannot be read (removed from the store, storage down) is skipped and listed, not fatal: the others
    are still hashed, and running it again retries it."""
    rows = (await db_service.call_procedure(StoredProcedures.LIST_FILES_MISSING_HASH, []))[0]
    hashed, unreadable = 0, []
    for row in rows:
        try:
            stream = await storage.download_file(extract_blob_path_from_url(row["file_path"]))
            with stream:
                digest = hashlib.sha256(stream.read()).hexdigest()
        except Exception as error:
            logger.warning("Could not read file %s (%s) to hash it: %s", row["id"], row["name"], error)
            unreadable.append({"id": row["id"], "name": row["name"]})
            continue
        await db_service.call_procedure(StoredProcedures.SET_FILE_CONTENT_HASH, [row["id"], digest])
        hashed += 1
    return {"checked": len(rows), "hashed": hashed, "unreadable": unreadable}
