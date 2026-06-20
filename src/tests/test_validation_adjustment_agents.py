from pathlib import Path

from agents.constraint_checker_agent import ConstraintCheckerAgent
from agents.data_parser_agent import DataParserAgent
from agents.dynamic_responder_agent import DynamicResponderAgent
from agents.plan_arbiter_agent import PlanArbiterAgent
from agents.resource_allocator_agent import ResourceAllocatorAgent
from agents.wbs_planner_agent import WBSPlannerAgent
from blackboard.excel_store import ExcelBlackboardStore
from tests.helpers import (
    minimal_adjustment_rows,
    minimal_event_rows,
    minimal_parameter_checklist,
    minimal_resource_rows,
    minimal_wbs_rows,
)
from tools.constraint_tools import FAIL, PASS, WARNING


def test_validation_adjustment_agents_write_expected_sheets(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "demo_blackboard.xlsx")
    store.initialize()
    store.append_rows("parameter_checklist", minimal_parameter_checklist())
    store.replace_rows("wbs_tasks_final", minimal_wbs_rows())
    store.replace_rows("resource_plan_final", minimal_resource_rows())
    store.replace_rows("event_log", minimal_event_rows())
    store.replace_rows("adjustment_plan", minimal_adjustment_rows())
    DataParserAgent(store=store).run()
    WBSPlannerAgent(store=store).run()
    ResourceAllocatorAgent(store=store).run()

    constraint_result = ConstraintCheckerAgent(store=store).run()
    dynamic_result = DynamicResponderAgent(store=store).run()
    arbiter_result = PlanArbiterAgent(store=store).run()

    assert constraint_result["status"] == "success"
    assert dynamic_result["status"] == "success"
    assert arbiter_result["status"] == "success"

    check_results = {row["result"] for row in store.read_rows("constraint_check")}
    assert PASS in check_results
    assert WARNING in check_results or FAIL in check_results
    assert len(store.read_rows("event_log")) == 1

    plans = store.read_rows("adjustment_plan")
    assert len(plans) == 1
    assert sum(1 for row in plans if row["selected_flag"] is True) == 1

    messages = store.read_rows("agent_message_log")
    assert any(row["event_type"] == "constraint.violation.detected" for row in messages)
    assert any(row["event_type"] == "dynamic.event.received" for row in messages)
    assert any(row["event_type"] == "adjustment.plan.proposed" for row in messages)
