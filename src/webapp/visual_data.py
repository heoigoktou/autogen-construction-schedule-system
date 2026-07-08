"""Build data-driven visualization payloads from the Excel blackboard."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from blackboard.excel_store import ExcelBlackboardStore


VISUAL_EXPORTS = {
    "visual_data": "current_schedule_data.json",
    "baseline_visual_data": "baseline_schedule_data.json",
    "schedule_json": "schedule_initial.json",
    "cpm_json": "cpm_analysis.json",
    "resource_load_json": "resource_load_daily.json",
    "milestones_json": "milestone_check.json",
    "adjustments_json": "adjustment_history.json",
}


def visual_export_path(outputs_root: Path, key: str) -> Path:
    relative = VISUAL_EXPORTS[key]
    return outputs_root / "schedule" / relative


def export_visual_json_artifacts(store: ExcelBlackboardStore, outputs_root: Path) -> dict[str, Path]:
    """Write reusable JSON artifacts for the visual dashboard and delivery package."""

    schedule_dir = outputs_root / "schedule"
    schedule_dir.mkdir(parents=True, exist_ok=True)

    data = build_visual_payload(store)
    current_path = visual_export_path(outputs_root, "visual_data")
    write_json(current_path, data)

    baseline_path = visual_export_path(outputs_root, "baseline_visual_data")
    if data["tasks"] and not baseline_path.exists():
        write_json(baseline_path, data)

    table_exports = {
        "schedule_json": store.read_rows("schedule_initial"),
        "cpm_json": store.read_rows("cpm_analysis"),
        "resource_load_json": store.read_rows("resource_load_daily"),
        "milestones_json": store.read_rows("milestone_check"),
        "adjustments_json": {
            "events": store.read_rows("event_log"),
            "plans": store.read_rows("adjustment_plan"),
        },
    }
    written = {
        "visual_data": current_path,
        "baseline_visual_data": baseline_path,
    }
    for key, rows in table_exports.items():
        path = visual_export_path(outputs_root, key)
        write_json(path, rows)
        written[key] = path
    return written


def build_visual_payload(
    store: ExcelBlackboardStore,
    *,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize blackboard rows into a stable browser-facing JSON payload."""

    wbs_rows = store.read_rows("wbs_tasks_final")
    schedule_rows = store.read_rows("schedule_initial")
    cpm_rows = store.read_rows("cpm_analysis")
    resource_rows = store.read_rows("resource_plan_final")
    resource_load_rows = store.read_rows("resource_load_daily")
    milestone_rows = store.read_rows("milestone_check")
    event_rows = store.read_rows("event_log")
    adjustment_rows = store.read_rows("adjustment_plan")

    wbs_by_task = {str(row.get("task_id") or ""): row for row in wbs_rows}
    cpm_by_task = {str(row.get("task_id") or ""): row for row in cpm_rows}
    resources_by_task: dict[str, list[dict[str, Any]]] = {}
    for row in resource_rows:
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            continue
        resources_by_task.setdefault(task_id, []).append(normalize_resource(row))

    tasks = []
    for row in schedule_rows:
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            continue
        wbs = wbs_by_task.get(task_id, {})
        cpm = cpm_by_task.get(task_id, {})
        tasks.append(
            {
                "task_id": task_id,
                "task_name": safe_text(row.get("task_name") or wbs.get("task_name")),
                "phase": safe_text(row.get("phase") or wbs.get("phase") or "未分组"),
                "section": safe_text(wbs.get("section") or wbs.get("floor_or_area") or "未分区"),
                "work_package": safe_text(wbs.get("work_package") or ""),
                "planned_start": safe_date(row.get("planned_start")),
                "planned_finish": safe_date(row.get("planned_finish")),
                "duration_days": safe_number(row.get("duration_days")),
                "predecessor_ids": safe_text(row.get("predecessor_ids") or wbs.get("predecessor_ids")),
                "total_float": safe_number(row.get("total_float") if row.get("total_float") is not None else cpm.get("total_float")),
                "free_float": safe_number(cpm.get("free_float")),
                "critical_path_no": safe_text(cpm.get("critical_path_no")),
                "is_critical": safe_bool(row.get("is_critical") if row.get("is_critical") is not None else cpm.get("is_critical")),
                "resource_summary": safe_text(row.get("resource_summary")),
                "status": safe_text(row.get("status")),
                "resources": resources_by_task.get(task_id, []),
            }
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tasks": tasks,
        "resources": [normalize_resource(row) for row in resource_rows],
        "resource_load": [normalize_resource_load(row) for row in resource_load_rows],
        "milestones": [normalize_milestone(row) for row in milestone_rows],
        "adjustment_history": build_adjustment_history(event_rows, adjustment_rows),
        "baseline": baseline or {},
        "filters": {
            "phases": sorted({task["phase"] for task in tasks if task["phase"]}),
            "sections": sorted({task["section"] for task in tasks if task["section"]}),
            "resources": sorted({safe_text(row.get("resource_name")) for row in resource_rows if row.get("resource_name")}),
        },
    }


def build_adjustment_history(
    event_rows: list[dict[str, Any]],
    adjustment_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plans_by_event: dict[str, list[dict[str, Any]]] = {}
    for row in adjustment_rows:
        event_id = safe_text(row.get("event_id"))
        plans_by_event.setdefault(event_id, []).append(
            {
                "plan_id": safe_text(row.get("plan_id")),
                "measure": safe_text(row.get("measure")),
                "recovered_days": safe_number(row.get("recovered_days")),
                "cost_level": safe_text(row.get("cost_level")),
                "risk_level": safe_text(row.get("risk_level")),
                "score": safe_number(row.get("score")),
                "selected_flag": safe_bool(row.get("selected_flag")),
                "note": safe_text(row.get("note")),
                "created_at": safe_text(row.get("created_at")),
            }
        )

    history = []
    for row in event_rows:
        event_id = safe_text(row.get("event_id"))
        history.append(
            {
                "event_id": event_id,
                "event_type": safe_text(row.get("event_type")),
                "related_task": safe_text(row.get("related_task")),
                "impact_days": safe_number(row.get("impact_days")),
                "priority": safe_text(row.get("priority")),
                "status": safe_text(row.get("status")),
                "created_at": safe_text(row.get("created_at")),
                "source": safe_text(row.get("source")),
                "note": safe_text(row.get("note")),
                "plans": plans_by_event.get(event_id, []),
            }
        )
    return sorted(history, key=lambda item: item.get("created_at") or "", reverse=True)


def normalize_resource(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": safe_text(row.get("task_id")),
        "resource_type": safe_text(row.get("resource_type")),
        "resource_name": safe_text(row.get("resource_name")),
        "demand": safe_number(row.get("demand")),
        "unit": safe_text(row.get("unit")),
        "capacity": safe_number(row.get("capacity")),
        "period": safe_text(row.get("period")),
        "conflict_flag": safe_bool(row.get("conflict_flag")),
        "note": safe_text(row.get("note")),
    }


def normalize_resource_load(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date_or_period": safe_text(row.get("date_or_period")),
        "resource_type": safe_text(row.get("resource_type")),
        "resource_name": safe_text(row.get("resource_name")),
        "demand": safe_number(row.get("demand")),
        "capacity": safe_number(row.get("capacity")),
        "load_rate": safe_number(row.get("load_rate")),
        "conflict_flag": safe_bool(row.get("conflict_flag")),
        "related_tasks": safe_text(row.get("related_tasks")),
    }


def normalize_milestone(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "milestone_id": safe_text(row.get("milestone_id")),
        "milestone_name": safe_text(row.get("milestone_name")),
        "target_date": safe_date(row.get("target_date")),
        "actual_date": safe_date(row.get("actual_date")),
        "result": safe_text(row.get("result")),
        "severity": safe_text(row.get("severity")),
        "suggestion": safe_text(row.get("suggestion")),
    }


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def safe_date(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return safe_text(value)


def safe_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).replace("%", "").strip())
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是", "通过", "critical", "关键", "超载", "冲突"}
