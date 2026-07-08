"""Editable table parsing and validation for Web forms."""

from __future__ import annotations

from typing import Any

from blackboard.excel_store import ExcelBlackboardStore
from blackboard.validators import BlackboardValidationError, validate_row
from tools.cpm_tools import calculate_cpm

WBS_EDIT_FIELDS = (
    "task_id",
    "wbs_code",
    "phase",
    "task_name",
    "duration_days",
    "predecessor_ids",
    "relation_type",
    "lag_days",
)

RESOURCE_EDIT_FIELDS = (
    "task_id",
    "resource_type",
    "resource_name",
    "demand",
    "unit",
    "capacity",
    "period",
)


def parse_indexed_rows(form: dict[str, Any], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Parse rows submitted as field__index inputs."""

    indexes: set[int] = set()
    for key in form:
        if "__" not in key:
            continue
        field, raw_index = key.rsplit("__", 1)
        if field in fields and raw_index.isdigit():
            indexes.add(int(raw_index))

    rows: list[dict[str, Any]] = []
    for index in sorted(indexes):
        row = {field: _first(form.get(f"{field}__{index}")) for field in fields}
        if any(str(value or "").strip() for value in row.values()):
            rows.append(row)
    return rows


def save_wbs_rows(store: ExcelBlackboardStore, form: dict[str, Any]) -> list[dict[str, str]]:
    rows = parse_indexed_rows(form, WBS_EDIT_FIELDS)
    existing_rows = store.read_rows("wbs_tasks_final")
    merged = merge_with_existing(existing_rows, rows, key_field="task_id")
    errors = validate_wbs_rows(merged)
    if errors:
        return errors
    store.replace_rows("wbs_tasks_final", merged)
    return []


def save_resource_rows(store: ExcelBlackboardStore, form: dict[str, Any]) -> list[dict[str, str]]:
    rows = parse_indexed_rows(form, RESOURCE_EDIT_FIELDS)
    existing_rows = store.read_rows("resource_plan_final")
    merged = merge_with_existing(existing_rows, rows, key_field="__row_index")
    task_ids = {str(row.get("task_id") or "").strip() for row in store.read_rows("wbs_tasks_final")}
    errors = validate_resource_rows(merged, task_ids)
    if errors:
        return errors
    store.replace_rows("resource_plan_final", merged)
    return []


def merge_with_existing(
    existing_rows: list[dict[str, Any]],
    submitted_rows: list[dict[str, Any]],
    *,
    key_field: str,
) -> list[dict[str, Any]]:
    """Merge editable fields into existing row metadata where possible."""

    if key_field == "__row_index":
        merged = []
        for index, submitted in enumerate(submitted_rows):
            base = dict(existing_rows[index]) if index < len(existing_rows) else {}
            base.update(clean_resource_row(submitted))
            merged.append(base)
        return merged

    existing_by_key = {str(row.get(key_field) or ""): row for row in existing_rows}
    merged = []
    for submitted in submitted_rows:
        key = str(submitted.get(key_field) or "").strip()
        base = dict(existing_by_key.get(key) or {})
        base.update(clean_wbs_row(submitted))
        merged.append(base)
    return merged


def validate_wbs_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    task_ids = {str(row.get("task_id") or "").strip() for row in rows if row.get("task_id")}
    for index, row in enumerate(rows, start=1):
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            errors.append(_error(index, "task_id", "task_id 不能为空。"))
        elif task_id in seen:
            errors.append(_error(index, "task_id", f"task_id 重复：{task_id}"))
        seen.add(task_id)
        try:
            duration = int(row.get("duration_days") or 0)
            if duration <= 0:
                errors.append(_error(index, "duration_days", "duration_days 必须大于 0。"))
        except (TypeError, ValueError):
            errors.append(_error(index, "duration_days", "duration_days 必须是整数。"))
        try:
            int(row.get("lag_days") or 0)
        except (TypeError, ValueError):
            errors.append(_error(index, "lag_days", "lag_days 必须是整数。"))
        relation_type = str(row.get("relation_type") or "FS").upper()
        if relation_type not in {"FS", "SS"}:
            errors.append(_error(index, "relation_type", "relation_type 仅支持 FS 或 SS。"))
        for predecessor in _split_predecessors(row.get("predecessor_ids")):
            if predecessor not in task_ids:
                errors.append(_error(index, "predecessor_ids", f"前置任务不存在：{predecessor}"))
        try:
            validate_row("wbs_tasks_final", row)
        except BlackboardValidationError as exc:
            errors.append(_error(index, "row", str(exc)))
    if not errors:
        try:
            calculate_cpm(rows)
        except Exception as exc:
            errors.append(_error(0, "predecessor_ids", f"前置关系无法排程：{exc}"))
    return errors


def validate_resource_rows(rows: list[dict[str, Any]], task_ids: set[str]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        task_id = str(row.get("task_id") or "").strip()
        if task_id not in task_ids:
            errors.append(_error(index, "task_id", f"资源引用的 task_id 不存在：{task_id}"))
        for field in ("demand", "capacity"):
            try:
                value = float(row.get(field) or 0)
                if value < 0:
                    errors.append(_error(index, field, f"{field} 不能小于 0。"))
            except (TypeError, ValueError):
                errors.append(_error(index, field, f"{field} 必须是数字。"))
        try:
            validate_row("resource_plan_final", row)
        except BlackboardValidationError as exc:
            errors.append(_error(index, "row", str(exc)))
    return errors


def clean_wbs_row(row: dict[str, Any]) -> dict[str, Any]:
    output = {field: str(row.get(field) or "").strip() for field in WBS_EDIT_FIELDS}
    try:
        output["duration_days"] = int(float(output["duration_days"] or 0))
    except ValueError:
        pass
    try:
        output["lag_days"] = int(float(output["lag_days"] or 0))
    except ValueError:
        pass
    output["relation_type"] = (output["relation_type"] or "FS").upper()
    output.setdefault("source", "web_edit")
    output.setdefault("confidence", "1.00")
    output.setdefault("note", "Edited in Web UI")
    output.setdefault("owner_agent", "web_user")
    return output


def clean_resource_row(row: dict[str, Any]) -> dict[str, Any]:
    output = {field: str(row.get(field) or "").strip() for field in RESOURCE_EDIT_FIELDS}
    try:
        demand: Any = float(output["demand"] or 0)
    except ValueError:
        demand = output["demand"]
    try:
        capacity: Any = float(output["capacity"] or 0)
    except ValueError:
        capacity = output["capacity"]
    output["demand"] = demand
    output["capacity"] = capacity
    output["conflict_flag"] = (
        demand > capacity if isinstance(demand, float) and isinstance(capacity, float) and capacity else False
    )
    output.setdefault("source", "web_edit")
    output.setdefault("confidence", "1.00")
    output.setdefault("note", "Edited in Web UI")
    output.setdefault("owner_agent", "web_user")
    return output


def _split_predecessors(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else ""
    return value


def _error(row: int, field: str, message: str) -> dict[str, str]:
    return {"row": str(row), "field": field, "message": message}
