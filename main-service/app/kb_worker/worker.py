"""The knowledge-base queue worker: handlers and the consumer loop.

Absorbed from chatbot-kb-management-service, which ran this as its own
container doing the same parse-then-index work POST /kb already did. The
handlers and the loop that drives them were two modules; they are only ever
used together.

Set KB_WORKER_ENABLED=false to run this process as API-only. The same image can
then be deployed twice - API replicas with the worker off, one worker replica
with it on - if parsing should not share the API's event loop.
"""
import asyncio
import logging
from enum import StrEnum
from functools import partial

from app.config import Settings
from app.stored_procedures import StoredProcedures
from app.kb_worker.indexing_rpc import IndexingRPCService
from app.services.queue_factory import create_queue_service
from app.utils import extract_blob_path_from_url
from app.kb_worker.indexing_rpc import parse_file_via_rpc
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)


async def add_file_handler(
    payload: dict,
    settings: Settings,
    db_service: DatabaseService,
):
    """Parse a file, index it, and record the parsed path against the file row.

    main-service has already created the file record; this fills in parsed_path.
    """
    file_id = payload.get("file_id")
    try:
        original_file_path = payload.get("file_path")
        if original_file_path is None:
            raise ValueError("file_path must be provided for CREATE action.")

        blob_path = extract_blob_path_from_url(original_file_path)

        # 1. Parse, in the compute service.
        parsed_file_path = await parse_file_via_rpc(settings, blob_path)
        if not parsed_file_path:
            raise ValueError("Parsing did not return a valid parsed file path.")
        logger.info("File %s parsed to: %s", file_id, parsed_file_path)

        # 2. Index it, linking the vector doc back to the SQL record.
        metadata = {"source_file_id": str(file_id)}
        indexed_id = await IndexingRPCService.indexing_add_file(
            settings=settings, parsed_file_path=parsed_file_path, metadata=metadata
        )
        if not indexed_id:
            raise ValueError("Indexing service did not return a valid ID.")
        logger.info("File %s indexed with vector ID: %s", file_id, indexed_id)

        file_name = payload.get("name") or payload.get("file_name")
        mimetype = payload.get("mimetype")
        user_id = payload.get("user_id")

        if not file_name:
            raise ValueError("File name must be provided in payload for CREATE action.")
        if not mimetype:
            raise ValueError("MIME type must be provided in payload for CREATE action.")
        if not user_id:
            raise ValueError("user_id must be provided in payload for CREATE action.")

        # 3. Write the parsed path back onto the existing file row.
        await db_service.call_procedure(
            StoredProcedures.UPSERT_FILE,
            [
                file_id,              # IN `id` - which record to update
                file_name,            # IN `name`
                mimetype,             # IN `mimetype`
                original_file_path,   # IN `file_path`
                parsed_file_path,     # IN `parsed_path` - the new information
                "framework",          # IN `file_type`
                True,                 # IN `is_active`
                user_id,              # IN `user_id`
            ],
        )
        logger.info("Updated file record %s with parsed path.", file_id)

    except Exception as e:
        logger.exception("add_file_handler failed for file_id %s: %s", file_id, e)
        raise  # re-raise so the worker applies retry/DLQ


async def delete_file_handler(
    payload: dict,
    settings: Settings,
    db_service: DatabaseService,
    **_unused,
):
    """Remove a file from the vector index and soft-delete its row."""
    file_id = payload.get("file_id")
    try:
        if file_id is None:
            raise ValueError("file_id must be provided for DELETE action.")
        deleted_by = payload.get("user_id")

        await IndexingRPCService.indexing_delete_file(
            settings=settings, source_file_id=str(file_id)
        )
        logger.info("File %s deleted from vector index.", file_id)

        await db_service.call_procedure(StoredProcedures.DELETE_FILE, [file_id, deleted_by])
        logger.info("File record %s marked inactive.", file_id)

    except Exception as e:
        logger.exception("delete_file_handler failed for file_id %s: %s", file_id, e)
        raise


"""Background consumer for the chatbot knowledge-base queue.

This ran as its own container (chatbot-kb-management-service) doing the same
parse-then-index work the /kb endpoint does. It now runs as an asyncio task
inside this service, which removes the container, the duplicate implementation,
and the gRPC hop to parsing-rpc-server.

Set KB_WORKER_ENABLED=false to turn it off - useful if ingestion volume ever
grows enough that parsing should not share a process with chat requests. The
same image can then be deployed twice: API replicas with the worker off, and a
worker replica with it on.
"""


logger = logging.getLogger(__name__)


class PolicyActionType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


async def _process_message(
    payload: dict,
    operation_id: str,
    *,
    settings: Settings,
    db_service: DatabaseService,
):
    action = payload.get("action_type")
    logger.info("[KBWorker] Task %s: action=%s", operation_id, action)

    if action == PolicyActionType.CREATE:
        await add_file_handler(
            payload=payload,
            settings=settings,
            db_service=db_service,
        )
    elif action == PolicyActionType.DELETE:
        await delete_file_handler(
            payload=payload,
            settings=settings,
            db_service=db_service,
        )
    else:
        # Raise, so the worker's retry/DLQ path handles it rather than silently
        # acknowledging a message nothing understands.
        raise ValueError(f"Unsupported action_type '{action}' in payload.")

    logger.info("[KBWorker] Task %s: completed '%s'.", operation_id, action)


async def run_kb_worker(
    settings: Settings,
    db_service: DatabaseService,
):
    """Consume the chatbot queue until cancelled."""
    async with create_queue_service(settings) as queue:
        logger.info("[KBWorker] watching queue '%s'", settings.CHATBOT_QUEUE_NAME)
        await queue.run_worker(
            process_func=partial(
                _process_message,
                settings=settings,
                db_service=db_service,
            ),
            brpop_timeout=settings.WORKER_BRPOP_TIMEOUT,
            error_retry_delay=settings.WORKER_ERROR_RETRY_DELAY,
        )


def start_kb_worker(app, settings, db_service) -> None:
    """Launch the worker as a background task, if enabled."""
    if not settings.KB_WORKER_ENABLED:
        logger.info("[KBWorker] disabled (KB_WORKER_ENABLED=false); not consuming the queue.")
        return

    async def _supervised():
        try:
            await run_kb_worker(settings, db_service)
        except asyncio.CancelledError:
            logger.info("[KBWorker] cancelled during shutdown.")
            raise
        except Exception:
            # A dead worker must not take the API down with it, but it must be
            # loud - otherwise ingestion silently stops.
            logger.exception("[KBWorker] stopped with an unhandled error; queue is no longer being consumed.")

    app.state.kb_worker_task = asyncio.create_task(_supervised())
    logger.info("[KBWorker] started as a background task.")


async def stop_kb_worker(app) -> None:
    task = getattr(app.state, "kb_worker_task", None)
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    logger.info("[KBWorker] stopped.")
