from pathlib import Path

from agents.constraint_checker_agent import ConstraintCheckerAgent
from agents.coordinator_agent import CoordinatorAgent
from agents.dynamic_responder_agent import DynamicResponderAgent
from blackboard.excel_store import ExcelBlackboardStore
from communication.event_topics import DYNAMIC_EVENT_RECEIVED
from communication.message_logger import MessageLogger
from communication.router import CommunicationRouter
from tests.helpers import minimal_event_rows, minimal_resource_rows, minimal_wbs_rows


def test_dynamic_event_broadcast_and_arbitration(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    store.replace_rows("wbs_tasks_final", minimal_wbs_rows())
    store.replace_rows("resource_plan_final", minimal_resource_rows())
    store.replace_rows("event_log", minimal_event_rows())
    logger = MessageLogger(store, tmp_path / "communication_log.xlsx")
    router = CommunicationRouter(logger)
    router.register(CoordinatorAgent(store=store))
    router.register(DynamicResponderAgent(store=store))
    router.register(ConstraintCheckerAgent(store=store))

    responses = router.broadcast(
        sender="dynamic_responder_agent",
        event_type=DYNAMIC_EVENT_RECEIVED,
        summary="连续7天暴雨影响基坑土方开挖。",
        priority="high",
    )
    arbitration = router.request_arbitration(
        sender="dynamic_responder_agent",
        summary="请总控确认暴雨事件处理优先级。",
    )

    assert responses
    assert arbitration["agent"] == "coordinator_agent"
    assert logger.count_messages() >= 3
