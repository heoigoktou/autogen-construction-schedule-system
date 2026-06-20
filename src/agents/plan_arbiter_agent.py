"""Plan arbiter Agent for candidate adjustment scoring."""

from __future__ import annotations

from typing import Any

from agents.base_agent import AgentResult, BaseAgent
from communication.event_topics import ADJUSTMENT_PLAN_PROPOSED
from communication.message_schema import AgentMessage


class PlanArbiterAgent(BaseAgent):
    """Score candidate adjustment plans and recommend one plan."""

    def __init__(self, *, store) -> None:
        super().__init__(
            name="plan_arbiter_agent",
            display_name="方案仲裁Agent",
            role="对候选调整方案进行评分、排序和推荐",
            store=store,
        )

    def run(self) -> dict[str, Any]:
        """Execute candidate plan arbitration."""

        return self._run().to_dict()

    def handle_message(self, message: AgentMessage) -> AgentResult:
        """Run arbitration for direct/broadcast requests."""

        if message.mode in {"direct", "broadcast", "arbitration"}:
            result = self._run()
            result.messages.insert(0, message.message_id)
            return result
        return super().handle_message(message)

    def _run(self) -> AgentResult:
        candidate_rows = self.store.read_rows("adjustment_plan")
        if not candidate_rows:
            return self.result(
                status="failed",
                summary=(
                    "未发现真实候选调整方案；生产运行必须由 AutoGen AgentChat "
                    "基于事件和约束生成"
                ),
                written_sheets=[],
                needs_human_confirmation=True,
                warnings=["禁止使用固定候选方案或样例评分"],
            )
        selected = next((row for row in candidate_rows if row.get("selected_flag") is True), None)
        if selected is None:
            return self.result(
                status="failed",
                summary="候选调整方案缺少 selected_flag=True 的推荐方案",
                written_sheets=["adjustment_plan"],
                needs_human_confirmation=True,
                warnings=["需要由 AgentChat 或人工指定唯一推荐方案"],
            )
        message_id = self.log_message(
            receiver="coordinator_agent,constraint_checker_agent,dynamic_responder_agent",
            mode="arbitration",
            event_type=ADJUSTMENT_PLAN_PROPOSED,
            priority="high",
            summary=(
                f"已对 {len(candidate_rows)} 个候选方案评分，"
                f"推荐 {selected['plan_id']}：{selected['measure']}"
            ),
            related_sheet="adjustment_plan",
            related_id=str(selected["plan_id"]),
        )
        return self.result(
            status="success",
            summary=(
                f"已生成 {len(candidate_rows)} 个候选调整方案，" f"推荐方案为 {selected['plan_id']}"
            ),
            written_sheets=["adjustment_plan"],
            messages=[message_id],
            needs_human_confirmation=True,
            warnings=["推荐方案需由总控调度Agent最终确认后发布"],
        )
