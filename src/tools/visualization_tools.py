"""Visualization helpers for generated construction schedules."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import networkx as nx
from openpyxl import load_workbook
from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont

from blackboard.excel_store import ExcelBlackboardStore
from tools.resource_tools import build_resource_load
from tools.schedule_tools import build_initial_schedule


DEFAULT_OUTPUT_DIRNAME = "visualizations"
CRITICAL_COLOR = "#D94841"
NORMAL_COLOR = "#4E79A7"
FLOAT_COLOR = "#59A14F"
CAPACITY_COLOR = "#F28E2B"
GRID_COLOR = "#D9DEE7"
TEXT_COLOR = "#263238"


@dataclass(frozen=True)
class VisualizationResult:
    """Summary of generated visualization artifacts."""

    output_dir: Path
    artifacts: dict[str, Path]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "artifacts": {name: str(path) for name, path in self.artifacts.items()},
            "warnings": self.warnings,
        }


def generate_schedule_visualizations(
    store: ExcelBlackboardStore,
    output_dir: str | Path,
    *,
    title: str = "AgentChat Schedule Visualization",
) -> VisualizationResult:
    """Generate Gantt, CPM, float, and resource load visuals from blackboard tables."""

    return generate_schedule_visualizations_from_rows(
        {
            "schedule_initial": store.read_rows("schedule_initial"),
            "cpm_analysis": store.read_rows("cpm_analysis"),
            "network_edges": store.read_rows("network_edges"),
            "resource_load_daily": store.read_rows("resource_load_daily"),
        },
        output_dir,
        title=title,
    )


def generate_schedule_visualizations_from_export_dir(
    schedule_dir: str | Path,
    output_dir: str | Path,
    *,
    title: str = "AgentChat Schedule Visualization",
) -> VisualizationResult:
    """Generate schedule visuals from standalone exported Excel files."""

    rows = read_exported_schedule_tables(schedule_dir)
    return generate_schedule_visualizations_from_rows(rows, output_dir, title=title)


def generate_schedule_visualizations_from_rows(
    rows: dict[str, list[dict[str, Any]]],
    output_dir: str | Path,
    *,
    title: str = "AgentChat Schedule Visualization",
) -> VisualizationResult:
    """Generate schedule visuals from already-loaded table rows."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    _configure_matplotlib()

    schedule_rows = rows.get("schedule_initial") or []
    cpm_rows = rows.get("cpm_analysis") or []
    edge_rows = rows.get("network_edges") or []
    resource_load_rows = rows.get("resource_load_daily") or []

    artifacts: dict[str, Path] = {}
    warnings: list[str] = []

    if schedule_rows:
        artifacts["gantt_chart"] = write_gantt_chart(
            output_path / "gantt_chart.png",
            schedule_rows,
            title=f"{title} - Gantt",
        )
    else:
        warnings.append("schedule_initial is empty; Gantt chart was skipped.")

    if schedule_rows and cpm_rows:
        artifacts["cpm_float_chart"] = write_cpm_float_chart(
            output_path / "cpm_float_chart.png",
            schedule_rows,
            cpm_rows,
            title=f"{title} - CPM Float",
        )
        artifacts["cpm_network"] = write_cpm_network_chart(
            output_path / "cpm_network.png",
            schedule_rows,
            cpm_rows,
            edge_rows,
            title=f"{title} - CPM Network",
        )
        artifacts["cpm_network_mermaid"] = write_cpm_mermaid(
            output_path / "cpm_network.md",
            schedule_rows,
            cpm_rows,
            edge_rows,
            title=f"{title} - CPM Network",
        )
    else:
        warnings.append("schedule_initial/cpm_analysis is empty; CPM visuals were skipped.")

    if resource_load_rows:
        artifacts["resource_load_heatmap"] = write_resource_load_heatmap(
            output_path / "resource_load_heatmap.png",
            resource_load_rows,
            title=f"{title} - Resource Load Rate",
        )
        artifacts["resource_load_bars"] = write_resource_load_bars(
            output_path / "resource_load_bars.png",
            resource_load_rows,
            title=f"{title} - Demand vs Capacity",
        )
    else:
        warnings.append("resource_load_daily is empty; resource load charts were skipped.")

    artifacts["report"] = write_visualization_report(
        output_path / "schedule_visualization_report.md",
        artifacts=artifacts,
        schedule_rows=schedule_rows,
        cpm_rows=cpm_rows,
        resource_load_rows=resource_load_rows,
        warnings=warnings,
        title=title,
    )
    artifacts["manifest"] = write_visualization_manifest(
        output_path / "visualization_manifest.json",
        result=VisualizationResult(output_dir=output_path, artifacts=artifacts, warnings=warnings),
        counts={
            "schedule_initial": len(schedule_rows),
            "cpm_analysis": len(cpm_rows),
            "network_edges": len(edge_rows),
            "resource_load_daily": len(resource_load_rows),
        },
    )
    return VisualizationResult(output_dir=output_path, artifacts=artifacts, warnings=warnings)


def read_exported_schedule_tables(schedule_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Read visualizable tables from the workflow's exported schedule directory."""

    directory = Path(schedule_dir)
    file_by_table = {
        "schedule_initial": "初始施工进度计划.xlsx",
        "cpm_analysis": "关键线路分析表.xlsx",
        "network_edges": "网络计划关系表.xlsx",
        "resource_load_daily": "资源负荷图.xlsx",
    }
    rows: dict[str, list[dict[str, Any]]] = {}
    for table_name, filename in file_by_table.items():
        path = directory / filename
        rows[table_name] = _read_xlsx_rows(path) if path.exists() else []
    if not rows["resource_load_daily"]:
        resource_rows = _read_xlsx_rows(directory / "资源需求表.xlsx")
        if resource_rows:
            rows["resource_load_daily"] = build_resource_load(resource_rows)
    return rows


def build_demo_visualization_rows() -> dict[str, list[dict[str, Any]]]:
    """Build realistic fake schedule rows for visual testing."""

    wbs_rows = [
        _demo_wbs("TASK-001", "1.1", "Preparation", "Site mobilization", 5, ""),
        _demo_wbs("TASK-002", "1.2", "Preparation", "Survey and setting out", 3, "TASK-001"),
        _demo_wbs("TASK-003", "2.1", "Earthwork", "Foundation pit excavation", 12, "TASK-002"),
        _demo_wbs("TASK-004", "2.2", "Earthwork", "Foundation pit support", 10, "TASK-002"),
        _demo_wbs("TASK-005", "3.1", "Basement", "Basement waterproofing", 6, "TASK-003,TASK-004"),
        _demo_wbs("TASK-006", "3.2", "Basement", "Basement structure", 14, "TASK-005"),
        _demo_wbs("TASK-007", "4.1", "Superstructure", "First to fourth floor structure", 18, "TASK-006"),
        _demo_wbs("TASK-008", "4.2", "Superstructure", "Secondary structure", 12, "TASK-006"),
        _demo_wbs("TASK-009", "5.1", "MEP", "MEP rough-in", 14, "TASK-007"),
        _demo_wbs("TASK-010", "5.2", "Finishes", "Interior finishes", 16, "TASK-008,TASK-009"),
        _demo_wbs("TASK-011", "6.1", "Outdoor", "Outdoor utilities and pavement", 10, "TASK-009"),
        _demo_wbs("TASK-012", "7.1", "Acceptance", "Commissioning and acceptance", 5, "TASK-010,TASK-011"),
    ]
    resource_rows = [
        _demo_resource("TASK-003", "labor", "Excavation crew", 36, 30, "2026-W10"),
        _demo_resource("TASK-004", "labor", "Support crew", 28, 32, "2026-W10"),
        _demo_resource("TASK-005", "material", "Waterproofing membrane", 850, 1000, "2026-W12"),
        _demo_resource("TASK-006", "labor", "Rebar crew", 42, 40, "2026-W13"),
        _demo_resource("TASK-006", "equipment", "Tower crane", 1, 1, "2026-W13"),
        _demo_resource("TASK-007", "labor", "Concrete crew", 48, 45, "2026-W15"),
        _demo_resource("TASK-008", "labor", "Masonry crew", 30, 36, "2026-W15"),
        _demo_resource("TASK-009", "labor", "MEP crew", 32, 35, "2026-W18"),
        _demo_resource("TASK-010", "labor", "Finishing crew", 52, 45, "2026-W20"),
        _demo_resource("TASK-011", "equipment", "Excavator", 2, 1, "2026-W20"),
        _demo_resource("TASK-012", "labor", "Commissioning team", 18, 24, "2026-W22"),
    ]
    schedule_rows, cpm_rows, edge_rows = build_initial_schedule(
        wbs_rows,
        resource_rows,
        start_date=date(2026, 3, 1),
    )
    schedule_by_id = {row["task_id"]: row for row in schedule_rows}
    for row in resource_rows:
        schedule = schedule_by_id.get(str(row["task_id"]))
        if not schedule:
            continue
        start = _parse_date(schedule["planned_start"]) or date(2026, 3, 1)
        week = start + timedelta(days=(7 - start.weekday()) % 7)
        row["period"] = f"{week.isocalendar().year}-W{week.isocalendar().week:02d}"
    return {
        "schedule_initial": schedule_rows,
        "cpm_analysis": cpm_rows,
        "network_edges": edge_rows,
        "resource_load_daily": build_resource_load(resource_rows),
    }


def write_gantt_chart(path: Path, rows: list[dict[str, Any]], *, title: str) -> Path:
    """Write a horizontal Gantt chart PNG."""

    tasks = [_schedule_task(row) for row in rows]
    tasks = [task for task in tasks if task["start"] and task["finish"]]
    if not tasks:
        raise ValueError("No schedulable rows with planned_start/planned_finish were found.")
    tasks.sort(key=lambda task: (task["start"], task["finish"], task["task_id"]))

    height = _figure_height(len(tasks), row_height=0.32, minimum=6.5)
    fig, ax = plt.subplots(figsize=(16, height), constrained_layout=True)
    y_positions = range(len(tasks))
    colors = [CRITICAL_COLOR if task["critical"] else NORMAL_COLOR for task in tasks]

    for y, task, color in zip(y_positions, tasks, colors, strict=True):
        start_num = mdates.date2num(task["start"])
        width = max(1, (task["finish"] - task["start"]).days + 1)
        ax.barh(y, width, left=start_num, height=0.62, color=color, edgecolor="white")
        if task["duration"]:
            ax.text(
                start_num + width + 0.5,
                y,
                f"{task['duration']}d",
                va="center",
                fontsize=7,
                color=TEXT_COLOR,
            )

    ax.set_yticks(list(y_positions))
    ax.set_yticklabels([_task_label(task) for task in tasks], fontsize=7)
    ax.invert_yaxis()
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=12))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.8)
    ax.set_title(title, fontsize=14, pad=12)
    ax.set_xlabel("Calendar")
    _add_critical_legend(ax)
    _save_figure(fig, path)
    return path


def write_cpm_float_chart(
    path: Path,
    schedule_rows: list[dict[str, Any]],
    cpm_rows: list[dict[str, Any]],
    *,
    title: str,
) -> Path:
    """Write a total/free float bar chart PNG."""

    schedule_by_id = {str(row.get("task_id") or ""): row for row in schedule_rows}
    items = []
    for row in cpm_rows:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        items.append(
            {
                "task_id": task_id,
                "task_name": str(schedule_by_id.get(task_id, {}).get("task_name") or ""),
                "total_float": _as_float(row.get("total_float")),
                "free_float": _as_float(row.get("free_float")),
                "critical": _to_bool(row.get("is_critical")),
            }
        )
    if not items:
        raise ValueError("No CPM rows were found.")
    items.sort(key=lambda item: (item["critical"], -item["total_float"], item["task_id"]))

    height = _figure_height(len(items), row_height=0.28, minimum=6.5)
    fig, ax = plt.subplots(figsize=(14, height), constrained_layout=True)
    y_positions = range(len(items))
    ax.barh(
        list(y_positions),
        [item["total_float"] for item in items],
        color=[CRITICAL_COLOR if item["critical"] else FLOAT_COLOR for item in items],
        height=0.62,
        label="Total float",
    )
    ax.barh(
        list(y_positions),
        [item["free_float"] for item in items],
        color="#9CD49C",
        height=0.28,
        label="Free float",
    )
    ax.set_yticks(list(y_positions))
    ax.set_yticklabels([_task_label(item) for item in items], fontsize=7)
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.8)
    ax.set_xlabel("Days")
    ax.set_title(title, fontsize=14, pad=12)
    ax.legend(loc="lower right")
    _save_figure(fig, path)
    return path


def write_cpm_network_chart(
    path: Path,
    schedule_rows: list[dict[str, Any]],
    cpm_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    *,
    title: str,
) -> Path:
    """Write a readable CPM precedence flowchart PNG."""

    graph, task_meta, critical_edges = _build_network(schedule_rows, cpm_rows, edge_rows)
    if not graph.nodes:
        raise ValueError("No CPM network nodes were found.")

    _write_cpm_flowchart_image(path, graph, task_meta, critical_edges, title=title)
    return path


def write_resource_load_heatmap(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    title: str,
) -> Path:
    """Write a resource load-rate heatmap PNG."""

    matrix, resources, periods = _resource_matrix(rows)
    if not resources or not periods:
        raise ValueError("No plottable resource load rows were found.")

    fig_width = min(max(10, len(periods) * 0.55 + 4), 22)
    fig_height = min(max(6, len(resources) * 0.42 + 2.5), 20)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height), constrained_layout=True)
    image = ax.imshow(matrix, aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=max(1.5, matrix.max()))
    ax.set_xticks(range(len(periods)))
    ax.set_xticklabels(periods, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(resources)))
    ax.set_yticklabels(resources, fontsize=8)
    ax.set_title(title, fontsize=14, pad=12)
    ax.set_xlabel("Period")
    ax.set_ylabel("Resource")
    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label("Load rate")
    if len(resources) * len(periods) <= 240:
        for y, row in enumerate(matrix):
            for x, value in enumerate(row):
                if value > 0:
                    ax.text(
                        x,
                        y,
                        f"{value:.2f}",
                        ha="center",
                        va="center",
                        fontsize=6,
                        color="white" if value >= 1 else TEXT_COLOR,
                    )
    _save_figure(fig, path)
    return path


def write_resource_load_bars(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    title: str,
) -> Path:
    """Write a top resource demand-vs-capacity bar chart PNG."""

    items = []
    for row in rows:
        demand = _as_float(row.get("demand"))
        capacity = _as_float(row.get("capacity"))
        if not row.get("resource_name") or not row.get("date_or_period"):
            continue
        items.append(
            {
                "label": _truncate(
                    f"{row.get('resource_name')} | {row.get('date_or_period')}",
                    42,
                ),
                "demand": demand,
                "capacity": capacity,
                "load_rate": demand / capacity if capacity else math.inf,
                "conflict": _to_bool(row.get("conflict_flag")) or (capacity and demand > capacity),
            }
        )
    if not items:
        raise ValueError("No plottable resource load rows were found.")
    items.sort(key=lambda item: (-item["conflict"], -item["load_rate"], item["label"]))
    items = items[:25]

    height = _figure_height(len(items), row_height=0.36, minimum=6)
    fig, ax = plt.subplots(figsize=(14, height), constrained_layout=True)
    y_positions = list(range(len(items)))
    ax.barh(
        y_positions,
        [item["capacity"] for item in items],
        color=CAPACITY_COLOR,
        alpha=0.55,
        height=0.7,
        label="Capacity",
    )
    ax.barh(
        y_positions,
        [item["demand"] for item in items],
        color=[CRITICAL_COLOR if item["conflict"] else NORMAL_COLOR for item in items],
        height=0.42,
        label="Demand",
    )
    ax.set_yticks(y_positions)
    ax.set_yticklabels([item["label"] for item in items], fontsize=8)
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.8)
    ax.set_xlabel("Quantity")
    ax.set_title(title, fontsize=14, pad=12)
    ax.legend(loc="lower right")
    _save_figure(fig, path)
    return path


def write_cpm_mermaid(
    path: Path,
    schedule_rows: list[dict[str, Any]],
    cpm_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    *,
    title: str,
) -> Path:
    """Write a Mermaid CPM diagram for Markdown-friendly previews."""

    cpm_by_id = {str(row.get("task_id") or ""): row for row in cpm_rows}
    node_ids = {
        str(row.get("task_id") or ""): str(row.get("task_id") or "").replace("-", "_")
        for row in schedule_rows
        if row.get("task_id")
    }
    lines = [f"# {title}", "", "```mermaid", "flowchart LR"]
    for row in schedule_rows:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        cpm = cpm_by_id.get(task_id, {})
        label = _mermaid_label(
            f"{task_id}<br/>{row.get('task_name') or ''}<br/>TF={cpm.get('total_float', '')}"
        )
        lines.append(f'  {node_ids[task_id]}["{label}"]')
    for edge in edge_rows:
        source = str(edge.get("from_task_id") or "")
        target = str(edge.get("to_task_id") or "")
        if source not in node_ids or target not in node_ids:
            continue
        connector = "==>" if _to_bool(edge.get("is_critical_edge")) else "-->"
        lines.append(f"  {node_ids[source]} {connector} {node_ids[target]}")
    critical_nodes = [
        node_ids[str(row.get("task_id") or "")]
        for row in cpm_rows
        if _to_bool(row.get("is_critical")) and str(row.get("task_id") or "") in node_ids
    ]
    if critical_nodes:
        lines.append(f"  class {' '.join(critical_nodes)} critical")
        lines.append("  classDef critical fill:#ffd6d3,stroke:#d94841,stroke-width:2px")
    lines.extend(["```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_visualization_report(
    path: Path,
    *,
    artifacts: dict[str, Path],
    schedule_rows: list[dict[str, Any]],
    cpm_rows: list[dict[str, Any]],
    resource_load_rows: list[dict[str, Any]],
    warnings: list[str],
    title: str,
) -> Path:
    """Write a compact Markdown index for generated visualizations."""

    critical_count = sum(1 for row in cpm_rows if _to_bool(row.get("is_critical")))
    conflict_count = sum(1 for row in resource_load_rows if _to_bool(row.get("conflict_flag")))
    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"- Schedule tasks: `{len(schedule_rows)}`",
        f"- Critical tasks: `{critical_count}`",
        f"- Resource load rows: `{len(resource_load_rows)}`",
        f"- Resource conflicts: `{conflict_count}`",
        "",
        "## Artifacts",
        "",
    ]
    for name, artifact_path in artifacts.items():
        if name in {"report", "manifest"}:
            continue
        lines.append(f"- `{name}`: `{artifact_path.name}`")
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_visualization_manifest(
    path: Path,
    *,
    result: VisualizationResult,
    counts: dict[str, int],
) -> Path:
    """Write machine-readable visualization metadata."""

    payload = result.to_dict()
    payload["counts"] = counts
    payload["created_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _schedule_task(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(row.get("task_id") or ""),
        "task_name": str(row.get("task_name") or ""),
        "phase": str(row.get("phase") or ""),
        "start": _parse_date(row.get("planned_start")),
        "finish": _parse_date(row.get("planned_finish")),
        "duration": int(_as_float(row.get("duration_days"))),
        "critical": _to_bool(row.get("is_critical")),
    }


def _demo_wbs(
    task_id: str,
    wbs_code: str,
    phase: str,
    task_name: str,
    duration_days: int,
    predecessor_ids: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "wbs_code": wbs_code,
        "phase": phase,
        "section": "",
        "floor_or_area": "",
        "task_name": task_name,
        "work_package": phase,
        "quantity": "",
        "unit": "",
        "duration_days": duration_days,
        "predecessor_ids": predecessor_ids,
        "relation_type": "FS",
        "lag_days": 0,
        "source": "demo data",
        "confidence": "1.00",
        "note": "Fake row for visualization smoke testing.",
        "owner_agent": "visualization_demo",
    }


def _demo_resource(
    task_id: str,
    resource_type: str,
    resource_name: str,
    demand: float,
    capacity: float,
    period: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "resource_type": resource_type,
        "resource_name": resource_name,
        "demand": demand,
        "unit": "unit",
        "capacity": capacity,
        "period": period,
        "conflict_flag": demand > capacity,
        "source": "demo data",
        "confidence": "1.00",
        "note": "Fake row for visualization smoke testing.",
        "owner_agent": "visualization_demo",
    }


def _build_network(
    schedule_rows: list[dict[str, Any]],
    cpm_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
) -> tuple[nx.DiGraph, dict[str, dict[str, Any]], set[tuple[str, str]]]:
    cpm_by_id = {str(row.get("task_id") or ""): row for row in cpm_rows}
    graph = nx.DiGraph()
    task_meta: dict[str, dict[str, Any]] = {}
    for row in schedule_rows:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        cpm = cpm_by_id.get(task_id, {})
        meta = {
            "task_name": str(row.get("task_name") or ""),
            "phase": str(row.get("phase") or ""),
            "duration": int(_as_float(row.get("duration_days"))),
            "total_float": int(_as_float(cpm.get("total_float") or row.get("total_float"))),
            "es": int(_as_float(cpm.get("es"))),
            "ef": int(_as_float(cpm.get("ef"))),
            "critical": _to_bool(cpm.get("is_critical") or row.get("is_critical")),
            "predecessor_ids": str(row.get("predecessor_ids") or ""),
        }
        graph.add_node(task_id)
        task_meta[task_id] = meta

    critical_edges: set[tuple[str, str]] = set()
    for edge in edge_rows:
        source = str(edge.get("from_task_id") or "")
        target = str(edge.get("to_task_id") or "")
        if source in task_meta and target in task_meta:
            graph.add_edge(source, target)
            if _to_bool(edge.get("is_critical_edge")) or (
                task_meta[source]["critical"] and task_meta[target]["critical"]
            ):
                critical_edges.add((source, target))
    if not graph.edges:
        for target, meta in task_meta.items():
            for source in _split_predecessor_ids(meta["predecessor_ids"]):
                if source in task_meta:
                    graph.add_edge(source, target)
                    if task_meta[source]["critical"] and task_meta[target]["critical"]:
                        critical_edges.add((source, target))
    return graph, task_meta, critical_edges


def _cpm_layout(graph: nx.DiGraph, task_meta: dict[str, dict[str, Any]]) -> dict[str, tuple[float, float]]:
    groups: dict[int, list[str]] = {}
    for node in graph.nodes:
        groups.setdefault(task_meta[node]["es"], []).append(node)
    pos: dict[str, tuple[float, float]] = {}
    for x_index, es in enumerate(sorted(groups)):
        nodes = sorted(groups[es], key=lambda node: (task_meta[node]["ef"], node))
        center = (len(nodes) - 1) / 2
        for index, node in enumerate(nodes):
            pos[node] = (x_index, center - index)
    return pos


def _write_cpm_flowchart_image(
    path: Path,
    graph: nx.DiGraph,
    task_meta: dict[str, dict[str, Any]],
    critical_edges: set[tuple[str, str]],
    *,
    title: str,
) -> None:
    levels = _cpm_flowchart_levels(graph, task_meta)
    phase_order = _phase_order(task_meta)
    phase_index = {phase: index for index, phase in enumerate(phase_order)}

    level_nodes: dict[int, list[str]] = {}
    for node, level in levels.items():
        level_nodes.setdefault(level, []).append(node)
    for nodes in level_nodes.values():
        nodes.sort(
            key=lambda node: (
                phase_index.get(str(task_meta[node].get("phase") or ""), 999),
                task_meta[node].get("es", 0),
                task_meta[node].get("ef", 0),
                _numeric_task_id(node),
                node,
            )
        )

    max_level = max(level_nodes) if level_nodes else 0
    max_nodes_per_level = max((len(nodes) for nodes in level_nodes.values()), default=1)
    levels_per_band = 14
    band_count = math.ceil((max_level + 1) / levels_per_band)

    box_w = 330
    box_h = 146
    h_gap = 92
    v_gap = 42
    left_margin = 190
    right_margin = 230
    top_margin = 230
    band_header_h = 78
    band_gap = 135
    band_inner_h = band_header_h + max_nodes_per_level * (box_h + v_gap) + 35
    width = left_margin + levels_per_band * box_w + (levels_per_band - 1) * h_gap + right_margin
    height = top_margin + band_count * band_inner_h + (band_count - 1) * band_gap + 120

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), "#f7f9fc")
    draw = ImageDraw.Draw(image)
    font_regular = _load_pil_font(22)
    font_small = _load_pil_font(18)
    font_tiny = _load_pil_font(16)
    font_marker = _load_pil_font(17, bold=True)
    font_bold = _load_pil_font(24, bold=True)
    font_title = _load_pil_font(38, bold=True)
    font_subtitle = _load_pil_font(20)

    critical_color = CRITICAL_COLOR
    normal_color = NORMAL_COLOR
    edge_color = "#A8B3C2"
    phase_colors = [
        "#4C78A8",
        "#F58518",
        "#54A24B",
        "#E45756",
        "#72B7B2",
        "#B279A2",
        "#FF9DA6",
        "#9D755D",
        "#BAB0AB",
        "#5F9ED1",
    ]

    draw.text((left_margin, 54), title, font=font_title, fill="#18212f")
    draw.text(
        (left_margin, 108),
        "从左到右按前置关系推进；红色为关键工序，蓝色为非关键工序；跨段关系用“续接/承接”标记表示。",
        font=font_subtitle,
        fill="#526070",
    )
    _draw_flowchart_legend(
        draw,
        left_margin,
        150,
        font_small,
        font_marker,
        critical_color=critical_color,
        normal_color=normal_color,
        edge_color=edge_color,
    )

    positions: dict[str, tuple[int, int, int, int]] = {}
    for band in range(band_count):
        start_level = band * levels_per_band
        end_level = min(max_level, start_level + levels_per_band - 1)
        band_top = top_margin + band * (band_inner_h + band_gap)
        draw.rounded_rectangle(
            [60, band_top - 16, width - 70, band_top + band_inner_h - 2],
            radius=26,
            fill="#ffffff",
            outline="#d7dee8",
            width=2,
        )
        draw.text(
            (left_margin, band_top + 18),
            f"流程段 {band + 1}: 第 {start_level + 1} - {end_level + 1} 层",
            font=font_bold,
            fill="#233044",
        )
        for local_col, level in enumerate(range(start_level, end_level + 1)):
            x = left_margin + local_col * (box_w + h_gap)
            draw.text((x + 8, band_top + 24), f"L{level + 1:02d}", font=font_tiny, fill="#8a96a8")
            for index, node in enumerate(level_nodes.get(level, [])):
                y = band_top + band_header_h + index * (box_h + v_gap)
                positions[node] = (x, y, x + box_w, y + box_h)

    marker_rows: list[tuple[str, str, str, int, float, str]] = []
    for source, target in sorted(graph.edges, key=lambda item: (levels.get(item[0], 0), item[0], item[1])):
        if source not in positions or target not in positions:
            continue
        sx0, sy0, sx1, sy1 = positions[source]
        tx0, ty0, tx1, ty1 = positions[target]
        source_mid_y = (sy0 + sy1) / 2
        target_mid_y = (ty0 + ty1) / 2
        source_band = levels[source] // levels_per_band
        target_band = levels[target] // levels_per_band
        is_critical_edge = (source, target) in critical_edges
        line_color = critical_color if is_critical_edge else edge_color
        line_width = 5 if is_critical_edge else 3
        if source_band == target_band and sx1 <= tx0:
            mid_x = (sx1 + tx0) / 2
            _draw_arrow(
                draw,
                [(sx1, source_mid_y), (mid_x, source_mid_y), (mid_x, target_mid_y), (tx0, target_mid_y)],
                line_color,
                line_width,
            )
        else:
            out_x = min(width - 210, sx1 + 70)
            _draw_arrow(draw, [(sx1, source_mid_y), (out_x, source_mid_y)], line_color, line_width)
            marker_rows.append(("out", source, target, min(width - 155, out_x + 10), source_mid_y - 17, line_color))
            in_x = max(130, tx0 - 72)
            _draw_arrow(draw, [(in_x, target_mid_y), (tx0, target_mid_y)], line_color, line_width)
            marker_rows.append(("in", source, target, max(68, in_x - 92), target_mid_y - 17, line_color))

    for marker_type, source, target, x, y, color in marker_rows:
        label = f"续至 {_compact_task_id(target)}" if marker_type == "out" else f"承 {_compact_task_id(source)}"
        _draw_pill(draw, x, y, x + 86, y + 34, label, font_marker, fill="#ffffff", outline=color)

    for node in sorted(graph.nodes, key=lambda item: (levels.get(item, 0), _numeric_task_id(item), item)):
        if node not in positions:
            continue
        x0, y0, x1, y1 = positions[node]
        meta = task_meta[node]
        is_critical = bool(meta.get("critical"))
        outline = critical_color if is_critical else normal_color
        fill = "#fff4f2" if is_critical else "#eef5ff"
        draw.rounded_rectangle(
            [x0, y0, x1, y1],
            radius=18,
            fill=fill,
            outline=outline,
            width=4 if is_critical else 3,
        )
        phase = str(meta.get("phase") or "未分组")
        phase_color = phase_colors[phase_index.get(phase, 0) % len(phase_colors)]
        draw.rounded_rectangle([x0 + 8, y0 + 8, x0 + 22, y1 - 8], radius=7, fill=phase_color, outline=phase_color)
        draw.text((x0 + 34, y0 + 16), f"{node}  |  {meta.get('duration', 0)}天", font=font_bold, fill="#1f2937")
        text_y = y0 + 52
        for line in _wrap_pil_text(draw, str(meta.get("task_name") or ""), font_regular, box_w - 56)[:2]:
            draw.text((x0 + 34, text_y), line, font=font_regular, fill="#192334")
            text_y += 28
        draw.text(
            (x0 + 34, y1 - 32),
            f"{phase} / 总时差 {meta.get('total_float', 0)}天",
            font=font_tiny,
            fill="#5f6c7b",
        )

    _draw_phase_legend(
        draw,
        phase_order,
        phase_index,
        phase_colors,
        left_margin,
        height - 70,
        width,
        font_small,
        font_tiny,
    )
    image.save(path, quality=95)


def _cpm_flowchart_levels(
    graph: nx.DiGraph,
    task_meta: dict[str, dict[str, Any]],
) -> dict[str, int]:
    if not nx.is_directed_acyclic_graph(graph):
        es_values = sorted({int(task_meta[node].get("es", 0)) for node in graph.nodes})
        es_index = {es: index for index, es in enumerate(es_values)}
        return {node: es_index[int(task_meta[node].get("es", 0))] for node in graph.nodes}

    levels: dict[str, int] = {}
    for node in nx.topological_sort(graph):
        predecessors = list(graph.predecessors(node))
        levels[node] = max((levels.get(pred, 0) + 1 for pred in predecessors), default=0)
    return levels


def _phase_order(task_meta: dict[str, dict[str, Any]]) -> list[str]:
    phases: list[str] = []
    for node in sorted(
        task_meta,
        key=lambda item: (
            int(task_meta[item].get("es", 0)),
            int(task_meta[item].get("ef", 0)),
            _numeric_task_id(item),
            item,
        ),
    ):
        phase = str(task_meta[node].get("phase") or "未分组")
        if phase not in phases:
            phases.append(phase)
    return phases or ["未分组"]


def _draw_flowchart_legend(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    marker_font: ImageFont.ImageFont,
    *,
    critical_color: str,
    normal_color: str,
    edge_color: str,
) -> None:
    entries = [
        ("关键工序 / 关键路径", critical_color),
        ("非关键工序", normal_color),
        ("依赖箭线", edge_color),
        ("跨段续接", "#7a8699"),
    ]
    for index, (label, color) in enumerate(entries):
        item_x = x + index * 250
        if "箭线" in label:
            draw.line([(item_x, y + 16), (item_x + 68, y + 16)], fill=color, width=5)
            draw.polygon([(item_x + 68, y + 16), (item_x + 54, y + 8), (item_x + 54, y + 24)], fill=color)
        elif "续接" in label:
            _draw_pill(draw, item_x, y, item_x + 78, y + 32, "续接", marker_font, fill="#f1f4f8", outline=color)
        else:
            draw.rounded_rectangle([item_x, y, item_x + 68, y + 32], radius=10, fill=color, outline=color)
        draw.text((item_x + 90, y + 2), label, font=font, fill="#344154")


def _draw_phase_legend(
    draw: ImageDraw.ImageDraw,
    phase_order: list[str],
    phase_index: dict[str, int],
    phase_colors: list[str],
    x: int,
    y: int,
    image_width: int,
    font: ImageFont.ImageFont,
    label_font: ImageFont.ImageFont,
) -> None:
    draw.text((x, y - 6), "阶段颜色", font=font, fill="#344154")
    cursor_x = x + 100
    cursor_y = y
    for phase in phase_order:
        color = phase_colors[phase_index[phase] % len(phase_colors)]
        draw.rounded_rectangle([cursor_x, cursor_y, cursor_x + 28, cursor_y + 20], radius=6, fill=color, outline=color)
        draw.text((cursor_x + 36, cursor_y - 2), phase, font=label_font, fill="#465365")
        cursor_x += 36 + _pil_text_width(draw, phase, label_font) + 34
        if cursor_x > image_width - 250:
            cursor_y += 32
            cursor_x = x + 100


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: str,
    width: int,
) -> None:
    if len(points) < 2:
        return
    draw.line(points, fill=color, width=width, joint="curve")
    x1, y1 = points[-2]
    x2, y2 = points[-1]
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 15
    p1 = (x2 + size * math.cos(angle + math.pi * 0.82), y2 + size * math.sin(angle + math.pi * 0.82))
    p2 = (x2 + size * math.cos(angle - math.pi * 0.82), y2 + size * math.sin(angle - math.pi * 0.82))
    draw.polygon([(x2, y2), p1, p2], fill=color)


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    text: str,
    font: ImageFont.ImageFont,
    *,
    fill: str,
    outline: str,
) -> None:
    draw.rounded_rectangle([x0, y0, x1, y1], radius=18, fill=fill, outline=outline, width=2)
    draw.text((x0 + (x1 - x0 - _pil_text_width(draw, text, font)) / 2, y0 + 7), text, font=font, fill=outline)


def _wrap_pil_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in str(text or ""):
        candidate = current + char
        if _pil_text_width(draw, candidate, font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _pil_text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0]


def _load_pil_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    common_paths = (
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        ),
        (
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"
        ),
    )
    for font_path in common_paths:
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue

    preferred = (
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Arial Unicode MS",
        "DejaVu Sans",
    )
    candidates = list(font_manager.fontManager.ttflist)
    if bold:
        bold_candidates = [
            font
            for font in candidates
            if font.name in preferred
            and (
                "bold" in str(getattr(font, "style", "")).lower()
                or str(getattr(font, "weight", "")).lower() in {"bold", "700"}
            )
        ]
        for font in bold_candidates:
            try:
                return ImageFont.truetype(font.fname, size)
            except OSError:
                continue
    for family in preferred:
        for font in candidates:
            if font.name == family:
                try:
                    return ImageFont.truetype(font.fname, size)
                except OSError:
                    continue
    return ImageFont.load_default()


def _numeric_task_id(task_id: str) -> int:
    match = re.search(r"(\d+)$", str(task_id))
    return int(match.group(1)) if match else 999999


def _compact_task_id(task_id: str) -> str:
    return str(task_id).replace("TASK-", "T")


def _split_predecessor_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,;，；\s]+", str(value or ""))
    return [str(item).strip() for item in raw if str(item).strip() and str(item).strip() != "-"]


def _resource_matrix(rows: list[dict[str, Any]]):
    periods = sorted({str(row.get("date_or_period") or "") for row in rows if row.get("date_or_period")})
    resources = sorted(
        {str(row.get("resource_name") or "") for row in rows if row.get("resource_name")}
    )
    max_load_by_resource = {
        resource: max(
            (
                _as_float(row.get("load_rate"))
                for row in rows
                if str(row.get("resource_name") or "") == resource
            ),
            default=0,
        )
        for resource in resources
    }
    resources = sorted(resources, key=lambda resource: (-max_load_by_resource[resource], resource))[:30]
    period_index = {period: index for index, period in enumerate(periods)}
    resource_index = {resource: index for index, resource in enumerate(resources)}

    import numpy as np

    matrix = np.zeros((len(resources), len(periods)))
    for row in rows:
        resource = str(row.get("resource_name") or "")
        period = str(row.get("date_or_period") or "")
        if resource not in resource_index or period not in period_index:
            continue
        matrix[resource_index[resource], period_index[period]] = max(
            matrix[resource_index[resource], period_index[period]],
            _as_float(row.get("load_rate")),
        )
    return matrix, [_truncate(resource, 36) for resource in resources], periods


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _read_xlsx_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    workbook = load_workbook(path, data_only=True, read_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(value or "").strip() for value in rows[0]]
    output: list[dict[str, Any]] = []
    for values in rows[1:]:
        if not values or all(value is None for value in values):
            continue
        output.append(
            {
                headers[index]: value
                for index, value in enumerate(values[: len(headers)])
                if headers[index]
            }
        )
    return output


def _as_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def _truncate(value: str, length: int) -> str:
    return value if len(value) <= length else value[: max(length - 3, 0)] + "..."


def _task_label(task: dict[str, Any]) -> str:
    return _truncate(f"{task['task_id']} {task['task_name']}", 48)


def _network_label(task_id: str, task_name: str) -> str:
    name = _truncate(task_name, 16)
    return f"{task_id}\n{name}" if name else task_id


def _mermaid_label(value: str) -> str:
    return value.replace('"', "'").replace("[", "(").replace("]", ")")


def _figure_height(count: int, *, row_height: float, minimum: float) -> float:
    return min(max(minimum, count * row_height + 2.5), 28)


def _add_critical_legend(ax: plt.Axes) -> None:
    handles = [
        plt.Line2D([0], [0], marker="s", color="w", label="Critical", markerfacecolor=CRITICAL_COLOR, markersize=9),
        plt.Line2D([0], [0], marker="s", color="w", label="Non-critical", markerfacecolor=NORMAL_COLOR, markersize=9),
    ]
    ax.legend(handles=handles, loc="lower right")


def _configure_matplotlib() -> None:
    plt.rcParams["axes.unicode_minus"] = False
    preferred_fonts = (
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Arial Unicode MS",
    )
    available = {font.name for font in font_manager.fontManager.ttflist}
    for font in preferred_fonts:
        if font in available:
            plt.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
            return
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]


def _save_figure(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
