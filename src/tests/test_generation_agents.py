from pathlib import Path

from agents.data_parser_agent import DataParserAgent
from agents.resource_allocator_agent import ResourceAllocatorAgent
from agents.wbs_planner_agent import WBSPlannerAgent
from blackboard.excel_store import ExcelBlackboardStore
from tests.helpers import minimal_parameter_checklist, minimal_resource_rows, minimal_wbs_rows


def test_generation_agents_write_expected_sheets(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "demo_blackboard.xlsx")
    store.initialize()
    store.append_rows("parameter_checklist", minimal_parameter_checklist())
    store.replace_rows("wbs_tasks_final", minimal_wbs_rows())
    store.replace_rows("resource_plan_final", minimal_resource_rows())

    parser_result = DataParserAgent(store=store).run()
    wbs_result = WBSPlannerAgent(store=store).run()
    resource_result = ResourceAllocatorAgent(store=store).run()

    assert parser_result["status"] == "success"
    assert wbs_result["status"] == "success"
    assert resource_result["status"] == "success"
    assert len(store.read_rows("project_parameters")) >= 2
    assert len(store.read_rows("wbs_tasks_final")) == 3

    resource_rows = store.read_rows("resource_plan_final")
    assert len(resource_rows) == 2
    assert any(row["conflict_flag"] is True for row in resource_rows)

    messages = store.read_rows("agent_message_log")
    assert any(row["event_type"] == "resource.conflict.detected" for row in messages)
