"""The numbers GET /admin/metrics reports, from the row dbo.chat_metrics returns (scripts/mssql/09_audit_trace_hash.sql)."""


def _rate(part, whole) -> float | None:
    return round(part / whole, 4) if whole else None


def metrics_report(days: int, row: dict) -> dict:
    """The row as the endpoint returns it, plus the three rates an admin reads first.

    error_rate is errors over all turns; no_evidence_rate is over turns that reached the search (answered
    or stopped by the evidence gate, not small talk); negative_feedback_rate is over rated turns.
    A rate is None when there was nothing to divide by."""
    answered, no_evidence = row.get("answered") or 0, row.get("no_evidence") or 0
    positive, negative = row.get("feedback_positive") or 0, row.get("feedback_negative") or 0
    return {
        "days": days,
        **row,
        "error_rate": _rate(row.get("errors") or 0, row.get("turns") or 0),
        "no_evidence_rate": _rate(no_evidence, answered + no_evidence),
        "negative_feedback_rate": _rate(negative, positive + negative),
    }
