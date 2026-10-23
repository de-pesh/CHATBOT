"""Knowledge-base queue worker: parse + index a file entirely in-process.

Receives Celery tasks from the queue, parses and indexes each file using the
same handlers the REST API uses, then writes the results back to the SQL
database and keeps the vector-store version metadata in sync.
"""
import asyncio
import logging
import time

from fastapi import HTTPException, status

from app.config import Settings
from app.controllers.file_controller import FileController
from app.controllers.version_controller import sync_document
from app.indexing.handler import index_handler
from app.logic.audit import record_ingest
from app.logic.files import extract_blob_path_from_url
from app.logic.kb_jobs import (
    failure_reason,
    file_fields,
    file_id_of,
    file_record_params,
    index_metadata,
    is_create,
    source_path,
    stale_chunk_sets,
)
from app.models.files import FileInDB, ProcessingStatus
from app.parsing.handler import parsing_handler
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.vector.vector_store_factory import create_vector_store

logger = logging.getLogger(__name__)


async def _mark(file_controller: FileController, file_id, processing_status: ProcessingStatus) -> None:
    try:
        await file_controller.update_processing_status(file_id, processing_status)
    except Exception:
        logger.warning("Could not mark file %s as '%s'.", file_id, processing_status.value, exc_info=True)


async def _current_row(file_controller: FileController, file_id) -> FileInDB | None:
    try:
        return await file_controller.get_file_by_id(file_id)
    except HTTPException as e:
        if e.status_code == status.HTTP_404_NOT_FOUND:
            return None
        raise


def _wanted(row: FileInDB | None) -> bool:
    from app.logic.kb_jobs import should_ingest
    return should_ingest(row is not None, row is not None and row.is_active)


async def _discard_chunks(settings: Settings, vector_store_file_ids: list[str]) -> None:
    for vector_store_file_id in vector_store_file_ids:
        try:
            async with create_vector_store(settings) as vector_store:
                await vector_store.deactivate_data_by_source_id(
                    collection_name=settings.VECTOR_COLLECTION_NAME,
                    source_file_id=vector_store_file_id,
                )
            logger.info("Removed superseded knowledge-base document %s.", vector_store_file_id)
        except Exception:
            logger.warning(
                "Could not remove knowledge-base document %s; its chunks are orphaned.",
                vector_store_file_id, exc_info=True,
            )


async def add_file_handler(
    payload: dict,
    settings: Settings,
    db_service: DatabaseService,
    storage,
    embedding,
    vision,
    catalog=None,
):
    """Parse a file in-process, index it, and record the results.

    Safe to run more than once (retries, duplicate delivery). Each attempt indexes
    a fresh document and only the recorded one remains reachable; stale ones are
    cleaned up on completion or failure.
    """
    file_id = None
    file_controller = FileController(db_service)
    t_start = time.perf_counter()
    parse_ms: int | None = None
    index_ms: int | None = None
    user_id: str | None = None
    file_name: str | None = None
    try:
        file_id = file_id_of(payload)
        original_file_path = source_path(payload)
        row = await _current_row(file_controller, file_id)
        if not _wanted(row):
            logger.info("File %s no longer exists or was deleted; not ingesting it.", file_id)
            return
        previous = row.vector_store_file_id
        old_parsed_path = row.extracted_text_path
        await _mark(file_controller, file_id, ProcessingStatus.PROCESSING)
        file_name, mimetype, user_id = file_fields(payload)

        # 1. Parse in-process.
        blob_path = extract_blob_path_from_url(original_file_path)
        t_parse = time.perf_counter()
        parsed_file_path = await parsing_handler(
            blob_path, storage, vision, settings.MAX_BLANK_PAGE_SHARE
        )
        parse_ms = round((time.perf_counter() - t_parse) * 1000)
        if not parsed_file_path:
            raise ValueError("Parsing did not return a valid parsed file path.")
        logger.info("File %s parsed to: %s", file_id, parsed_file_path)

        # 2. Index in-process.
        t_index = time.perf_counter()
        async with create_vector_store(settings) as vector_store:
            indexed_id = await index_handler(
                file_path=parsed_file_path,
                collection_name=settings.VECTOR_COLLECTION_NAME,
                metadata=index_metadata(file_id, file_name, payload),
                storage_service=storage,
                vector_store=vector_store,
                embedding_service=embedding,
                settings=settings,
                is_policy=True,
            )
        index_ms = round((time.perf_counter() - t_index) * 1000)
        if not indexed_id:
            raise ValueError("Indexing did not return a valid ID.")
        logger.info(
            "Ingest timing: file_id=%s parse_ms=%d index_ms=%d total_ms=%d",
            file_id, parse_ms, index_ms, parse_ms + index_ms,
        )
        logger.info("File %s indexed with vector ID: %s", file_id, indexed_id)

        # 3. Write results back to the database.
        recorded = False
        seen_ids = [previous]
        try:
            row = await _current_row(file_controller, file_id)
            if _wanted(row):
                seen_ids.append(row.vector_store_file_id)
                await db_service.call_procedure(
                    StoredProcedures.UPSERT_FILE,
                    file_record_params(
                        file_id, file_name, mimetype, original_file_path,
                        parsed_file_path, indexed_id, user_id,
                    ),
                )
                recorded = True
                await _mark(file_controller, file_id, ProcessingStatus.COMPLETED)
                if old_parsed_path and old_parsed_path != parsed_file_path:
                    try:
                        await storage.delete_file(old_parsed_path)
                        logger.info("Deleted old parsed blob for file %s: %s", file_id, old_parsed_path)
                    except Exception:
                        logger.warning("Could not delete old parsed blob %s for file %s.", old_parsed_path, file_id, exc_info=True)
            else:
                logger.info("File %s was deleted during ingestion; discarding indexed chunks.", file_id)
        finally:
            await asyncio.shield(_discard_chunks(settings, stale_chunk_sets(seen_ids, indexed_id, recorded)))
        if not recorded:
            return

        # 4. Elect the latest version and mirror it onto all version chunks.
        await sync_document(db_service, settings, payload.get("document_key"))

        # 5. Notify main-service to refresh the knowledge-base catalog descriptions.
        if catalog is not None:
            await catalog.refresh()

        await record_ingest(
            db_service,
            actor=user_id or "system",
            target=f"{file_id}:{file_name}",
            result="ok",
            detail={
                "parse_ms": parse_ms,
                "index_ms": index_ms,
                "total_ms": round((time.perf_counter() - t_start) * 1000),
            },
        )

    except Exception as e:
        logger.exception("add_file_handler failed for file_id %s: %s", file_id, e)
        await record_ingest(
            db_service,
            actor=user_id or "system",
            target=f"{file_id}:{file_name}",
            result="error",
            detail={
                "parse_ms": parse_ms,
                "index_ms": index_ms,
                "total_ms": round((time.perf_counter() - t_start) * 1000),
                "error": str(e),
            },
        )
        raise


async def process_message(payload: dict, operation_id: str, *, settings: Settings, db_service: DatabaseService, storage, embedding, vision, catalog=None):
    action = payload.get("action_type")
    logger.info("[KBWorker] Task %s: action=%s", operation_id, action)
    if not is_create(payload):
        raise ValueError(f"Unsupported action_type '{action}' in payload.")
    await add_file_handler(
        payload=payload, settings=settings, db_service=db_service,
        storage=storage, embedding=embedding, vision=vision, catalog=catalog,
    )
    logger.info("[KBWorker] Task %s: completed '%s'.", operation_id, action)


async def handle_dead_letter(
    payload: dict, operation_id: str, error_message: str,
    *, db_service: DatabaseService, settings: Settings,
):
    file_id = payload.get("file_id")
    if is_create(payload) and file_id is not None:
        try:
            await FileController(db_service).update_processing_status(
                file_id,
                ProcessingStatus.FAILED,
                failure_reason(error_message, settings.WORKER_ERROR_MESSAGE_MAX_CHARS),
            )
            logger.error("File %s permanently failed (task %s): %s", file_id, operation_id, error_message)
        except Exception:
            logger.exception("Could not record failure for file %s (task %s).", file_id, operation_id)
    else:
        logger.error("Task %s (action=%s) permanently failed: %s", operation_id, payload.get("action_type"), error_message)
