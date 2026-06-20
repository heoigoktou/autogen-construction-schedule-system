"""Coordinator Agent for task routing and arbitration."""

from __future__ import annotations

from agents.base_agent import AgentResult, BaseAgent
from communication.event_topics import ARBITRATION_COMPLETED
from communication.message_schema import AgentMessage


class CoordinatorAgent(BaseAgent):
    """Top-level coordinator responsible for routing and arbitration summaries."""

    def __init__(self, *, store) -> None:
        super().__init__(
            name="coordinator_agent",
            display_name="总控调度Agent",
            role="任务拆解、Agent调度、通信协调、冲突汇总和最终发布",
            store=store,
        )

    def handle_message(self, message: AgentMessage) -> AgentResult:
        """Handle coordination and arbitration messages."""

        if message.mode == "arbitration" or message.event_type == ARBITRATION_COMPLETED:
            return self.result(
                status="success",
                summary="总控调度Agent已完成仲裁：优先保证硬约束，建议进入候选方案评分。",
                messages=[message.message_id],
                warnings=[],
            )

        if message.mode == "broadcast":
            return self.result(
                status="success",
                summary=f"总控调度Agent已记录广播事件：{message.event_type}",
                messages=[message.message_id],
            )

        return self.result(
            status="success",
            summary=f"总控调度Agent已接收请求：{message.payload.get('summary', '')}",
            messages=[message.message_id],
        )
