"""Automatic recovery for files stuck in 'pending' or 'processing'.

Called once at startup and then periodically by a background task.  Finds
files that have been in an unfinished processing state for longer than
INGEST_RECOVERY_STUCK_SECONDS and re-enqueues them.  The ingest worker is
idempotent: re-running a task on the same file is safe — the last writer
wins in the DB and stale vector chunks are cleaned up.
"""
import logging

from app.config import Settings
from app.controllers.file_controller import FileController
from app.models.files import FileInDB, ProcessingStatus
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.queue.queue_service import MessageQueueService

logger = logging.getLogger(__name__)


def _ingest_payload(record: FileInDB, actor: str) -> dict:
    payload: dict = {
        "action_type": "create",
        "file_id": record.id,
        "file_path": record.file_path,
        "name": record.name,
        "mimetype": record.mimetype,
        "user_id": actor,
    }
    if record.document_key:
        payload["document_key"] = record.document_key
        payload["version_no"] = record.version_number
        if record.version_label:
            payload["version_label"] = record.version_label
    return payload


async def recover_stuck_files(
    db_service: DatabaseService,
    queue: MessageQueueService,
    settings: Settings,
    *,
    actor: str = "recovery",
) -> int:
    """Resets and requeues files stuck in pending/processing. Returns the count requeued."""
    try:
        result = await db_service.call_procedure(
            StoredProcedures.LIST_STUCK_FILES,
            [settings.INGEST_RECOVERY_STUCK_SECONDS],
        )
    except Exception:
        logger.warning("Could not query for stuck files; skipping recovery sweep.", exc_info=True)
        return 0

    rows = result[0] if result else []
    if not rows:
        return 0

    file_controller = FileController(db_service)
    requeued = 0
    for row in rows:
        file_id = row.get("id")
        try:
            record = FileInDB(**row)
            await file_controller.update_processing_status(record.id, ProcessingStatus.PENDING)
            await queue.send_message(_ingest_payload(record, actor))
            logger.warning(
                "Ingest recovery: requeued file %s ('%s') — stuck in '%s' for >%ds.",
                record.id, record.name, row.get("processing_status"),
                settings.INGEST_RECOVERY_STUCK_SECONDS,
            )
            requeued += 1
        except Exception:
            logger.warning("Ingest recovery: could not requeue file %s.", file_id, exc_info=True)

    return requeued
