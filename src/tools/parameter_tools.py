"""Parameter checklist utilities for generation-side agents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

CN_TZ = timezone(timedelta(hours=8))

STATUS_SOURCE_EXACT = "source_exact"
STATUS_SOURCE_TABLE = "source_table"
STATUS_DERIVED = "derived"
STATUS_INFERRED = "inferred"
STATUS_MISSING = "missing"
STATUS_CONFLICT = "conflict"

STATUS_CONFIRMED = {
    STATUS_SOURCE_EXACT,
    STATUS_SOURCE_TABLE,
    STATUS_DERIVED,
    "confirmed",
    "\u5df2\u83b7\u53d6",
    "\u5df2\u901a\u8fc7",
    "\u5df2\u751f\u6210",
    "\u5df2\u6821\u6838",
    "\u5df2\u6709\u4efb\u52a1\u4e66\u4f9d\u636e",
}
STATUS_MISSING_SET = {STATUS_MISSING, "\u7f3a\u5931"}
STATUS_PENDING = {"pending", "\u5f85\u68c0\u67e5", "\u5f85\u786e\u8ba4"}
STATUS_NEEDS_CONFIRMATION = {
    STATUS_INFERRED,
    "\u9700\u4eba\u5de5\u786e\u8ba4",
    "\u5f85\u786e\u8ba4",
}
STATUS_BLOCKED = {STATUS_CONFLICT}


def now_iso() -> str:
    """Return current China-time ISO text."""

    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def summarize_parameter_status(rows: list[dict[str, object]]) -> dict[str, int]:
    """Return a simple status count for parameter checklist rows."""

    summary: dict[str, int] = {}
    for row in rows:
        status = str(row.get("extraction_status") or row.get("status") or "unset")
        summary[status] = summary.get(status, 0) + 1
    return summary


def classify_parameter_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Classify checklist rows by resolver status."""

    missing = []
    needs_confirmation = []
    confirmed = []
    blocked = []
    for row in rows:
        status = str(row.get("extraction_status") or row.get("status") or "").strip()
        required = str(row.get("required") or "").strip().lower()
        is_required = required in {"yes", "true", "1", "\u662f"}
        if status in STATUS_BLOCKED:
            blocked.append(row)
        elif is_required and status in STATUS_MISSING_SET | STATUS_PENDING:
            missing.append(row)
        elif status in STATUS_NEEDS_CONFIRMATION:
            needs_confirmation.append(row)
        elif status in STATUS_CONFIRMED:
            confirmed.append(row)
    return {
        "missing": missing,
        "needs_confirmation": needs_confirmation,
        "confirmed": confirmed,
        "blocked": blocked,
    }


def build_project_parameter_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build project parameter rows from usable checklist rows."""

    updated_at = now_iso()
    output = []
    for row in rows:
        status = str(row.get("extraction_status") or row.get("status") or "").strip()
        if status not in STATUS_CONFIRMED | STATUS_NEEDS_CONFIRMATION:
            continue
        confidence = row.get("confidence") or _confidence_for_status(status)
        value = row.get("value") or row.get("note") or row.get("source") or "pending"
        output.append(
            {
                "parameter_id": row.get("parameter_id"),
                "value": value,
                "unit": row.get("unit") or "",
                "source": row.get("source") or "parameter_checklist",
                "evidence_id": row.get("evidence_id") or "",
                "extraction_status": status,
                "confidence": confidence,
                "confirmed_by": row.get("owner_agent") or "data_parser_agent",
                "updated_at": updated_at,
                "created_by": "data_parser_agent",
                "note": row.get("note") or f"generated from parameter status {status}",
            }
        )
    return output


def _confidence_for_status(status: str) -> str:
    if status == STATUS_SOURCE_TABLE:
        return "0.96"
    if status == STATUS_SOURCE_EXACT:
        return "0.94"
    if status == STATUS_DERIVED:
        return "0.82"
    if status == STATUS_INFERRED:
        return "0.65"
    return "0.70"
