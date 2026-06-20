"""Dynamic responder Agent for event-side collaboration."""

from __future__ import annotations

from typing import Any

from agents.base_agent import AgentResult, BaseAgent
from communication.event_topics import DYNAMIC_EVENT_RECEIVED
from communication.message_schema import AgentMessage


class DynamicResponderAgent(BaseAgent):
    """Create sample dynamic events and notify related Agents."""

    def __init__(self, *, store) -> None:
        super().__init__(
            name="dynamic_responder_agent",
            display_name="动态响应Agent",
            role="接收动态事件、判断触发等级、发起协商",
            store=store,
        )

    def run(self) -> dict[str, Any]:
        """Execute dynamic event intake."""

        return self._run().to_dict()

    def handle_message(self, message: AgentMessage) -> AgentResult:
        """Record sample events when event handling is requested."""

        if message.event_type == DYNAMIC_EVENT_RECEIVED or message.mode == "direct":
            result = self._run()
            result.messages.insert(0, message.message_id)
            return result
        return super().handle_message(message)

    def _run(self) -> AgentResult:
        events = self.store.read_rows("event_log")
        if not events:
            return self.result(
                status="failed",
                summary=(
                    "未发现真实动态事件；生产运行必须由 AutoGen AgentChat "
                    "从资料或风险上下文提取"
                ),
                written_sheets=[],
                needs_human_confirmation=True,
                warnings=["禁止写入暴雨、钢筋供应延迟、设计变更等固定样例事件"],
            )
        messages = []
        for event in events:
            messages.append(
                self.log_message(
                    receiver="resource_allocator_agent,constraint_checker_agent,plan_arbiter_agent",
                    mode="broadcast",
                    event_type=DYNAMIC_EVENT_RECEIVED,
                    priority=str(event["priority"]),  # type: ignore[arg-type]
                    summary=(
                        f"{event['event_type']} 影响 {event['related_task']}，"
                        f"预计影响 {event['impact_days']} 天"
                    ),
                    related_sheet="event_log",
                    related_id=str(event["event_id"]),
                )
            )
        return self.result(
            status="success",
            summary=f"已读取 {len(events)} 条真实动态事件",
            written_sheets=["event_log"],
            messages=messages,
            needs_human_confirmation=True,
            warnings=["高优先级事件需进入总控仲裁或动态调整流程"],
        )
