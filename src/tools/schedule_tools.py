"""Initial schedule generation and export helpers."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from tools.cpm_tools import calculate_cpm
from tools.parameter_tools import now_iso

SCHEDULE_VERSION = "SCHEDULE-W2-V1"


def build_initial_schedule(
    wbs_rows: list[dict[str, Any]],
    resource_rows: list[dict[str, Any]],
    start_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build schedule_initial, cpm_analysis, and network_edges rows."""

    cpm_rows, edge_rows = calculate_cpm(wbs_rows)
    cpm_by_task = {row["task_id"]: row for row in cpm_rows}
    resource_summary = _resource_summary_by_task(resource_rows)
    generated_at = now_iso()

    schedule_rows = []
    for row in wbs_rows:
        task_id = str(row["task_id"])
        cpm = cpm_by_task[task_id]
        planned_start = start_date + timedelta(days=int(cpm["es"]))
        planned_finish = start_date + timedelta(days=max(int(cpm["ef"]) - 1, int(cpm["es"])))
        schedule_rows.append(
            {
                "task_id": task_id,
                "task_name": row["task_name"],
                "phase": row.get("phase") or "",
                "planned_start": planned_start.isoformat(),
                "planned_finish": planned_finish.isoformat(),
                "duration_days": row["duration_days"],
                "predecessor_ids": row.get("predecessor_ids") or "",
                "total_float": cpm["total_float"],
                "is_critical": cpm["is_critical"],
                "resource_summary": resource_summary.get(task_id, ""),
                "status": "generated",
                "version": SCHEDULE_VERSION,
                "generated_at": generated_at,
                "generated_by": "main_initial_schedule",
            }
        )
    return schedule_rows, cpm_rows, edge_rows


def build_milestone_checks(
    schedule_rows: list[dict[str, Any]],
    *,
    project_parameters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build basic milestone checks for the supplementary task."""

    generated_at = now_iso()
    project_finish = max(row["planned_finish"] for row in schedule_rows)
    target_finish = _target_finish_date(schedule_rows, project_parameters or [])
    checks: list[tuple[str, str, str, str, str]] = [
        ("MS-001", "项目计划完成", target_finish, project_finish, "P-003/P-001"),
    ]
    for milestone_id, name, phases in (
        ("MS-002", "地下结构完成", {"地下室结构", "地下结构", "基础施工"}),
        ("MS-003", "主体结构完成", {"主体结构", "主体施工"}),
        ("MS-004", "竣工验收完成", {"竣工验收", "验收"}),
    ):
        actual_date = _latest_finish(schedule_rows, phases)
        if actual_date:
            checks.append((milestone_id, name, "", actual_date, "phase-derived"))

    rows = []
    for milestone_id, name, target_date, actual_date, source in checks:
        result = _milestone_result(str(actual_date), target_date)
        missing_target = not target_date
        rows.append(
            {
                "milestone_id": milestone_id,
                "milestone_name": name,
                "target_date": target_date or "待确认",
                "actual_date": actual_date,
                "result": result,
                "severity": "medium" if missing_target or result == "未通过" else "low",
                "suggestion": (
                    f"目标日期来自 {source}，节点满足初始计划要求"
                    if result == "通过"
                    else "缺少真实目标日期，需由 AgentChat 补充 P-003 或 P-001"
                    if missing_target
                    else "需复核流水搭接、资源投入或合同目标"
                ),
                "created_by": "main_initial_schedule",
                "created_at": generated_at,
            }
        )
    return rows


def write_network_diagram(
    path: Path,
    wbs_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    cpm_rows: list[dict[str, Any]],
) -> Path:
    """Write a Mermaid network diagram for reporting."""

    task_name = {row["task_id"]: str(row["task_name"]) for row in wbs_rows}
    critical = {row["task_id"] for row in cpm_rows if row["is_critical"]}
    node_id = {row["task_id"]: str(row["task_id"]).replace("-", "_") for row in wbs_rows}
    lines = ["# Network Diagram", "", "```mermaid", "flowchart LR"]
    for row in wbs_rows:
        task_id = row["task_id"]
        label = _mermaid_label(f"{task_id}<br/>{task_name[task_id]}")
        lines.append(f'  {node_id[task_id]}["{label}"]')
    for edge in edge_rows:
        from_id = node_id[edge["from_task_id"]]
        to_id = node_id[edge["to_task_id"]]
        style = "==>" if edge["is_critical_edge"] else "-->"
        lines.append(f"  {from_id} {style} {to_id}")
    if critical:
        lines.append(
            f"  class {' '.join(node_id[task_id] for task_id in sorted(critical))} critical"
        )
        lines.append("  classDef critical fill:#ffe1e1,stroke:#c62828,stroke-width:2px")
    lines.extend(["```", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_summary_asset(
    path: Path,
    schedule_rows: list[dict[str, Any]],
    cpm_rows: list[dict[str, Any]],
    milestone_rows: list[dict[str, Any]],
) -> Path:
    """Write a compact A-side reporting evidence summary."""

    critical_count = sum(1 for row in cpm_rows if row["is_critical"])
    project_finish = max(row["planned_finish"] for row in schedule_rows)
    lines = [
        "# A Initial Schedule Evidence",
        "",
        f"- Schedule version: `{SCHEDULE_VERSION}`",
        f"- Task count: `{len(schedule_rows)}`",
        f"- Critical task count: `{critical_count}`",
        f"- Initial project finish: `{project_finish}`",
        "",
        "## Milestone Checks",
        "",
    ]
    for row in milestone_rows:
        lines.append(
            "- {milestone}: target `{target}`, actual `{actual}`, result `{result}`".format(
                milestone=row["milestone_name"],
                target=row["target_date"],
                actual=row["actual_date"],
                result=row["result"],
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_rows_to_xlsx(rows: list[dict[str, Any]], path: Path, sheet_name: str = "data") -> Path:
    """Export dictionaries to a simple xlsx file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name[:31]
    if rows:
        headers = list(rows[0].keys())
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(header) for header in headers])
    workbook.save(path)
    return path


def _mermaid_label(value: str) -> str:
    return value.replace('"', "'").replace("[", "(").replace("]", ")")


def _resource_summary_by_task(resource_rows: list[dict[str, Any]]) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for row in resource_rows:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        summary = f"{row.get('resource_name')}:{row.get('demand')}{row.get('unit') or ''}"
        grouped.setdefault(task_id, []).append(summary)
    return {task_id: "; ".join(values[:4]) for task_id, values in grouped.items()}


def _target_finish_date(
    schedule_rows: list[dict[str, Any]],
    project_parameters: list[dict[str, Any]],
) -> str:
    for row in project_parameters:
        if str(row.get("parameter_id") or "") == "P-003":
            parsed = _parse_date(str(row.get("value") or ""))
            if parsed:
                return parsed.isoformat()
    total_days = _total_duration_days(project_parameters)
    if total_days is None:
        return ""
    start_dates = [
        _parse_date(str(row.get("planned_start") or ""))
        for row in schedule_rows
        if row.get("planned_start")
    ]
    start_dates = [item for item in start_dates if item is not None]
    if not start_dates:
        return ""
    return (min(start_dates) + timedelta(days=max(total_days - 1, 0))).isoformat()


def _total_duration_days(project_parameters: list[dict[str, Any]]) -> int | None:
    for row in project_parameters:
        if str(row.get("parameter_id") or "") != "P-001":
            continue
        value = str(row.get("value") or "")
        match = __import__("re").search(r"([0-9]+(?:\.[0-9]+)?)\s*(天|日|个月|月)?", value)
        if not match:
            return None
        amount = float(match.group(1))
        unit = match.group(2) or "天"
        return int(round(amount * 30)) if unit in {"个月", "月"} else int(round(amount))
    return None


def _milestone_result(actual_date: str, target_date: str) -> str:
    if not target_date:
        return "警告"
    return "通过" if actual_date <= target_date else "未通过"


def _parse_date(value: str) -> date | None:
    match = __import__("re").search(r"([0-9]{4})[-年/.]([0-9]{1,2})[-月/.]([0-9]{1,2})", value)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _latest_finish(schedule_rows: list[dict[str, Any]], phases: set[str]) -> str | None:
    matched = [
        row["planned_finish"]
        for row in schedule_rows
        if any(phase in str(row.get("phase", "")) for phase in phases)
    ]
    if not matched:
        return None
    return max(matched)
