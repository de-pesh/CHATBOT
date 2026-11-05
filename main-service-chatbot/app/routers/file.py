import hashlib
import logging
from datetime import date
from io import BytesIO
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import Settings, get_settings
from app.controllers.comparison_controller import generate_comparison
from app.controllers.file_controller import FileController
from app.controllers.version_controller import stamp_upload
from app.dependencies import (
    CurrentUserDep,
    get_chatbot_queue,
    get_db_service,
    get_file_storage_service,
    get_kb_catalog,
    require_admin,
)
from app.services.llm.chat_llm import ChatLLM, get_chat_llm
from app.services.vector.search_runtime import SearchRuntime, get_search_runtime
from app.logic.audit import (
    ACTION_FILE_DELETE,
    ACTION_FILE_DUPLICATE,
    ACTION_FILE_REINDEX,
    ACTION_FILE_UPLOAD,
    record_audit,
)
from app.logic.files import (
    classify_file_id,
    extract_blob_path_from_url,
    is_supported_extension,
    max_upload_bytes,
    stored_name,
    upload_problem,
)
from app.logic.http import attachment_disposition
from app.logic.versions import VERSION_LABEL_PATTERN
from app.models.files import FileCreate, FileLifecycleStatus, FileResponse, FileStatusResponse, ProcessingStatus
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.kb import kb_service
from app.services.queue.queue_service import MessageQueueService
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)
router = APIRouter()


async def _find_duplicate(db_service: DatabaseService, content_hash: str) -> dict | None:
    """The active file already holding these exact bytes, or None. A failed lookup is not a reason to
    refuse an upload: it is logged and the file goes through."""
    try:
        result = await db_service.call_procedure(StoredProcedures.FIND_FILE_BY_CONTENT_HASH, [content_hash])
    except Exception:
        logger.warning("Duplicate check failed; accepting the upload.", exc_info=True)
        return None
    rows = result[0] if result else []
    return rows[0] if rows else None


async def _remember_hash(db_service: DatabaseService, file_id: int, content_hash: str) -> None:
    try:
        await db_service.call_procedure(StoredProcedures.SET_FILE_CONTENT_HASH, [file_id, content_hash])
    except Exception:
        logger.warning("Could not store the content hash of file %s.", file_id, exc_info=True)


@router.post(
    "/",
    response_model=list[FileResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Upload files and create file records",
    description=(
        "Uploads one or more files to blob storage and creates corresponding file "
        "records in the database. Parsing and indexing happen off the request path: "
        "vector_store_file_id and extracted_text_path are null on the returned record until kb_worker "
        "finishes the job - poll GET /files/{file_id}/status to see it progress. A file whose name "
        "(without extension or a trailing _v<n> tag) matches an existing document is the next version of "
        "that document: questions are answered from the newest version unless they ask for an older one."
    ),
    dependencies=[Depends(require_admin)]
)
async def create_files(
    current_user: CurrentUserDep,
    files: list[UploadFile] = File(...),
    allow_duplicate: bool = Query(
        False, description="Upload a file whose content is already in the knowledge base anyway. Without it, a "
                           "byte-identical file is refused with a 409 naming the existing one.",
    ),
    version_label: str | None = Query(
        None, max_length=50, pattern=VERSION_LABEL_PATTERN,
        description="This version's own name: letters, digits, '.', '_' or '-', e.g. '3.2'. Default: the tag in the file name "
                                         "('Policy_v3.2.pdf'), else none - the version number is always set.",
    ),
    document_key: str | None = Query(
        None, max_length=200,
        description="Override the document key used to group versions of the same document. "
                    "If omitted, derived from the file name by stripping any trailing version tag. "
                    "Use this when file names don't follow the convention: upload 'policy_2024.pdf' and "
                    "'policy_2025.pdf' both with document_key='policy' to link them as one document.",
    ),
    effective_date: date | None = Query(None, description="When this version takes effect (YYYY-MM-DD)."),
    db_service: DatabaseService = Depends(get_db_service),
    file_storage_service: FileStorageService = Depends(get_file_storage_service),
    chatbot_queue: MessageQueueService = Depends(get_chatbot_queue),
    settings: Settings = Depends(get_settings),
) -> list[FileResponse]:
    """Endpoint to upload files and create file records."""
    controller = FileController(db_service, file_storage_service)
    size_limit = max_upload_bytes(settings.MAX_UPLOAD_SIZE_MB)
    file_responses = []
    for file in files:
        filename = file.filename or "unnamed"
        if not is_supported_extension(filename, settings.ALLOWED_UPLOAD_EXTENSIONS):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported extension for {filename}.",
            )

        too_large = HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"{filename} is larger than the maximum upload size of "
                f"{settings.MAX_UPLOAD_SIZE_MB} MB (MAX_UPLOAD_SIZE_MB)."
            ),
        )
        # The size the client declared is known before the body is read into memory; when the
        # framework could not tell (None), the check after reading is what enforces the limit.
        if file.size is not None and file.size > size_limit:
            raise too_large
        file_contents = await file.read()
        if len(file_contents) > size_limit:
            raise too_large
        if (problem := upload_problem(filename, file_contents)) is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
        content_hash = hashlib.sha256(file_contents).hexdigest()
        if not allow_duplicate and (existing := await _find_duplicate(db_service, content_hash)) is not None:
            await record_audit(
                db_service, current_user.email, ACTION_FILE_DUPLICATE, filename, "refused",
                {"existing_file_id": existing["id"], "existing_name": existing["name"]},
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{filename} has the same content as '{existing['name']}' (file {existing['id']}). "
                       "Delete that one first, or upload again with allow_duplicate=true.",
            )
        file_url = await file_storage_service.upload_file(
            file_data=BytesIO(file_contents), file_name=stored_name(uuid4(), filename),
        )
        # vector_store_file_id/extracted_text_path stay null until kb_worker's add_file_handler
        # parses and indexes the file and writes them back onto this row.
        file_response = await controller.create_file(
            FileCreate(name=filename, mimetype=file.content_type, file_path=file_url), current_user.email,
        )

        await _remember_hash(db_service, file_response.id, content_hash)
        version = await stamp_upload(db_service, file_response.id, filename, version_label, effective_date, document_key_override=document_key)
        file_response = await controller.get_file_by_id(file_response.id)
        await chatbot_queue.send_message({
            "action_type": "create",
            "file_id": file_response.id,
            "file_path": file_url,
            "name": filename,
            "mimetype": file.content_type,
            "user_id": current_user.email,
            **version,
        })

        await record_audit(
            db_service, current_user.email, ACTION_FILE_UPLOAD, f"{file_response.id}:{filename}",
            detail={"bytes": len(file_contents), "sha256": content_hash, "version_no": version["version_no"]},
        )
        file_responses.append(file_response)
    return file_responses


@router.get(
    "/download-by-name",
    response_class=StreamingResponse,
    summary="Download a file by name",
    description=(
        "Downloads a file by its original name from the configured file store. "
        "Available to all authenticated users, not just admins — intended for citation download links "
        "shown in chat answers. Pass `path` (the blob key stored in the citation) to skip the database "
        "lookup; omit it to resolve the path from the file name."
    ),
)
async def download_file_by_name(
    current_user: CurrentUserDep,
    name: str = Query(..., description="Original file name"),
    path: str | None = Query(None, description="Stored blob path, if known — skips the database lookup"),
    db_service: DatabaseService = Depends(get_db_service),
    file_storage_service: FileStorageService = Depends(get_file_storage_service),
) -> StreamingResponse:
    stored_path = path
    mimetype = "application/octet-stream"
    if not stored_path:
        results = await db_service.call_procedure(StoredProcedures.FILTER_FILES, [None, name, None, True, 1, 1])
        rows = results[0] if results else []
        row = rows[0] if rows else None
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File '{name}' not found.")
        stored_path = row.get("file_path")
        if not stored_path:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File '{name}' has no stored path.")
        mimetype = row.get("mimetype") or mimetype
    blob_name = extract_blob_path_from_url(stored_path)
    try:
        file_stream = await file_storage_service.download_file(blob_name)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File '{name}' not found in storage.")
    return StreamingResponse(
        file_stream,
        media_type=mimetype,
        headers={"Content-Disposition": attachment_disposition(name)},
    )


@router.get(
    "/{file_id}/versions",
    summary="All versions of a file's document",
    description="Every upload of the document this file belongs to, newest first, deleted ones included: "
                "version number and label, effective date, which is the latest, and each one's processing status.",
    dependencies=[Depends(require_admin)]
)
async def get_file_versions(
    file_id: int,
    db_service: DatabaseService = Depends(get_db_service),
) -> list[dict]:
    record = await FileController(db_service).get_file_by_id(file_id)
    if not record.document_key:
        return []
    result = await db_service.call_procedure(StoredProcedures.LIST_DOCUMENT_VERSIONS, [record.document_key])
    return result[0] if result else []


@router.get(
    "/{file_id}",
    response_model=FileResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a file record by ID",
    description="Retrieves one file record's metadata (including processing_status) by its unique ID.",
    dependencies=[Depends(require_admin)]
)
async def get_file_by_id(
    file_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
) -> FileResponse:
    """Endpoint to retrieve a single file record's metadata."""
    controller = FileController(db_service)
    return await controller.get_file_by_id(file_id)


@router.get(
    "/{file_id}/status",
    response_model=FileStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Check a file's processing status",
    description=(
        "Lightweight poll target for an in-flight upload: just the processing "
        "status/error and vector_store_file_id, not the rest of the record."
    ),
    dependencies=[Depends(require_admin)]
)
async def get_file_status(
    file_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
) -> FileStatusResponse:
    """Endpoint to check just a file's processing status."""
    controller = FileController(db_service)
    file_record = await controller.get_file_by_id(file_id)
    return FileStatusResponse(
        id=file_record.id,
        processing_status=file_record.processing_status,
        processing_error=file_record.processing_error,
        vector_store_file_id=file_record.vector_store_file_id,
    )


@router.post(
    "/{file_id}/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-parse and re-index a file",
    description=(
        "Drops the file's existing vector chunks and parsed text blob, then queues a "
        "fresh parse-and-index pass against the original stored file. "
        "Useful after an embedding model change or a parsing bug fix. "
        "Returns immediately; poll GET /files/{file_id}/status to track progress."
    ),
    dependencies=[Depends(require_admin)],
)
async def reindex_file(
    file_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    chatbot_queue: MessageQueueService = Depends(get_chatbot_queue),
) -> dict:
    record = await FileController(db_service).get_file_by_id(file_id)
    if not record.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"File {file_id} is deleted and cannot be reindexed.",
        )

    await FileController(db_service).update_processing_status(file_id, ProcessingStatus.PENDING)

    payload: dict = {
        "action_type": "create",
        "file_id": record.id,
        "file_path": record.file_path,
        "name": record.name,
        "mimetype": record.mimetype,
        "user_id": current_user.email,
    }
    if record.document_key:
        payload["document_key"] = record.document_key
        payload["version_no"] = record.version_number
        if record.version_label:
            payload["version_label"] = record.version_label

    await chatbot_queue.send_message(payload)
    await record_audit(
        db_service, current_user.email, ACTION_FILE_REINDEX, f"{file_id}:{record.name}",
        detail={"triggered_by": current_user.email},
    )
    return {"message": f"File {file_id} queued for reindexing.", "file_id": file_id}


@router.get(
    "/{file_id}/download",
    response_class=StreamingResponse,
    summary="Download a file by ID",
    description="Downloads a file's original content from the configured file store using its unique ID.",
    dependencies=[Depends(require_admin)]
)
async def download_file(
    file_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    file_storage_service: FileStorageService = Depends(get_file_storage_service)
) -> StreamingResponse:
    """Endpoint to download a file's original content by its ID."""
    controller = FileController(db_service)
    file_record = await controller.get_file_by_id(file_id)
    # file_path is whatever the backend returned from upload_file: an absolute
    # URL under Azure, a store-relative path under a mounted volume. This
    # normalises both.
    blob_name = extract_blob_path_from_url(file_record.file_path)
    file_stream = await file_storage_service.download_file(blob_name)
    return StreamingResponse(
        file_stream,
        media_type=file_record.mimetype,
        headers={"Content-Disposition": attachment_disposition(file_record.name)},
    )


@router.get(
    "/",
    response_model=list[FileResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve all file records",
    description="Retrieves a list of all file records currently in the system.",
    dependencies=[Depends(require_admin)]
)
async def get_all_files(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> list[FileResponse]:
    """Endpoint to retrieve all file records."""
    controller = FileController(db_service)
    return await controller.get_all_files()


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a file record",
    description="Deletes a file record from the system using its unique ID.",
    dependencies=[Depends(require_admin)]
)
async def delete_file(
    file_id: str,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    settings: Settings = Depends(get_settings),
    file_storage_service: FileStorageService = Depends(get_file_storage_service),
    catalog=Depends(get_kb_catalog),
) -> JSONResponse:
    """Endpoint to delete a file record, or a knowledge-base document.

    `file_id` accepts either identifier, since the two are easily confused:
      * an integer  -> the SQL file record (cascades to its KB document, if any)
      * a UUID      -> a knowledge-base document only (no owning file record)
    """
    controller = FileController(db_service, file_storage_service)
    try:
        kind, identifier = classify_file_id(file_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{file_id}' is neither an integer file ID nor a knowledge-base UUID.",
        ) from exc
    if kind == "sql":
        await controller.delete_file(identifier, current_user.email)
        message = "File record deleted successfully"
    # A KB document is usually owned by a file record. Deleting only the vector chunks would
    # leave that record active and still visible, so resolve the owner and delete the whole thing.
    elif (owning_file_id := await controller.find_file_id_by_vector_store_file_id(identifier)) is not None:
        await controller.delete_file(owning_file_id, current_user.email)
        message = f"Knowledge-base document {file_id} and its file record ({owning_file_id}) deleted successfully"
    else:
        await kb_service.delete_document(settings, identifier)
        message = f"Knowledge-base document {file_id} deleted successfully"
    await record_audit(db_service, current_user.email, ACTION_FILE_DELETE, file_id, detail={"kind": kind})
    if catalog is not None:
        # The document is gone; drop it from the catalog so the bot stops listing it.
        await catalog.refresh()
    return JSONResponse(content={"message": message}, status_code=status.HTTP_200_OK)


@router.patch(
    "/{file_id}/status",
    response_model=dict,
    summary="Update File Management Status (ACTIVE, DISABLE, INACTIVE)",
    description=(
        "Admin-only. Updates file status to ACTIVE, DISABLE, or INACTIVE. "
        "DISABLE/INACTIVE moves the file's chunks to a separate MongoDB collection "
        "so they are excluded from search. ACTIVE moves them back."
    ),
    dependencies=[Depends(require_admin)]
)
async def update_file_status(
    file_id: int,
    status_state: FileLifecycleStatus = Query(..., description="File status: ACTIVE, DISABLE, or INACTIVE"),
    current_user: CurrentUserDep = None,
    db_service: DatabaseService = Depends(get_db_service),
    search: SearchRuntime = Depends(get_search_runtime),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Updates file active state and moves vector chunks between active/disabled collections."""
    is_active = status_state == FileLifecycleStatus.ACTIVE
    chunks_moved = 0

    # Look up vector_store_file_id to identify which chunks to move.
    file_record = await FileController(db_service).get_file_by_id(file_id)
    vector_store_file_id = file_record.vector_store_file_id

    if vector_store_file_id:
        active_col = settings.MONGODB_COLLECTION_NAME
        disabled_col = settings.MONGODB_DISABLED_COLLECTION_NAME
        from_col = disabled_col if is_active else active_col
        to_col = active_col if is_active else disabled_col
        try:
            store = await search.store()
            chunks_moved = await store.move_chunks_by_source_id(vector_store_file_id, from_col, to_col)
        except Exception:
            logger.warning(f"Could not move chunks for file {file_id} ({vector_store_file_id}) in MongoDB.")

    try:
        await db_service.call_procedure(
            StoredProcedures.UPDATE_FILE_STATUS,
            [file_id, is_active, current_user.email]
        )
    except Exception:
        logger.warning(f"Could not update status for file {file_id} in DB.")

    return {
        "file_id": file_id,
        "status": status_state.value,
        "is_active": is_active,
        "chunks_moved": chunks_moved,
        "updated_by": current_user.email,
    }


@router.post(
    "/documents/{document_key}/generate-comparison",
    status_code=status.HTTP_200_OK,
    summary="Generate a prebaked comparison between the two most recent versions",
    description=(
        "Generates a structured comparison summary between the two most recent indexed versions "
        "of a document and stores it as a special comparison chunk in MongoDB. "
        "On the next comparison question (asking about differences between versions), the stored "
        "chunk is retrieved alongside the live content search, giving the LLM a pre-computed diff. "
        "Call this endpoint after uploading and indexing a new version. "
        "Set force=true to regenerate even if a comparison chunk already exists. "
        "Requires COMPARISON_ENABLED=true (default) and both versions to be fully indexed."
    ),
    dependencies=[Depends(require_admin)],
)
async def generate_document_comparison(
    document_key: str,
    force: bool = Query(False, description="Regenerate even if comparison chunks already exist."),
    db_service: DatabaseService = Depends(get_db_service),
    search: SearchRuntime = Depends(get_search_runtime),
    chat_llm: ChatLLM = Depends(get_chat_llm),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.COMPARISON_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Comparison generation is disabled (COMPARISON_ENABLED=False).",
        )
    try:
        return await generate_comparison(db_service, search, chat_llm, settings, document_key, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
