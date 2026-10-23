"""Document versions: elect the latest version and mirror it onto the vector-store chunks."""
import asyncio
import logging

from app.config import Settings
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.vector.vector_store_factory import create_vector_store

logger = logging.getLogger(__name__)

PUSH_ATTEMPTS = 3
PUSH_RETRY_SECONDS = 1.0


async def _push_version(settings: Settings, row: dict) -> None:
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            async with create_vector_store(settings) as vector_store:
                await vector_store.set_latest_by_source_id(
                    source_file_id=row["vector_store_file_id"],
                    is_latest=bool(row["is_latest"]),
                    collection_name=settings.VECTOR_COLLECTION_NAME,
                    version_no=row.get("version_number"),
                    version_label=row.get("version_label"),
                    document_key=row.get("document_key"),
                )
            return
        except Exception:
            if attempt == PUSH_ATTEMPTS:
                raise
            logger.warning(
                "Could not write version of %s (attempt %d); retrying.",
                row["vector_store_file_id"], attempt, exc_info=True,
            )
            await asyncio.sleep(PUSH_RETRY_SECONDS)


async def sync_document(db_service: DatabaseService, settings: Settings, document_key: str | None) -> int:
    """Elect the latest version and mirror it onto every ingested version's chunks."""
    if not document_key:
        return 0
    result = await db_service.call_procedure(StoredProcedures.PROMOTE_LATEST_VERSION, [document_key])
    versions = result[0] if result else []
    for row in sorted(versions, key=lambda r: not r["is_latest"]):
        await _push_version(settings, row)
    return len(versions)
