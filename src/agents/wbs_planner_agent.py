"""WBS planner Agent for reading AgentChat-produced WBS rows."""

from __future__ import annotations

from typing import Any

from agents.base_agent import AgentResult, BaseAgent
from communication.message_schema import AgentMessage


class WBSPlannerAgent(BaseAgent):
    """Read and report validated WBS rows already written by AgentChat."""

    def __init__(self, *, store) -> None:
        super().__init__(
            name="wbs_planner_agent",
            display_name="工序规划Agent",
            role="读取 AgentChat 生成的真实 WBS、工序逻辑和流水规划",
            store=store,
        )

    def run(self) -> dict[str, Any]:
        """Read existing WBS rows."""

        return self._run().to_dict()

    def handle_message(self, message: AgentMessage) -> AgentResult:
        """Report WBS status when directly requested by the coordinator."""

        if message.mode == "direct":
            result = self._run()
            result.messages.insert(0, message.message_id)
            return result
        return super().handle_message(message)

    def _run(self) -> AgentResult:
        final_rows = self.store.read_rows("wbs_tasks_final")
        if not final_rows:
            return self.result(
                status="failed",
                summary="未发现真实 WBS；生产运行必须由 AutoGen AgentChat 基于资料生成",
                needs_human_confirmation=True,
                warnings=["禁止使用固定 WBS 模板或样例 WBS"],
            )
        return self.result(
            status="success",
            summary=f"已读取 {len(final_rows)} 条真实 WBS 工序",
            written_sheets=["wbs_tasks_final"],
            messages=[],
            needs_human_confirmation=False,
            warnings=[],
        )
