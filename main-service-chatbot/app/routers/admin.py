"""Administrator-only views over what the service has been doing: the audit trail, the metrics over chat
turns, the trace of one turn, and retention. All of it reads what scripts/mssql/09_audit_trace_hash.sql stores."""
import contextlib
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import Settings, get_settings
from app.controllers.hash_backfill import backfill_content_hashes
from app.controllers.version_controller import sync_all
from app.dependencies import (
    CurrentUserDep,
    get_db_service,
    get_file_storage_service,
    require_admin,
    require_real_accounts,
)
from app.logic.audit import (
    ACTION_AUDIT_PURGE,
    ACTION_HASH_BACKFILL,
    ACTION_VERSIONS_SYNC,
    audit_list_params,
    record_audit,
)
from app.logic.chat_records import page_of
from app.logic.metrics import metrics_report
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_real_accounts), Depends(require_admin)])

DbDep = Annotated[DatabaseService, Depends(get_db_service)]


@router.get(
    "/audit",
    summary="Admin audit trail",
    description="Who uploaded, deleted or was refused what, newest first. Filter by action (file_upload, "
                "file_delete, file_duplicate, audit_purge) or actor (an email).",
)
async def get_audit_log(
    db_service: DbDep,
    action: str | None = Query(None, max_length=50),
    actor: str | None = Query(None, max_length=255),
    page_number: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict:
    rows, total = page_of(await db_service.call_procedure(
        StoredProcedures.FILTER_AUDIT_LOG, audit_list_params(action, actor, page_number, page_size),
    ))
    return {"total_records": total, "page_number": page_number, "page_size": page_size, "data": rows}


@router.get(
    "/metrics",
    summary="Chat quality and health metrics",
    description="Turns, errors, how many were answered, stopped by the evidence gate or small talk, latency, "
                "thumbs up/down and grounding verdicts over the last `days` days.",
)
async def get_metrics(db_service: DbDep, days: int = Query(7, ge=1, le=365)) -> dict:
    result = await db_service.call_procedure(StoredProcedures.CHAT_METRICS, [days])
    return metrics_report(days, dict(result[0][0]) if result and result[0] else {})


@router.get(
    "/chat-trace/{history_id}",
    summary="Why a chat turn went the way it did",
    description="The question, the answer, the queries searched, the passages retrieved and kept after the "
                "rerank, the outcome, the model and configuration version, and any feedback on it.",
)
async def get_chat_trace(history_id: str, db_service: DbDep) -> dict:
    result = await db_service.call_procedure(StoredProcedures.GET_CHATBOT_TRACE, [history_id])
    rows = result[0] if result else []
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Chat turn {history_id} not found.")
    row = dict(rows[0])
    if isinstance(row.get("trace"), str):
        with contextlib.suppress(ValueError):
            row["trace"] = json.loads(row["trace"])
    return row


@router.post(
    "/purge",
    summary="Apply the retention settings now",
    description="Deletes audit-log and chat-trace rows older than AUDIT_RETENTION_DAYS / TRACE_RETENTION_DAYS "
                "(0 keeps that table). Also runs once at startup.",
)
async def purge(db_service: DbDep, current_user: CurrentUserDep, settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    removed = await purge_old_rows(db_service, settings)
    await record_audit(db_service, current_user.email, ACTION_AUDIT_PURGE, detail=removed)
    return removed


@router.post(
    "/versions/sync",
    summary="Bring every document's versions up to date",
    description="Gives files uploaded before versioning their document key and version number (oldest first), then "
                "re-elects the latest version of every document and pushes it onto the chunks. Safe to run any time; "
                "run it once after upgrading, and to repair a document whose latest version looks wrong.",
)
async def sync_versions(
    db_service: DbDep, current_user: CurrentUserDep, settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    report = await sync_all(db_service, settings)
    await record_audit(db_service, current_user.email, ACTION_VERSIONS_SYNC, detail=report)
    return report


@router.post(
    "/files/backfill-hashes",
    summary="Hash files uploaded before duplicate detection",
    description="Files uploaded before content hashing have no hash, so re-uploading one of them is not refused as a "
                "duplicate. This reads each such file from the store and records its hash. Safe to run again; a file "
                "that cannot be read is listed and retried next time.",
)
async def backfill_hashes(
    db_service: DbDep, current_user: CurrentUserDep,
    storage: Annotated[FileStorageService, Depends(get_file_storage_service)],
) -> dict:
    report = await backfill_content_hashes(db_service, storage)
    await record_audit(
        db_service, current_user.email, ACTION_HASH_BACKFILL,
        detail={key: value for key, value in report.items() if key != "unreadable"},
    )
    return report


async def purge_old_rows(db_service: DatabaseService, settings: Settings) -> dict:
    """{audit_rows_removed, trace_rows_removed} after deleting what the retention settings say to."""
    result = await db_service.call_procedure(
        StoredProcedures.PURGE_AUDIT_AND_TRACE, [settings.AUDIT_RETENTION_DAYS, settings.TRACE_RETENTION_DAYS],
    )
    return dict(result[0][0]) if result and result[0] else {"audit_rows_removed": 0, "trace_rows_removed": 0}
