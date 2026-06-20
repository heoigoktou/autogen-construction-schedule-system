"""Constraint checker Agent for validation-side collaboration."""

from __future__ import annotations

from typing import Any

from agents.base_agent import AgentResult, BaseAgent
from communication.event_topics import CONSTRAINT_VIOLATION_DETECTED
from communication.message_schema import AgentMessage
from tools.constraint_tools import FAIL, WARNING, check_constraints


class ConstraintCheckerAgent(BaseAgent):
    """Check schedule, process, resource, and site constraints."""

    def __init__(self, *, store) -> None:
        super().__init__(
            name="constraint_checker_agent",
            display_name="约束校核Agent",
            role="校核工期、工艺、资源、现场约束",
            store=store,
        )

    def run(self) -> dict[str, Any]:
        """Execute constraint checking."""

        return self._run().to_dict()

    def handle_message(self, message: AgentMessage) -> AgentResult:
        """Run constraint checks for broadcast or direct requests."""

        if message.mode in {"broadcast", "direct"}:
            result = self._run()
            result.messages.insert(0, message.message_id)
            return result
        return super().handle_message(message)

    def _run(self) -> AgentResult:
        wbs_rows = self.store.read_rows("wbs_tasks_final")
        resource_rows = self.store.read_rows("resource_plan_final")
        if not wbs_rows or not resource_rows:
            return self.result(
                status="failed",
                summary=(
                    "未发现真实 wbs_tasks_final/resource_plan_final；"
                    "约束校核必须基于 AutoGen AgentChat 生成并通过校验的成果"
                ),
                written_sheets=[],
                needs_human_confirmation=True,
                warnings=["禁止回退到 wbs_tasks/resource_plan 中间表进行生产校核"],
            )
        check_rows = check_constraints(wbs_rows, resource_rows)
        self.store.replace_rows("constraint_check", check_rows)
        failed = [row for row in check_rows if row.get("result") == FAIL]
        warning = [row for row in check_rows if row.get("result") == WARNING]
        messages = []
        if failed:
            messages.append(
                self.log_message(
                    receiver="coordinator_agent,resource_allocator_agent,plan_arbiter_agent",
                    mode="broadcast",
                    event_type=CONSTRAINT_VIOLATION_DETECTED,
                    priority="high",
                    summary=f"发现 {len(failed)} 项未通过约束，需总控确认处理",
                    related_sheet="constraint_check",
                )
            )
        return self.result(
            status="success",
            summary=f"已完成 {len(check_rows)} 项约束校核：通过/警告/未通过均已覆盖",
            written_sheets=["constraint_check"],
            messages=messages,
            needs_human_confirmation=bool(failed or warning),
            warnings=[str(row.get("suggestion")) for row in failed + warning],
        )
