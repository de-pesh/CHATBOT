"""The admin audit trail: one row per thing an administrator did, and the same event as a JSON log line.
Params for insert_audit_log / filter_audit_log (scripts/mssql/09_audit_trace_hash.sql)."""
import json
import logging

from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures

logger = logging.getLogger("app.audit")

MAX_TARGET_CHARS = 500  # dbo.audit_log.target is NVARCHAR(500)
MAX_DETAIL_CHARS = 2000

ACTION_FILE_UPLOAD = "file_upload"
ACTION_FILE_DELETE = "file_delete"
ACTION_FILE_DUPLICATE = "file_duplicate"
ACTION_FILE_REINDEX = "file_reindex"
ACTION_AUDIT_PURGE = "audit_purge"
ACTION_VERSIONS_SYNC = "versions_sync"
ACTION_HASH_BACKFILL = "hash_backfill"
ACTION_USER_CREATE = "user_create"
ACTION_PASSWORD_CHANGE = "password_change"


def audit_params(actor: str, action: str, target: str | None, result: str, detail: dict | None) -> list:
    """insert_audit_log: [actor, action, target, result, detail json]."""
    return [
        actor, action, (target or "")[:MAX_TARGET_CHARS] or None, result,
        json.dumps(detail, ensure_ascii=False, default=str)[:MAX_DETAIL_CHARS] if detail else None,
    ]


def audit_list_params(action: str | None, actor: str | None, page_number: int, page_size: int) -> list:
    return [action, actor, page_number, page_size]


async def record_audit(
    db_service: DatabaseService, actor: str, action: str, target: str | None = None,
    result: str = "ok", detail: dict | None = None,
    outcome: str | None = None, model: str | None = None,
    total_ms: int | None = None, top_score: float | None = None,
    trace: dict | None = None,
) -> None:
    """Logs and stores a unified audit and trace event.
    Combines Audit Log & Audit Trace into a single unified record.
    """
    merged_detail = dict(detail or {})
    if trace:
        merged_detail["trace"] = trace
    if outcome:
        merged_detail["outcome"] = outcome
    if model:
        merged_detail["model"] = model
    if total_ms is not None:
        merged_detail["total_ms"] = total_ms
    if top_score is not None:
        merged_detail["top_score"] = top_score

    logger.info(json.dumps(
        {"audit": action, "actor": actor, "target": target, "result": result, "detail": merged_detail},
        ensure_ascii=False, default=str,
    ))
    try:
        await db_service.call_procedure(StoredProcedures.INSERT_AUDIT_LOG, audit_params(actor, action, target, result, merged_detail))
    except Exception:
        logger.exception("Could not store the audit event %s by %s.", action, actor)
