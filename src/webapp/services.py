"""Reusable services for Web jobs."""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from blackboard.excel_store import ExcelBlackboardStore
from tools.constraint_tools import check_constraints
from tools.resource_tools import build_resource_load, build_resource_resolution
from tools.schedule_tools import (
    build_initial_schedule,
    build_milestone_checks,
    export_rows_to_xlsx,
    write_network_diagram,
    write_summary_asset,
)
from tools.visualization_tools import DEFAULT_OUTPUT_DIRNAME, generate_schedule_visualizations
from webapp.preprocess import PREPROCESS_DIRNAME, PREPROCESS_JSON, PREPROCESS_MARKDOWN
from webapp.visual_data import VISUAL_EXPORTS, export_visual_json_artifacts


EXPORT_FILES = {
    "blackboard": "blackboard.xlsx",
    "wbs": "wbs_tasks_final.xlsx",
    "resources": "resource_plan_final.xlsx",
    "schedule": "schedule_initial.xlsx",
    "cpm": "cpm_analysis.xlsx",
    "network": "network_edges.xlsx",
    "resource_load": "resource_load_daily.xlsx",
    "resource_resolution": "resource_resolution.xlsx",
    "milestones": "milestone_check.xlsx",
    "constraints": "constraint_check.xlsx",
    "network_diagram": "network_diagram.md",
    "summary": "initial_schedule_summary.md",
    "visual_report": f"{DEFAULT_OUTPUT_DIRNAME}/schedule_visualization_report.md",
    "gantt": f"{DEFAULT_OUTPUT_DIRNAME}/gantt_chart.png",
    "cpm_network": f"{DEFAULT_OUTPUT_DIRNAME}/cpm_network.png",
    "cpm_float": f"{DEFAULT_OUTPUT_DIRNAME}/cpm_float_chart.png",
    "resource_heatmap": f"{DEFAULT_OUTPUT_DIRNAME}/resource_load_heatmap.png",
    "resource_bars": f"{DEFAULT_OUTPUT_DIRNAME}/resource_load_bars.png",
    "preprocess_json": f"{PREPROCESS_DIRNAME}/{PREPROCESS_JSON}",
    "preprocess_markdown": f"{PREPROCESS_DIRNAME}/{PREPROCESS_MARKDOWN}",
    **VISUAL_EXPORTS,
}


def recalculate_blackboard_outputs(
    store: ExcelBlackboardStore,
    outputs_root: Path,
    *,
    title: str = "Web Schedule",
) -> dict[str, Any]:
    """Recalculate deterministic schedule, CPM, resource, constraints and visuals."""

    wbs_rows = store.read_rows("wbs_tasks_final")
    resource_rows = store.read_rows("resource_plan_final")
    project_parameters = store.read_rows("project_parameters")
    if not wbs_rows:
        raise ValueError("wbs_tasks_final is empty.")
    if not resource_rows:
        raise ValueError("resource_plan_final is empty.")

    start_date = required_project_start_date(project_parameters)
    schedule_rows, cpm_rows, edge_rows = build_initial_schedule(
        wbs_rows,
        resource_rows,
        start_date=start_date,
    )
    resource_load_rows = build_resource_load(resource_rows)
    resource_resolution_rows = build_resource_resolution(resource_load_rows)
    milestone_rows = build_milestone_checks(
        schedule_rows,
        project_parameters=project_parameters,
    )
    constraint_rows = check_constraints(wbs_rows, resource_rows)

    store.replace_sheets_rows(
        {
            "schedule_initial": schedule_rows,
            "cpm_analysis": cpm_rows,
            "network_edges": edge_rows,
            "resource_load_daily": resource_load_rows,
            "resource_resolution": resource_resolution_rows,
            "milestone_check": milestone_rows,
            "constraint_check": constraint_rows,
        }
    )

    export_job_artifacts(store, outputs_root)
    visualization_result = generate_schedule_visualizations(
        store,
        outputs_root / DEFAULT_OUTPUT_DIRNAME,
        title=title,
    )
    return {
        "schedule_initial": len(schedule_rows),
        "cpm_analysis": len(cpm_rows),
        "network_edges": len(edge_rows),
        "resource_load_daily": len(resource_load_rows),
        "resource_resolution": len(resource_resolution_rows),
        "milestone_check": len(milestone_rows),
        "constraint_check": len(constraint_rows),
        "visualization_result": visualization_result.to_dict(),
    }


def export_job_artifacts(store: ExcelBlackboardStore, outputs_root: Path) -> None:
    """Export commonly downloaded schedule tables and reports."""

    schedule_dir = outputs_root / "schedule"
    report_dir = outputs_root / "report_assets"
    schedule_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    table_exports = {
        "wbs_tasks_final": schedule_dir / EXPORT_FILES["wbs"],
        "resource_plan_final": schedule_dir / EXPORT_FILES["resources"],
        "schedule_initial": schedule_dir / EXPORT_FILES["schedule"],
        "cpm_analysis": schedule_dir / EXPORT_FILES["cpm"],
        "network_edges": schedule_dir / EXPORT_FILES["network"],
        "resource_load_daily": schedule_dir / EXPORT_FILES["resource_load"],
        "resource_resolution": schedule_dir / EXPORT_FILES["resource_resolution"],
        "milestone_check": schedule_dir / EXPORT_FILES["milestones"],
        "constraint_check": schedule_dir / EXPORT_FILES["constraints"],
    }
    for sheet_name, path in table_exports.items():
        export_rows_to_xlsx(store.read_rows(sheet_name), path, sheet_name)

    wbs_rows = store.read_rows("wbs_tasks_final")
    cpm_rows = store.read_rows("cpm_analysis")
    edge_rows = store.read_rows("network_edges")
    schedule_rows = store.read_rows("schedule_initial")
    milestone_rows = store.read_rows("milestone_check")
    if wbs_rows and edge_rows and cpm_rows:
        write_network_diagram(schedule_dir / EXPORT_FILES["network_diagram"], wbs_rows, edge_rows, cpm_rows)
    if schedule_rows and cpm_rows and milestone_rows:
        write_summary_asset(
            report_dir / EXPORT_FILES["summary"],
            schedule_rows,
            cpm_rows,
            milestone_rows,
        )
    export_visual_json_artifacts(store, outputs_root)


def artifact_path(outputs_root: Path, artifact: str, blackboard_path: Path) -> Path:
    """Resolve a downloadable artifact name to a whitelisted file path."""

    if artifact == "blackboard":
        return blackboard_path
    if artifact == "all":
        return outputs_root / "job_results.zip"
    relative = EXPORT_FILES.get(artifact)
    if not relative:
        raise KeyError(artifact)
    if artifact in {"summary"}:
        return outputs_root / "report_assets" / relative
    if artifact in {"preprocess_json", "preprocess_markdown"}:
        return outputs_root / relative
    if artifact in VISUAL_EXPORTS:
        return outputs_root / "schedule" / relative
    if artifact.startswith("visual_") or artifact in {
        "gantt",
        "cpm_network",
        "cpm_float",
        "resource_heatmap",
        "resource_bars",
    }:
        return outputs_root / relative
    return outputs_root / "schedule" / relative


def build_results_zip(outputs_root: Path, blackboard_path: Path) -> Path:
    """Create a zip package for the blackboard and output directory."""

    zip_path = outputs_root / "job_results.zip"
    outputs_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if blackboard_path.exists():
            archive.write(blackboard_path, "blackboard.xlsx")
        for path in sorted(outputs_root.rglob("*")):
            if path.is_file() and path != zip_path:
                archive.write(path, path.relative_to(outputs_root).as_posix())
    return zip_path


def list_existing_artifacts(outputs_root: Path, blackboard_path: Path) -> list[dict[str, str]]:
    """Return whitelisted artifacts that currently exist."""

    items = [{"key": "all", "name": "完整结果压缩包"}]
    names = {
        "blackboard": "黑板 Excel",
        "wbs": "WBS Excel",
        "resources": "资源 Excel",
        "schedule": "排程 Excel",
        "cpm": "CPM Excel",
        "network": "网络关系 Excel",
        "resource_load": "资源负载 Excel",
        "resource_resolution": "资源冲突处理 Excel",
        "milestones": "里程碑检查 Excel",
        "constraints": "约束检查 Excel",
        "network_diagram": "网络图 Markdown",
        "summary": "排程摘要 Markdown",
        "visual_report": "图表报告 Markdown",
        "gantt": "甘特图",
        "cpm_network": "CPM 网络图",
        "cpm_float": "CPM 时差图",
        "resource_heatmap": "资源热力图",
        "resource_bars": "资源柱状图",
        "preprocess_json": "标准化输入包 JSON",
        "preprocess_markdown": "标准化输入包 Markdown",
        "visual_data": "交互看板 JSON",
        "baseline_visual_data": "基准进度 JSON",
        "schedule_json": "排程 JSON",
        "cpm_json": "CPM JSON",
        "resource_load_json": "资源负荷 JSON",
        "milestones_json": "里程碑 JSON",
        "adjustments_json": "调整历史 JSON",
    }
    for key, name in names.items():
        try:
            path = artifact_path(outputs_root, key, blackboard_path)
        except KeyError:
            continue
        if path.exists():
            items.append({"key": key, "name": name})
    return items


def required_project_start_date(project_parameters: list[dict[str, Any]]) -> date:
    """Resolve P-002 start date from project parameters."""

    for row in project_parameters:
        if str(row.get("parameter_id") or "") != "P-002":
            continue
        parsed = parse_date(str(row.get("value") or ""))
        if parsed:
            return parsed
        raise ValueError(f"P-002 start date is invalid: {row.get('value')}")
    raise ValueError("Missing project start date P-002.")


def parse_date(value: str) -> date | None:
    import re

    match = re.search(r"([0-9]{4})[-年/.]([0-9]{1,2})[-月/.]([0-9]{1,2})", value)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
