"""Communication smoke check for the legacy router.

Production generation now runs through `main_real_case_workflow.py` and the
AutoGen AgentChat runtime. This script only checks message routing and existing
blackboard status; it does not generate WBS or resource data.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agents.coordinator_agent import CoordinatorAgent
from agents.data_parser_agent import DataParserAgent
from agents.resource_allocator_agent import ResourceAllocatorAgent
from agents.wbs_planner_agent import WBSPlannerAgent
from blackboard.excel_store import ExcelBlackboardStore
from communication.event_topics import PARAMETER_CHECK_REQUESTED
from communication.message_logger import MessageLogger
from communication.message_schema import make_message
from communication.router import CommunicationRouter
from config_loader import load_paths_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_paths() -> dict[str, Path]:
    """Load path config and resolve paths from project root."""

    return load_paths_config(PROJECT_ROOT)


def setup_logging(runtime_log: Path) -> None:
    """Configure runtime logging."""

    runtime_log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[logging.FileHandler(runtime_log, encoding="utf-8"), logging.StreamHandler()],
    )


def build_router(store: ExcelBlackboardStore, communication_log: Path) -> CommunicationRouter:
    """Register coordinator and status-check Agents."""

    logger = MessageLogger(store, communication_log)
    router = CommunicationRouter(logger)
    for agent in [
        CoordinatorAgent(store=store),
        DataParserAgent(store=store),
        WBSPlannerAgent(store=store),
        ResourceAllocatorAgent(store=store),
    ]:
        router.register(agent)
    return router


def main() -> None:
    paths = load_paths()
    setup_logging(paths["runtime_log"])

    store = ExcelBlackboardStore(paths["blackboard"])
    store.initialize()
    imported = 0
    if not store.read_rows("parameter_checklist"):
        imported = store.copy_parameter_template(paths["parameter_template"])

    router = build_router(store, paths["communication_log"])
    router.log_only(
        sender="main_generate_demo",
        event_type="demo.started",
        summary=f"计划生成侧demo启动，导入参数清单{imported}条。",
        related_sheet="parameter_checklist",
    )

    broadcast_responses = router.broadcast(
        sender="coordinator_agent",
        event_type=PARAMETER_CHECK_REQUESTED,
        summary="启动参数检查清单模板核对，请资料解析Agent读取必需项和缺失项。",
        priority="medium",
        related_sheet="parameter_checklist",
    )

    direct_response = router.send_direct(
        make_message(
            sender="coordinator_agent",
            receiver="wbs_planner_agent",
            mode="direct",
            event_type="wbs.status.requested",
            summary="请检查黑板中是否已有 AgentChat 生成的真实 WBS。",
            priority="medium",
            related_sheet="wbs_tasks_final",
            payload_extra={"required_response": "wbs_status"},
        )
    )
    resource_response = router.send_direct(
        make_message(
            sender="coordinator_agent",
            receiver="resource_allocator_agent",
            mode="direct",
            event_type="resource.status.requested",
            summary="请检查黑板中是否已有 AgentChat 生成的真实资源计划。",
            priority="medium",
            related_sheet="resource_plan_final",
            payload_extra={"required_response": "resource_status"},
        )
    )

    router.logger.export_communication_log()
    transcript_path = paths["demo_transcripts_dir"] / "generate_demo.md"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "\n".join(
            [
                "# Generate Demo Transcript",
                "",
                f"- Blackboard: `{paths['blackboard']}`",
                f"- Imported checklist rows: `{imported}`",
                f"- Registered agents: `{', '.join(router.registered_agents())}`",
                f"- Broadcast responses: `{broadcast_responses}`",
                f"- Direct response: `{direct_response}`",
                f"- Resource response: `{resource_response}`",
                f"- Communication log: `{paths['communication_log']}`",
            ]
        ),
        encoding="utf-8",
    )
    print(f"generate demo completed: {transcript_path}")


if __name__ == "__main__":
    main()
