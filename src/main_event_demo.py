"""Dynamic event communication smoke check.

Production event extraction now runs through AutoGen AgentChat. This script
routes existing `event_log` rows and does not fabricate weather/resource events.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agents.constraint_checker_agent import ConstraintCheckerAgent
from agents.coordinator_agent import CoordinatorAgent
from agents.dynamic_responder_agent import DynamicResponderAgent
from agents.plan_arbiter_agent import PlanArbiterAgent
from agents.resource_allocator_agent import ResourceAllocatorAgent
from blackboard.excel_store import ExcelBlackboardStore
from communication.event_topics import DYNAMIC_EVENT_RECEIVED
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
    """Register coordinator and dynamic-side Agents."""

    logger = MessageLogger(store, communication_log)
    router = CommunicationRouter(logger)
    for agent in [
        CoordinatorAgent(store=store),
        DynamicResponderAgent(store=store),
        ResourceAllocatorAgent(store=store),
        ConstraintCheckerAgent(store=store),
        PlanArbiterAgent(store=store),
    ]:
        router.register(agent)
    return router


def main() -> None:
    paths = load_paths()
    setup_logging(paths["runtime_log"])

    store = ExcelBlackboardStore(paths["blackboard"])
    store.initialize()
    router = build_router(store, paths["communication_log"])

    events = store.read_rows("event_log")
    if not events:
        raise RuntimeError(
            "未发现真实 event_log。请先运行 python src/main_real_case_workflow.py "
            "通过 AutoGen AgentChat 从资料中提取动态事件或风险。"
        )
    event_id = str(events[0]["event_id"])

    broadcast_responses = router.broadcast(
        sender="dynamic_responder_agent",
        event_type=DYNAMIC_EVENT_RECEIVED,
        summary=str(events[0].get("note") or events[0].get("event_type")),
        priority=str(events[0].get("priority") or "high"),  # type: ignore[arg-type]
        related_sheet="event_log",
        related_id=event_id,
    )

    direct_response = router.send_direct(
        make_message(
            sender="dynamic_responder_agent",
            receiver="resource_allocator_agent",
            mode="direct",
            event_type="resource.status.requested",
            summary="请基于已有 AgentChat 资源计划检查该事件关联资源状态。",
            priority="high",
            related_sheet="event_log",
            related_id=event_id,
            payload_extra={"required_response": "resource_adjustment_options"},
        )
    )
    constraint_response = router.send_direct(
        make_message(
            sender="dynamic_responder_agent",
            receiver="constraint_checker_agent",
            mode="direct",
            event_type=DYNAMIC_EVENT_RECEIVED,
            summary="已有真实动态事件，请复核进度、资源和现场约束。",
            priority="high",
            related_sheet="event_log",
            related_id=event_id,
            payload_extra={"required_response": "constraint_check"},
        )
    )
    arbiter_response = router.send_direct(
        make_message(
            sender="coordinator_agent",
            receiver="plan_arbiter_agent",
            mode="direct",
            event_type="adjustment.plan.proposed",
            summary="请检查 AgentChat 已生成的候选调整方案。",
            priority="high",
            related_sheet="event_log",
            related_id=event_id,
            payload_extra={"required_response": "candidate_adjustment_plans"},
        )
    )

    arbitration = router.request_arbitration(
        sender="dynamic_responder_agent",
        summary=(
            "真实动态事件需要总控确认进度和资源影响。"
        ),
        related_sheet="event_log",
        related_id=event_id,
    )

    router.logger.export_communication_log()
    transcript_path = paths["demo_transcripts_dir"] / "event_demo.md"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "\n".join(
            [
                "# Event Demo Transcript",
                "",
                f"- Event ID: `{event_id}`",
                f"- Registered agents: `{', '.join(router.registered_agents())}`",
                f"- Source event: `{events[0]}`",
                f"- Broadcast responses: `{broadcast_responses}`",
                f"- Direct response: `{direct_response}`",
                f"- Constraint response: `{constraint_response}`",
                f"- Arbiter response: `{arbiter_response}`",
                f"- Arbitration: `{arbitration}`",
                f"- Communication log: `{paths['communication_log']}`",
            ]
        ),
        encoding="utf-8",
    )
    print(f"event demo completed: {transcript_path}")


if __name__ == "__main__":
    main()
