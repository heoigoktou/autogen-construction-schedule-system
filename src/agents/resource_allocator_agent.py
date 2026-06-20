"""Resource allocator Agent for generation-side collaboration."""

from __future__ import annotations

from typing import Any

from agents.base_agent import AgentResult, BaseAgent
from communication.event_topics import RESOURCE_CONFLICT_DETECTED
from communication.message_schema import AgentMessage


class ResourceAllocatorAgent(BaseAgent):
    """Read AgentChat-produced resource rows and detect resource conflicts."""

    def __init__(self, *, store) -> None:
        super().__init__(
            name="resource_allocator_agent",
            display_name="资源配置Agent",
            role="读取 AgentChat 生成的真实资源需求并识别资源冲突",
            store=store,
        )

    def run(self) -> dict[str, Any]:
        """Execute resource planning."""

        return self._run().to_dict()

    def handle_message(self, message: AgentMessage) -> AgentResult:
        """Report resource planning for direct or event-driven requests."""

        if message.mode in {"direct", "broadcast"}:
            result = self._run()
            result.messages.insert(0, message.message_id)
            return result
        return super().handle_message(message)

    def _run(self) -> AgentResult:
        resource_final_rows = self.store.read_rows("resource_plan_final")
        if not resource_final_rows:
            return self.result(
                status="failed",
                summary="未发现真实资源计划；生产运行必须由 AutoGen AgentChat 基于资料和 WBS 生成",
                written_sheets=[],
                needs_human_confirmation=True,
                warnings=["禁止使用按任务名称套规则的资源模板"],
            )

        conflicts = [row for row in resource_final_rows if row.get("conflict_flag") is True]
        messages = []
        if conflicts:
            conflict_summary = "；".join(
                (
                    f"{row.get('task_id')} {row.get('resource_name')} "
                    f"需求 {row.get('demand')}>容量 {row.get('capacity')}"
                )
                for row in conflicts[:3]
            )
            messages.append(
                self.log_message(
                    receiver="constraint_checker_agent,plan_arbiter_agent,coordinator_agent",
                    mode="broadcast",
                    event_type=RESOURCE_CONFLICT_DETECTED,
                    priority="high",
                    summary=f"发现资源冲突：{conflict_summary}",
                    related_sheet="resource_plan_final",
                )
            )

        return self.result(
            status="success",
            summary=f"已读取 {len(resource_final_rows)} 条真实资源需求",
            written_sheets=["resource_plan_final"],
            messages=messages,
            needs_human_confirmation=bool(conflicts),
            warnings=[f"{row.get('task_id')} 资源超限" for row in conflicts],
        )
