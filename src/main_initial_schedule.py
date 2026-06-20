"""Generate the supplementary initial schedule and CPM outputs."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from blackboard.excel_store import ExcelBlackboardStore
from config_loader import load_paths_config
from tools.schedule_tools import (
    build_initial_schedule,
    build_milestone_checks,
    export_rows_to_xlsx,
    write_network_diagram,
    write_summary_asset,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def setup_logging(runtime_log: Path) -> None:
    """Configure runtime logging for the initial schedule workflow."""

    runtime_log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[logging.FileHandler(runtime_log, encoding="utf-8"), logging.StreamHandler()],
    )


def main() -> None:
    paths = load_paths_config(PROJECT_ROOT)
    setup_logging(paths["runtime_log"])

    store = ExcelBlackboardStore(paths["blackboard"])
    store.initialize()

    wbs_rows = store.read_rows("wbs_tasks_final")
    resource_rows = store.read_rows("resource_plan_final")
    project_parameters = store.read_rows("project_parameters")
    if not wbs_rows or not resource_rows:
        raise RuntimeError(
            "未发现真实 wbs_tasks_final/resource_plan_final。请先运行 "
            "python src/main_real_case_workflow.py 通过 AutoGen AgentChat 生成成果。"
        )
    start_date = _required_project_start_date(project_parameters)

    schedule_rows, cpm_rows, edge_rows = build_initial_schedule(
        wbs_rows,
        resource_rows,
        start_date=start_date,
    )
    milestone_rows = build_milestone_checks(
        schedule_rows,
        project_parameters=project_parameters,
    )

    store.replace_rows("schedule_initial", schedule_rows)
    store.replace_rows("cpm_analysis", cpm_rows)
    store.replace_rows("network_edges", edge_rows)
    store.replace_rows("milestone_check", milestone_rows)

    schedule_dir = paths["schedule_dir"]
    export_rows_to_xlsx(wbs_rows, schedule_dir / "WBS工序分解表.xlsx", "wbs_tasks_final")
    export_rows_to_xlsx(resource_rows, schedule_dir / "资源需求表.xlsx", "resource_plan_final")
    export_rows_to_xlsx(store.read_rows("resource_load_daily"), schedule_dir / "资源负荷图.xlsx")
    export_rows_to_xlsx(
        store.read_rows("resource_resolution"), schedule_dir / "资源冲突消解记录.xlsx"
    )
    export_rows_to_xlsx(schedule_rows, schedule_dir / "初始施工进度计划.xlsx", "schedule_initial")
    export_rows_to_xlsx(cpm_rows, schedule_dir / "关键线路分析表.xlsx", "cpm_analysis")
    export_rows_to_xlsx(edge_rows, schedule_dir / "网络计划关系表.xlsx", "network_edges")
    export_rows_to_xlsx(schedule_rows, schedule_dir / "横道图数据源.xlsx", "gantt_data")
    export_rows_to_xlsx(
        milestone_rows, schedule_dir / "节点工期符合性初检表.xlsx", "milestone_check"
    )
    write_network_diagram(schedule_dir / "network_diagram.md", wbs_rows, edge_rows, cpm_rows)
    write_summary_asset(
        paths["report_assets_dir"] / "initial_schedule_summary.md",
        schedule_rows,
        cpm_rows,
        milestone_rows,
    )

    docs_dir = paths["docs_dir"]
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "schedule_handoff.md").write_text(
        "\n".join(
            [
                "# Schedule Handoff",
                "",
                "## Delivered Inputs",
                "",
                "- `wbs_tasks_final`: AgentChat-generated formal WBS from real documents.",
                "- `resource_plan_final`: AgentChat-generated labor, machinery, "
                "and material demands.",
                "- `resource_load_daily`: load-chart data grouped by period/resource.",
                "- `resource_resolution`: at least 3 conflict resolution records.",
                "",
                "## Interface Self Check",
                "",
                "- `task_id` and `wbs_code` are unique.",
                "- `duration_days` is positive for every task.",
                "- `predecessor_ids` references existing tasks.",
                "- CPM validation rejects missing predecessors and cycles before export.",
                "",
                "## Downstream Usage",
                "",
                "Downstream schedule tools read `task_id`, `duration_days`, "
                "`predecessor_ids`, `relation_type`, and "
                "`lag_days` from `wbs_tasks_final` for CPM and initial schedule generation.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "wbs_resource_notes.md").write_text(
        "\n".join(
            [
                "# WBS and Resource Notes",
                "",
                "- Formal WBS covers preparation, foundation pit, basement, superstructure, "
                "secondary structure, MEP, decoration, outdoor works, and acceptance.",
                "- Resource outputs cover labor, core machinery, and main materials.",
                "- Resource load data uses period-level aggregation for demo reporting.",
                "- Conflict resolution records include smoothing, staggering, "
                "and temporary additions.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "schedule_generation.md").write_text(
        "\n".join(
            [
                "# Initial Schedule Generation",
                "",
                "## Inputs",
                "",
                "- `wbs_tasks_final` from `WBSPlannerAgent`",
                "- `resource_plan_final` from `ResourceAllocatorAgent`",
                "",
                "## Outputs",
                "",
                "- `schedule_initial`",
                "- `cpm_analysis`",
                "- `network_edges`",
                "- `milestone_check`",
                f"- Excel exports under `{schedule_dir.as_posix()}`",
                "",
                "## Validation Notes",
                "",
                f"- Schedule task count: `{len(schedule_rows)}`",
                f"- Critical task count: `{sum(1 for row in cpm_rows if row['is_critical'])}`",
                f"- Network edge count: `{len(edge_rows)}`",
            ]
        ),
        encoding="utf-8",
    )

    print(f"initial schedule completed: {schedule_dir}")


def _required_project_start_date(project_parameters: list[dict[str, Any]]) -> date:
    for row in project_parameters:
        if str(row.get("parameter_id") or "") == "P-002":
            parsed = _parse_date(str(row.get("value") or ""))
            if parsed:
                return parsed
            raise RuntimeError(f"开工日期 P-002 非法：{row.get('value')}")
    raise RuntimeError("缺少真实开工日期 P-002，禁止使用默认开工日期。")


def _parse_date(value: str) -> date | None:
    match = re.search(r"([0-9]{4})[-年/.]([0-9]{1,2})[-月/.]([0-9]{1,2})", value)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


if __name__ == "__main__":
    main()
