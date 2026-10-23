"""Audit trail for the parsing-indexing worker.
Params mirror insert_audit_log / filter_audit_log (scripts/mssql/09_audit_trace_hash.sql)."""
import json
import logging

from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures

logger = logging.getLogger("app.audit")

MAX_TARGET_CHARS = 500
MAX_DETAIL_CHARS = 2000

ACTION_FILE_INGEST = "file_ingest"


def _audit_params(actor: str, action: str, target: str | None, result: str, detail: dict | None) -> list:
    return [
        actor, action, (target or "")[:MAX_TARGET_CHARS] or None, result,
        json.dumps(detail, ensure_ascii=False, default=str)[:MAX_DETAIL_CHARS] if detail else None,
    ]


async def record_ingest(
    db_service: DatabaseService,
    actor: str,
    target: str,
    result: str = "ok",
    detail: dict | None = None,
) -> None:
    """Write one file_ingest row to audit_log. Never raises — a failed write is only logged."""
    logger.info(json.dumps(
        {"audit": ACTION_FILE_INGEST, "actor": actor, "target": target, "result": result, "detail": detail},
        ensure_ascii=False, default=str,
    ))
    try:
        await db_service.call_procedure(
            StoredProcedures.INSERT_AUDIT_LOG,
            _audit_params(actor, ACTION_FILE_INGEST, target, result, detail),
        )
    except Exception:
        logger.exception("Could not store audit event file_ingest for %s.", target)
