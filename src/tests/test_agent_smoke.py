from pathlib import Path

from agents.coordinator_agent import CoordinatorAgent
from agents.data_parser_agent import DataParserAgent
from blackboard.excel_store import ExcelBlackboardStore
from communication.message_logger import MessageLogger
from communication.message_schema import make_message
from communication.router import CommunicationRouter


def test_direct_message_between_two_agents(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    logger = MessageLogger(store, tmp_path / "communication_log.xlsx")
    router = CommunicationRouter(logger)
    router.register(CoordinatorAgent(store=store))
    router.register(DataParserAgent(store=store))

    response = router.send_direct(
        make_message(
            sender="coordinator_agent",
            receiver="data_parser_agent",
            mode="direct",
            event_type="parameter.check.requested",
            summary="检查参数清单。",
        )
    )

    assert response["agent"] == "data_parser_agent"
    assert logger.count_messages() >= 2
