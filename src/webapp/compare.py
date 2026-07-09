"""Build baseline-vs-adjusted comparison data for the Web compare page."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any


def build_compare_payload(current: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    baseline = baseline or {}
    current_tasks = list(current.get("tasks") or [])
    baseline_tasks = list(baseline.get("tasks") or [])
    baseline_by_id = {str(task.get("task_id") or ""): task for task in baseline_tasks}
    current_by_id = {str(task.get("task_id") or ""): task for task in current_tasks}

    task_diffs = []
    all_dates: list[date] = []
    for task in current_tasks:
        base = baseline_by_id.get(str(task.get("task_id") or ""), {})
        diff = build_task_diff(base, task)
        task_diffs.append(diff)
        all_dates.extend([value for value in (diff["base_start_date"], diff["base_finish_date"], diff["adjusted_start_date"], diff["adjusted_finish_date"]) if value])

    for task in baseline_tasks:
        task_id = str(task.get("task_id") or "")
        if task_id and task_id not in current_by_id:
            diff = build_task_diff(task, {})
            task_diffs.append(diff)
            all_dates.extend([value for value in (diff["base_start_date"], diff["base_finish_date"]) if value])

    range_start = min(all_dates) if all_dates else None
    range_finish = max(all_dates) if all_dates else None
    total_days = max(1, (range_finish - range_start).days + 1) if range_start and range_finish else 1
    for diff in task_diffs:
        diff["base_left"] = percent_offset(diff["base_start_date"], range_start, total_days)
        diff["base_width"] = percent_width(diff["base_start_date"], diff["base_finish_date"], total_days)
        diff["adjusted_left"] = percent_offset(diff["adjusted_start_date"], range_start, total_days)
        diff["adjusted_width"] = percent_width(diff["adjusted_start_date"], diff["adjusted_finish_date"], total_days)
        diff["delay_left"] = min(100, diff["adjusted_left"] + diff["adjusted_width"])
        diff["delay_width"] = min(20, max(0, abs(diff["finish_delta_days"]) * 1.2))

    task_diffs = sorted(
        task_diffs,
        key=lambda item: (
            item["adjusted_start_date"] or item["base_start_date"] or date.max,
            item["task_id"],
        ),
    )
    changed_tasks = [item for item in task_diffs if item["changed"]]
    delayed_tasks = [item for item in task_diffs if item["finish_delta_days"] > 0]
    critical_added = [
        item
        for item in task_diffs
        if item["adjusted_critical"] and not item["base_critical"]
    ]

    baseline_summary = summarize_plan(baseline_tasks)
    adjusted_summary = summarize_plan(current_tasks)
    baseline_finish_date = baseline_summary.pop("finish_date")
    adjusted_finish_date = adjusted_summary.pop("finish_date")
    baseline_summary.pop("start_date")
    adjusted_summary.pop("start_date")
    baseline_resource = summarize_resource_conflicts(baseline)
    adjusted_resource = summarize_resource_conflicts(current)
    resource_diffs = build_resource_diffs(baseline, current)
    milestone_diffs = build_milestone_diffs(baseline.get("milestones") or [], current.get("milestones") or [])
    for diff in task_diffs:
        diff.pop("base_start_date", None)
        diff.pop("base_finish_date", None)
        diff.pop("adjusted_start_date", None)
        diff.pop("adjusted_finish_date", None)

    return {
        "schema_version": 1,
        "baseline": baseline_summary,
        "adjusted": adjusted_summary,
        "metrics": {
            "duration_delta_days": adjusted_summary["duration_days"] - baseline_summary["duration_days"],
            "finish_delta_days": date_delta(adjusted_finish_date, baseline_finish_date),
            "critical_added_count": len(critical_added),
            "delayed_task_count": len(delayed_tasks),
            "resource_conflict_delta": adjusted_resource["conflict_count"] - baseline_resource["conflict_count"],
            "resource_conflict_count": adjusted_resource["conflict_count"],
        },
        "filters": {
            "phases": sorted({item["phase"] for item in task_diffs if item["phase"]}),
        },
        "timeline": {
            "start": format_date(range_start),
            "finish": format_date(range_finish),
            "labels": month_labels(range_start, range_finish),
        },
        "tasks": task_diffs,
        "changed_tasks": changed_tasks,
        "delayed_tasks": delayed_tasks,
        "critical_paths": {
            "baseline": [format_path_task(task) for task in baseline_tasks if bool(task.get("is_critical"))][:14],
            "adjusted": [format_path_task(task) for task in current_tasks if bool(task.get("is_critical"))][:14],
            "added": [item["task_id"] for item in critical_added],
            "removed": [
                item["task_id"]
                for item in task_diffs
                if item["base_critical"] and not item["adjusted_critical"]
            ],
        },
        "resources": resource_diffs,
        "milestones": milestone_diffs,
        "has_baseline": bool(baseline_tasks),
    }


def build_task_diff(base: dict[str, Any], adjusted: dict[str, Any]) -> dict[str, Any]:
    task_id = str(adjusted.get("task_id") or base.get("task_id") or "")
    base_start = parse_date(base.get("planned_start"))
    base_finish = parse_date(base.get("planned_finish"))
    adjusted_start = parse_date(adjusted.get("planned_start"))
    adjusted_finish = parse_date(adjusted.get("planned_finish"))
    start_delta = date_delta(adjusted_start, base_start)
    finish_delta = date_delta(adjusted_finish, base_finish)
    base_critical = bool(base.get("is_critical"))
    adjusted_critical = bool(adjusted.get("is_critical"))
    if adjusted and not base:
        critical_change = "新增工序"
    elif base and not adjusted:
        critical_change = "已删除"
    elif adjusted_critical and not base_critical:
        critical_change = "新增关键"
    elif base_critical and not adjusted_critical:
        critical_change = "退出关键"
    elif adjusted_critical:
        critical_change = "保持关键"
    else:
        critical_change = "未变化"
    return {
        "task_id": task_id,
        "task_name": str(adjusted.get("task_name") or base.get("task_name") or ""),
        "phase": str(adjusted.get("phase") or base.get("phase") or ""),
        "base_start": format_date(base_start),
        "base_finish": format_date(base_finish),
        "adjusted_start": format_date(adjusted_start),
        "adjusted_finish": format_date(adjusted_finish),
        "base_start_date": base_start,
        "base_finish_date": base_finish,
        "adjusted_start_date": adjusted_start,
        "adjusted_finish_date": adjusted_finish,
        "start_delta_days": start_delta,
        "finish_delta_days": finish_delta,
        "base_critical": base_critical,
        "adjusted_critical": adjusted_critical,
        "critical_change": critical_change,
        "changed": bool(start_delta or finish_delta or critical_change not in {"未变化", "保持关键"}),
        "change_reason": infer_change_reason(start_delta, finish_delta, critical_change),
    }


def summarize_plan(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    starts = [parse_date(task.get("planned_start")) for task in tasks]
    finishes = [parse_date(task.get("planned_finish")) for task in tasks]
    starts = [value for value in starts if value]
    finishes = [value for value in finishes if value]
    start = min(starts) if starts else None
    finish = max(finishes) if finishes else None
    return {
        "name": "基准方案" if tasks else "无基准方案",
        "task_count": len(tasks),
        "start_date": start,
        "finish_date": finish,
        "start": format_date(start),
        "finish": format_date(finish),
        "duration_days": (finish - start).days + 1 if start and finish else 0,
    }


def summarize_resource_conflicts(payload: dict[str, Any]) -> dict[str, Any]:
    rows = list(payload.get("resource_load") or [])
    if not rows:
        rows = list(payload.get("resources") or [])
    return {"conflict_count": sum(1 for row in rows if bool(row.get("conflict_flag")))}


def build_resource_diffs(baseline: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    base = aggregate_resources(baseline)
    adjusted = aggregate_resources(current)
    names = sorted(set(base) | set(adjusted))
    rows = []
    for name in names:
        b = base.get(name, {"demand": 0, "capacity": 0, "conflict": False})
        a = adjusted.get(name, {"demand": 0, "capacity": 0, "conflict": False})
        rows.append(
            {
                "resource_name": name,
                "baseline_demand": round(float(b["demand"]), 2),
                "adjusted_demand": round(float(a["demand"]), 2),
                "capacity": round(max(float(b["capacity"]), float(a["capacity"])), 2),
                "delta": round(float(a["demand"]) - float(b["demand"]), 2),
                "conflict": bool(a["conflict"]),
                "load_percent": min(100, int((float(a["demand"]) / max(float(a["capacity"]), 1)) * 100)),
            }
        )
    return sorted(rows, key=lambda row: (not row["conflict"], -abs(row["delta"]), row["resource_name"]))[:12]


def aggregate_resources(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = list(payload.get("resource_load") or [])
    if not rows:
        rows = list(payload.get("resources") or [])
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("resource_name") or "").strip()
        if not name:
            continue
        current = output.setdefault(name, {"demand": 0.0, "capacity": 0.0, "conflict": False})
        current["demand"] += number(row.get("demand"))
        current["capacity"] = max(float(current["capacity"]), number(row.get("capacity")))
        current["conflict"] = bool(current["conflict"] or row.get("conflict_flag"))
    return output


def build_milestone_diffs(
    baseline_rows: list[dict[str, Any]],
    adjusted_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_by_key = {milestone_key(row): row for row in baseline_rows}
    adjusted_by_key = {milestone_key(row): row for row in adjusted_rows}
    rows = []
    for key in sorted(set(baseline_by_key) | set(adjusted_by_key)):
        base = baseline_by_key.get(key, {})
        adjusted = adjusted_by_key.get(key, {})
        base_date = parse_date(base.get("actual_date"))
        adjusted_date = parse_date(adjusted.get("actual_date"))
        rows.append(
            {
                "milestone_name": str(adjusted.get("milestone_name") or base.get("milestone_name") or key),
                "target_date": str(adjusted.get("target_date") or base.get("target_date") or "-"),
                "baseline_actual": format_date(base_date),
                "adjusted_actual": format_date(adjusted_date),
                "delta_days": date_delta(adjusted_date, base_date),
                "status": str(adjusted.get("result") or base.get("result") or "-"),
            }
        )
    return rows


def task_diffs_to_csv(compare: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["任务编号", "工序名称", "阶段", "基准开始", "基准完成", "调整开始", "调整完成", "偏差天数", "关键线路变化", "变化原因"])
    for row in compare.get("tasks", []):
        writer.writerow([
            row["task_id"],
            row["task_name"],
            row["phase"],
            row["base_start"],
            row["base_finish"],
            row["adjusted_start"],
            row["adjusted_finish"],
            row["finish_delta_days"],
            row["critical_change"],
            row["change_reason"],
        ])
    return output.getvalue()


def infer_change_reason(start_delta: int, finish_delta: int, critical_change: str) -> str:
    reasons = []
    if finish_delta > 0:
        reasons.append("完工后移")
    elif finish_delta < 0:
        reasons.append("完工提前")
    if start_delta:
        reasons.append("开工日期变化")
    if critical_change in {"新增关键", "退出关键", "新增工序", "已删除"}:
        reasons.append(critical_change)
    return "、".join(reasons) or "无明显变化"


def percent_offset(value: date | None, start: date | None, total_days: int) -> int:
    if not value or not start:
        return 0
    return max(0, min(100, int(((value - start).days / total_days) * 100)))


def percent_width(start: date | None, finish: date | None, total_days: int) -> int:
    if not start or not finish:
        return 0
    return max(1, min(100, int((((finish - start).days + 1) / total_days) * 100)))


def date_delta(current: date | None, baseline: date | None) -> int:
    if not current or not baseline:
        return 0
    return (current - baseline).days


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def format_date(value: date | None) -> str:
    return value.isoformat() if value else "-"


def month_labels(start: date | None, finish: date | None) -> list[str]:
    if not start or not finish:
        return []
    labels = []
    current = date(start.year, start.month, 1)
    end = date(finish.year, finish.month, 1)
    while current <= end and len(labels) < 18:
        labels.append(current.strftime("%Y-%m"))
        year = current.year + (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        current = date(year, month, 1)
    return labels


def format_path_task(task: dict[str, Any]) -> str:
    return f"{task.get('task_id') or ''} {task.get('task_name') or ''}".strip()


def milestone_key(row: dict[str, Any]) -> str:
    return str(row.get("milestone_id") or row.get("milestone_name") or "").strip()


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
