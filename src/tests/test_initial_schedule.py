from datetime import date
from pathlib import Path

from blackboard.excel_store import ExcelBlackboardStore
from tests.helpers import minimal_parameter_checklist, minimal_resource_rows, minimal_wbs_rows
from tools.parameter_tools import build_project_parameter_rows
from tools.schedule_tools import build_initial_schedule, build_milestone_checks


def test_formal_wbs_resource_and_initial_schedule_are_computable(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "demo_blackboard.xlsx")
    store.initialize()
    store.replace_rows("wbs_tasks_final", minimal_wbs_rows())
    store.replace_rows("resource_plan_final", minimal_resource_rows())
    project_parameters = build_project_parameter_rows(minimal_parameter_checklist())

    wbs_rows = store.read_rows("wbs_tasks_final")
    task_ids = {row["task_id"] for row in wbs_rows}
    wbs_codes = {row["wbs_code"] for row in wbs_rows}
    predecessors = {
        predecessor.strip()
        for row in wbs_rows
        for predecessor in str(row.get("predecessor_ids") or "").split(",")
        if predecessor.strip()
    }

    assert len(wbs_rows) == 3
    assert len(task_ids) == len(wbs_rows)
    assert len(wbs_codes) == len(wbs_rows)
    assert predecessors.issubset(task_ids)
    assert all(float(row["duration_days"]) > 0 for row in wbs_rows)

    assert len(store.read_rows("resource_plan_final")) == 2

    schedule_rows, cpm_rows, edge_rows = build_initial_schedule(
        wbs_rows,
        store.read_rows("resource_plan_final"),
        start_date=date(2026, 3, 1),
    )
    milestone_rows = build_milestone_checks(
        schedule_rows,
        project_parameters=project_parameters,
    )

    assert len(schedule_rows) == len(wbs_rows)
    assert len(cpm_rows) == len(wbs_rows)
    assert len(edge_rows) == len(wbs_rows) - 1
    assert any(row["is_critical"] is True for row in cpm_rows)
    assert all(row["planned_start"] <= row["planned_finish"] for row in schedule_rows)
    assert milestone_rows
