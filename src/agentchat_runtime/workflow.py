"""Production AutoGen AgentChat workflow for real case scheduling."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from agentchat_runtime.exceptions import AgentChatRuntimeError, AgentOutputValidationError
from agentchat_runtime.model_factory import build_openai_chat_completion_client
from agentchat_runtime.output_writer import (
    force_write_agentchat_output,
    parse_agentchat_json,
    validate_agentchat_payload,
    write_agentchat_output,
)
from blackboard.excel_store import ExcelBlackboardStore
from blackboard.sheet_schema import get_sheet_spec
from blackboard.validators import BlackboardValidationError
from communication.message_schema import new_message_id, now_iso
from tools.document_tools import SourceDocument, build_document_evidence_rows, concatenate_documents
from tools.parameter_extraction import extract_facts_from_evidence, extract_parameter_checklist_by_rules
from tools.cpm_tools import calculate_cpm
from tools.parameter_tools import build_project_parameter_rows

Runner = Callable[[str], Any]
MAX_VALIDATION_REVISIONS = 3
COORDINATOR_WRITE_BLACKBOARD_LIMIT = 3
RESPONSIBLE_AGENT_REPAIR_ATTEMPT = 2
COORDINATOR_TAKEOVER_ATTEMPT = 3
DEFAULT_TEAM_RUN_TIMEOUT_SECONDS = 1800
DEFAULT_AGENTCHAT_MAX_MESSAGES = 140
DEFAULT_AGENTCHAT_MAX_TURNS = 30
DEFAULT_REPEATED_VALIDATION_ERROR_LIMIT = 3
REFLECT_ON_TOOL_USE = False
FINAL_MARKER_MISSING_ERROR = "Final coordinator message did not include FINAL_SCHEDULE_READY."
LOGGER = logging.getLogger(__name__)

AGENTCHAT_DRAFT_TABLES = {
    "parameter_checklist",
    "project_parameters",
    "wbs_tasks_final",
    "resource_plan_final",
    "event_log",
    "adjustment_plan",
    "document_sections",
    "document_tables",
    "extracted_facts",
    "parameter_audit",
    "assumption_register",
    "quality_gates",
}

TARGET_DRAFT_TABLES = (
    "parameter_checklist",
    "project_parameters",
    "wbs_tasks_final",
    "resource_plan_final",
    "event_log",
    "adjustment_plan",
)

AGENT_WRITE_PERMISSIONS = {
    "data_parser_agent": {"parameter_checklist", "project_parameters"},
    "wbs_planner_agent": {"wbs_tasks_final"},
    "resource_allocator_agent": {"resource_plan_final"},
    "constraint_checker_agent": set(),
    "dynamic_responder_agent": {"event_log"},
    "plan_arbiter_agent": {"adjustment_plan"},
    "coordinator_agent": set(TARGET_DRAFT_TABLES),
}

TABLE_OWNER_AGENTS = {
    "parameter_checklist": "data_parser_agent",
    "project_parameters": "data_parser_agent",
    "wbs_tasks_final": "wbs_planner_agent",
    "resource_plan_final": "resource_allocator_agent",
    "event_log": "dynamic_responder_agent",
    "adjustment_plan": "plan_arbiter_agent",
}


@dataclass(frozen=True)
class AgentChatWorkflowResult:
    """Serializable summary of one AgentChat production run."""

    final_content: str
    written_counts: dict[str, int]
    message_count: int
    stop_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_content": self.final_content,
            "written_counts": self.written_counts,
            "message_count": self.message_count,
            "stop_reason": self.stop_reason,
        }


class AgentChatWorkflow:
    """Run real AutoGen AgentChat collaboration and persist validated outputs."""

    def __init__(
        self,
        *,
        store: ExcelBlackboardStore,
        model_settings: dict[str, Any],
        documents: list[SourceDocument],
        max_messages: int = DEFAULT_AGENTCHAT_MAX_MESSAGES,
        runner: Runner | None = None,
    ) -> None:
        self.store = store
        self.model_settings = model_settings
        self.documents = documents
        self.max_messages = max_messages
        self.runner = runner
        self.draft_tables: dict[str, list[dict[str, Any]]] = _new_draft_tables()
        self.tool_state: dict[str, Any] = {}

    def run(self) -> AgentChatWorkflowResult:
        """Run the workflow synchronously for script entrypoints."""

        reset_agentchat_output_tables(self.store)
        if self.runner is not None:
            task = build_agentchat_task(self.documents)
            result = self.runner(task)
            final_content = result if isinstance(result, str) else str(result)
            payload = parse_agentchat_json(final_content)
            counts = write_agentchat_output(
                self.store,
                payload,
                source_documents=self.documents,
            )
            return AgentChatWorkflowResult(
                final_content=final_content,
                written_counts=counts,
                message_count=0,
                stop_reason="test_runner",
            )
        return asyncio.run(self.run_async())

    async def run_async(self) -> AgentChatWorkflowResult:
        """Run SelectorGroupChat and persist the final JSON payload."""

        if not self.documents:
            problem = "No readable source documents were found; production inference is blocked."
            self._write_error_report(problem=problem, fix_action="Add readable source files.")
            raise AgentChatRuntimeError(problem)
        unreadable = [
            document for document in self.documents if document.warning and not document.text
        ]
        if unreadable:
            details = "; ".join(f"{doc.name}: {doc.warning}" for doc in unreadable)
            problem = f"Unreadable source documents blocked production flow: {details}"
            self._write_error_report(
                problem=problem,
                fix_action="Convert or replace unreadable files.",
            )
            raise AgentChatRuntimeError(problem)

        task = build_agentchat_task(self.documents)
        full_team_task = task
        last_error = ""
        total_messages = 0
        final_content = ""
        retry_coordinator_only = False
        responsible_repair_mode = False
        stop_reason: str | None = None
        for attempt in range(1, MAX_VALIDATION_REVISIONS + 1):
            LOGGER.info(
                "AgentChat attempt %s/%s started; waiting for model/team responses.",
                attempt,
                MAX_VALIDATION_REVISIONS,
            )
            team, model_clients = build_selector_team(
                store=self.store,
                model_settings=self.model_settings,
                documents=self.documents,
                max_messages=self.max_messages,
                draft_tables=self.draft_tables,
                tool_state=self.tool_state,
                revision_mode=retry_coordinator_only,
                responsible_repair_mode=responsible_repair_mode,
            )
            try:
                task_result = await asyncio.wait_for(
                    self._run_team_with_progress(team=team, task=task),
                    timeout=_team_run_timeout_seconds(self.model_settings),
                )
            except Exception as exc:
                problem = _runtime_failure_message(exc, attempt=attempt)
                self._persist_attempt_artifacts(
                    attempt=attempt,
                    task_result=None,
                    stop_reason=None,
                    validation_problem=problem,
                    failure=True,
                )
                self._write_error_report(problem=problem, fix_action=_runtime_fix_action(exc))
                raise AgentChatRuntimeError(problem) from exc
            finally:
                for model_client in model_clients:
                    close = getattr(model_client, "close", None)
                    if close is not None:
                        maybe_coro = close()
                        if asyncio.iscoroutine(maybe_coro):
                            await maybe_coro

            attempt_messages, final_content = self._record_task_result_messages(task_result)
            self._persist_attempt_artifacts(
                attempt=attempt,
                task_result=task_result,
                stop_reason=str(getattr(task_result, "stop_reason", "") or ""),
            )
            total_messages += attempt_messages
            stop_reason = str(getattr(task_result, "stop_reason", "") or "")
            LOGGER.info(
                "AgentChat attempt %s finished with %s messages; stop_reason=%s",
                attempt,
                attempt_messages,
                stop_reason or "<none>",
            )

            try:
                if not _is_final_payload_message(final_content) and _all_target_drafts_ready(
                    self.draft_tables
                ):
                    final_content = _build_final_content_from_drafts(self.draft_tables)
                    LOGGER.info(
                        "AgentChat final payload assembled locally from complete draft tables."
                    )
                if not _is_final_payload_message(final_content):
                    raise AgentOutputValidationError(FINAL_MARKER_MISSING_ERROR)
                payload = parse_agentchat_json(final_content)
                validate_agentchat_payload(payload, source_documents=self.documents)
                counts = write_agentchat_output(
                    self.store,
                    payload,
                    source_documents=self.documents,
                )
                LOGGER.info("AgentChat output validated and written: %s", counts)
                return AgentChatWorkflowResult(
                    final_content=final_content,
                    written_counts=counts,
                    message_count=total_messages,
                    stop_reason=stop_reason,
                )
            except AgentOutputValidationError as exc:
                last_error = str(exc)
                self._log_validation_retry(attempt=attempt, problem=last_error)
                if (
                    attempt == MAX_VALIDATION_REVISIONS
                    or self.tool_state.get("coordinator_write_limit_reached")
                ):
                    self._persist_attempt_artifacts(
                        attempt=attempt,
                        task_result=task_result,
                        stop_reason=stop_reason,
                        validation_problem=last_error,
                        final_fallback=True,
                    )
                    best_effort_payload = _build_best_effort_payload(
                        final_content=final_content,
                        draft_tables=self.draft_tables,
                    )
                    forced_final_content = _build_forced_final_content(best_effort_payload)
                    counts = force_write_agentchat_output(
                        self.store,
                        best_effort_payload,
                        source_documents=self.documents,
                        validation_error=last_error,
                    )
                    LOGGER.info("AgentChat output force-written after validation limit: %s", counts)
                    return AgentChatWorkflowResult(
                        final_content=forced_final_content,
                        written_counts=counts,
                        message_count=total_messages,
                        stop_reason=stop_reason,
                    )
                if _is_final_marker_missing_error(last_error):
                    retry_coordinator_only = True
                    responsible_repair_mode = False
                    task = build_missing_final_marker_task(
                        documents=self.documents,
                        validation_error=last_error,
                        attempt=attempt + 1,
                        draft_tables=self.draft_tables,
                        previous_output=final_content,
                        stop_reason=stop_reason,
                    )
                elif attempt == 1:
                    retry_coordinator_only = False
                    responsible_repair_mode = True
                    task = build_responsible_agent_repair_task(
                        documents=self.documents,
                        validation_error=last_error,
                        attempt=attempt + 1,
                        draft_tables=self.draft_tables,
                        previous_final=final_content,
                    )
                elif attempt == RESPONSIBLE_AGENT_REPAIR_ATTEMPT:
                    retry_coordinator_only = True
                    responsible_repair_mode = False
                    task = build_coordinator_takeover_task(
                        documents=self.documents,
                        validation_error=last_error,
                        attempt=attempt + 1,
                        draft_tables=self.draft_tables,
                        previous_final=final_content,
                    )
                else:
                    retry_coordinator_only = False
                    responsible_repair_mode = False
                    task = build_full_team_retry_task(
                        documents=self.documents,
                        validation_error=last_error,
                        attempt=attempt + 1,
                        original_task=full_team_task,
                    )

        problem = (
            "AgentChat output failed validation after "
            f"{MAX_VALIDATION_REVISIONS} attempts: {last_error}"
        )
        self._write_error_report(
            problem=problem,
            fix_action=(
                "Inspect agent_message_log/debug_records and revise the source context "
                "or model prompt."
            ),
        )
        forced_payload = _build_best_effort_payload(
            final_content=final_content,
            draft_tables=self.draft_tables,
        )
        forced_final_content = _build_forced_final_content(forced_payload)
        counts = force_write_agentchat_output(
            self.store,
            forced_payload,
            source_documents=self.documents,
            validation_error=problem,
        )
        LOGGER.info("AgentChat fallback output force-written after retries exhausted: %s", counts)
        return AgentChatWorkflowResult(
            final_content=forced_final_content,
            written_counts=counts,
            message_count=total_messages,
            stop_reason=stop_reason,
        )

    async def _run_team_with_progress(self, *, team: Any, task: str) -> Any:
        task_result: Any = None
        validation_error_counts: dict[str, int] = {}
        async for event in team.run_stream(task=task):
            if hasattr(event, "messages") and hasattr(event, "stop_reason"):
                task_result = event
                continue
            source = str(getattr(event, "source", event.__class__.__name__))
            raw_content = _message_content(event)
            final_payload = _extract_final_payload_from_text(raw_content)
            repeated_error = _track_repeated_validation_error(
                raw_content,
                validation_error_counts,
                _repeated_validation_error_limit(self.model_settings),
            )
            content = raw_content.replace("\r", " ").replace("\n", " ").strip()
            if len(content) > 160:
                content = content[:160] + "..."
            LOGGER.info("AgentChat event from %s: %s", source, content or event.__class__.__name__)
            if final_payload:
                LOGGER.info("AgentChat final payload captured from tool result.")
                return SimpleNamespace(
                    messages=[
                        SimpleNamespace(source="coordinator_agent", content=final_payload)
                    ],
                    stop_reason="final_payload_tool_result",
                )
            if repeated_error:
                raise AgentOutputValidationError(
                    "Repeated AgentChat validation blocker; stopping model repair loop: "
                    f"{repeated_error}"
                )
        if task_result is None:
            raise AgentChatRuntimeError("AutoGen AgentChat ended without a task result.")
        return task_result

    def _clear_unvalidated_outputs(self) -> None:
        reset_agentchat_output_tables(self.store)

    def _record_task_result_messages(self, task_result: Any) -> tuple[int, str]:
        message_count = 0
        final_content = ""
        for message in task_result.messages:
            message_count += 1
            source = str(getattr(message, "source", "agentchat_team"))
            content = _message_content(message)
            if source == "coordinator_agent":
                extracted_final = _extract_final_payload_from_message(message)
                if extracted_final:
                    final_content = extracted_final
            self._log_agentchat_message(source=source, content=content)
        return message_count, final_content

    def _log_validation_retry(self, *, attempt: int, problem: str) -> None:
        summary = f"AgentChat validation attempt {attempt} failed: {problem}"
        if len(summary) > 500:
            summary = summary[:500] + "..."
        LOGGER.warning(summary)

    def _write_error_report(self, *, problem: str, fix_action: str) -> None:
        LOGGER.error("%s Fix action: %s", problem, fix_action)

    def _log_agentchat_message(self, *, source: str, content: str) -> None:
        summary = content.replace("\r", " ").replace("\n", " ").strip()
        if len(summary) > 500:
            summary = summary[:500] + "..."
        LOGGER.info("AgentChat transcript %s: %s", source, summary or "<empty>")

    def _persist_attempt_artifacts(
        self,
        *,
        attempt: int,
        task_result: Any | None,
        stop_reason: str | None,
        validation_problem: str | None = None,
        failure: bool = False,
        final_fallback: bool = False,
    ) -> None:
        self._persist_draft_snapshot(
            attempt=attempt,
            validation_problem=validation_problem,
            failure=failure,
            final_fallback=final_fallback,
        )
        self._persist_communication_log(
            attempt=attempt,
            task_result=task_result,
            stop_reason=stop_reason,
            validation_problem=validation_problem,
            failure=failure,
            final_fallback=final_fallback,
        )

    def _persist_draft_snapshot(
        self,
        *,
        attempt: int,
        validation_problem: str | None,
        failure: bool,
        final_fallback: bool,
    ) -> None:
        sheet_rows: dict[str, list[dict[str, Any]]] = {
            sheet_name: [
                _snapshot_row(sheet_name, row, attempt=attempt, index=index)
                for index, row in enumerate(self.draft_tables.get(sheet_name) or [], start=1)
            ]
            for sheet_name in TARGET_DRAFT_TABLES
        }
        debug_rows: list[dict[str, Any]] = []
        if failure or final_fallback or validation_problem:
            debug_rows.append(
                {
                    "debug_id": f"DBG-AGENTCHAT-{attempt:02d}-001",
                    "module": "agentchat_runtime",
                    "scenario": "attempt_snapshot",
                    "problem": validation_problem
                    or (
                        "AgentChat runtime failed before finalization."
                        if failure
                        else "AgentChat reached validation limit; best-effort snapshot persisted."
                    ),
                    "severity": "high" if failure else "medium",
                    "root_cause": "validation_or_runtime_retry",
                    "fix_action": "Inspect agent_message_log and the latest blackboard snapshot.",
                    "retest_result": "pending",
                    "owner": "coordinator_agent",
                    "status": "logged",
                    "evidence_path": "",
                }
            )
        sheet_rows["debug_records"] = debug_rows
        try:
            self.store.replace_sheets_rows(sheet_rows)
        except Exception as exc:
            LOGGER.warning("AgentChat draft snapshot could not be fully persisted: %s", exc)

    def _persist_communication_log(
        self,
        *,
        attempt: int,
        task_result: Any | None,
        stop_reason: str | None,
        validation_problem: str | None,
        failure: bool,
        final_fallback: bool,
    ) -> None:
        rows: list[dict[str, Any]] = []
        if task_result is not None:
            for message in getattr(task_result, "messages", []):
                source = str(getattr(message, "source", "agentchat_team"))
                content = _message_content(message)
                summary = content.replace("\r", " ").replace("\n", " ").strip()
                if len(summary) > 500:
                    summary = summary[:500] + "..."
                rows.append(
                    {
                        "message_id": new_message_id(),
                        "sender": source,
                        "receiver": "SYSTEM" if source == "coordinator_agent" else "coordinator_agent",
                        "mode": "direct" if source == "coordinator_agent" else "handoff",
                        "event_type": "agentchat.transcript",
                        "priority": "medium",
                        "payload_summary": summary or "<empty>",
                        "status": "completed",
                        "timestamp": now_iso(),
                        "related_sheet": "agentchat_runtime",
                        "related_id": str(getattr(message, "message_id", "") or ""),
                    }
                )
        if failure or final_fallback or validation_problem:
            rows.append(
                {
                    "message_id": new_message_id(),
                    "sender": "agentchat_runtime",
                    "receiver": "SYSTEM",
                    "mode": "log_only",
                    "event_type": "agentchat.runtime.snapshot",
                    "priority": "high" if failure else "medium",
                    "payload_summary": validation_problem
                    or (
                        f"Best-effort snapshot persisted after attempt {attempt}; stop_reason={stop_reason or '<none>'}."
                        if final_fallback
                        else f"Draft snapshot persisted after attempt {attempt}."
                    ),
                    "status": "completed",
                    "timestamp": now_iso(),
                    "related_sheet": "debug_records",
                    "related_id": f"attempt-{attempt}",
                }
            )
        if rows:
            try:
                self.store.append_rows("agent_message_log", rows)
            except Exception as exc:
                LOGGER.warning("AgentChat communication log could not be fully persisted: %s", exc)


def run_real_case_agentchat_workflow(
    *,
    store: ExcelBlackboardStore,
    model_settings: dict[str, Any],
    documents: list[SourceDocument],
) -> dict[str, Any]:
    """Run the production workflow and return a serializable summary."""

    return AgentChatWorkflow(
        store=store,
        model_settings=model_settings,
        documents=documents,
    ).run().to_dict()


def build_selector_team(
    *,
    store: ExcelBlackboardStore,
    model_settings: dict[str, Any],
    documents: list[SourceDocument],
    max_messages: int,
    draft_tables: dict[str, list[dict[str, Any]]] | None = None,
    tool_state: dict[str, Any] | None = None,
    revision_mode: bool = False,
    responsible_repair_mode: bool = False,
):
    """Create a SelectorGroupChat team with production AgentChat agents."""

    try:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
        from autogen_agentchat.teams import SelectorGroupChat
    except ModuleNotFoundError as exc:
        raise AgentChatRuntimeError(
            "缺少 AutoGen AgentChat 依赖，请运行：python -m pip install -e \".[dev]\""
        ) from exc

    selector_model_client = build_openai_chat_completion_client(
        model_settings,
        thinking="disabled",
    )
    extraction_model_client = build_openai_chat_completion_client(
        model_settings,
        vision=True,
        thinking="disabled",
    )
    fast_model_client = build_openai_chat_completion_client(
        model_settings,
        thinking="disabled",
    )
    planning_model_client = build_openai_chat_completion_client(
        model_settings,
        thinking="enabled",
    )
    if draft_tables is None:
        draft_tables = _new_draft_tables()
    if tool_state is None:
        tool_state = {}
    tools_by_agent = {
        agent_name: build_agent_tools(
            store=store,
            documents=documents,
            draft_tables=draft_tables,
            writer_agent=agent_name,
            tool_state=tool_state,
        )
        for agent_name in AGENT_WRITE_PERMISSIONS
    }
    if revision_mode:
        stateful_candidate_func = _revision_candidate_func
    elif responsible_repair_mode:
        stateful_candidate_func = _build_responsible_repair_candidate_func(draft_tables)
    else:
        stateful_candidate_func = _build_candidate_func(draft_tables)
    agents = [
        AssistantAgent(
            "data_parser_agent",
            extraction_model_client,
            tools=tools_by_agent["data_parser_agent"],
            description="从真实资料抽取项目参数并维护 parameter_checklist/project_parameters。",
            system_message=_agent_system_message("data_parser_agent"),
            tool_call_summary_formatter=_tool_call_summary_formatter,
            reflect_on_tool_use=REFLECT_ON_TOOL_USE,
            max_tool_iterations=4,
        ),
        AssistantAgent(
            "wbs_planner_agent",
            planning_model_client,
            tools=tools_by_agent["wbs_planner_agent"],
            description="根据真实资料和参数生成非模板 WBS 工序。",
            system_message=_agent_system_message("wbs_planner_agent"),
            tool_call_summary_formatter=_tool_call_summary_formatter,
            reflect_on_tool_use=REFLECT_ON_TOOL_USE,
            max_tool_iterations=4,
        ),
        AssistantAgent(
            "resource_allocator_agent",
            fast_model_client,
            tools=tools_by_agent["resource_allocator_agent"],
            description="根据 WBS 和资料推断资源需求、容量和冲突。",
            system_message=_agent_system_message("resource_allocator_agent"),
            tool_call_summary_formatter=_tool_call_summary_formatter,
            reflect_on_tool_use=REFLECT_ON_TOOL_USE,
            max_tool_iterations=3,
        ),
        AssistantAgent(
            "constraint_checker_agent",
            planning_model_client,
            tools=tools_by_agent["constraint_checker_agent"],
            description="校验 schema、前置关系、CPM、资源字段和成果一致性。",
            system_message=_agent_system_message("constraint_checker_agent"),
            tool_call_summary_formatter=_tool_call_summary_formatter,
            reflect_on_tool_use=REFLECT_ON_TOOL_USE,
            max_tool_iterations=3,
        ),
        AssistantAgent(
            "dynamic_responder_agent",
            fast_model_client,
            tools=tools_by_agent["dynamic_responder_agent"],
            description="从资料中的事件或进度风险提取动态事件。",
            system_message=_agent_system_message("dynamic_responder_agent"),
            tool_call_summary_formatter=_tool_call_summary_formatter,
            reflect_on_tool_use=REFLECT_ON_TOOL_USE,
            max_tool_iterations=3,
        ),
        AssistantAgent(
            "plan_arbiter_agent",
            fast_model_client,
            tools=tools_by_agent["plan_arbiter_agent"],
            description="生成、评分并选择调整方案。",
            system_message=_agent_system_message("plan_arbiter_agent"),
            tool_call_summary_formatter=_tool_call_summary_formatter,
            reflect_on_tool_use=REFLECT_ON_TOOL_USE,
            max_tool_iterations=3,
        ),
        AssistantAgent(
            "coordinator_agent",
            planning_model_client,
            tools=tools_by_agent["coordinator_agent"],
            description="总控协调，产出最终 JSON 并用 FINAL_SCHEDULE_READY 收尾。",
            system_message=_coordinator_system_message(),
            tool_call_summary_formatter=_tool_call_summary_formatter,
            reflect_on_tool_use=REFLECT_ON_TOOL_USE,
            max_tool_iterations=4,
        ),
    ]
    termination = TextMentionTermination(
        "FINAL_SCHEDULE_READY",
        sources=["coordinator_agent"],
    ) | MaxMessageTermination(max_messages)
    selector_prompt = (
        "你是施工进度计划 AgentChat 团队的选择器。candidate_func 已给出当前唯一或少量"
        "候选人；你必须只从 {participants} 中返回一个名字。"
        "目标顺序：资料解析 -> WBS -> 资源 -> 约束校验 -> 动态事件 -> 仲裁 -> 总控最终 JSON。"
        "\n\n{roles}\n\n{history}\n\n下一位："
    )
    return (
        SelectorGroupChat(
            agents,
            model_client=selector_model_client,
            termination_condition=termination,
            max_turns=int(model_settings.get("agentchat_max_turns") or DEFAULT_AGENTCHAT_MAX_TURNS),
            allow_repeated_speaker=True,
            max_selector_attempts=3,
            selector_prompt=selector_prompt,
            candidate_func=stateful_candidate_func,
        ),
        [selector_model_client, extraction_model_client, fast_model_client, planning_model_client],
    )


def build_agent_tools(
    *,
    store: ExcelBlackboardStore,
    documents: list[SourceDocument],
    draft_tables: dict[str, list[dict[str, Any]]] | None = None,
    writer_agent: str | None = None,
    tool_state: dict[str, Any] | None = None,
) -> list[Callable[..., Any]]:
    """Build Python tools exposed to AssistantAgents."""

    source_names = ", ".join(document.name for document in documents) or "未提供资料"
    source_text = concatenate_documents(documents, max_chars=12000)
    extracted_parameters = extract_parameter_checklist_by_rules(
        source_text,
        source_names=source_names,
    )
    extracted_project_parameters = build_project_parameter_rows(extracted_parameters)
    document_section_rows, document_table_rows = build_document_evidence_rows(documents)
    extracted_fact_rows = extract_facts_from_evidence(document_section_rows, document_table_rows)
    allowed_draft_tables = AGENTCHAT_DRAFT_TABLES
    if draft_tables is None:
        draft_tables = _new_draft_tables()
    if tool_state is None:
        tool_state = {}
    for sheet_name in allowed_draft_tables:
        draft_tables.setdefault(sheet_name, [])
    writable_tables = AGENT_WRITE_PERMISSIONS.get(writer_agent or "", set())

    def read_source_context(max_chars: int = 3000) -> str:
        """Return concatenated real source document text."""

        return concatenate_documents(documents, max_chars=min(max_chars, 4000))

    def read_extracted_parameter_candidates() -> str:
        """Return deterministic parameter candidates extracted from real documents."""

        return json.dumps(
            {
                "parameter_checklist": extracted_parameters,
                "project_parameters": extracted_project_parameters,
                "document_sections": document_section_rows[:80],
                "document_tables": document_table_rows[:40],
                "extracted_facts": extracted_fact_rows,
            },
            ensure_ascii=False,
            default=str,
        )

    def read_document_evidence(max_rows: int = 80) -> str:
        """Return parsed evidence rows with evidence_id values."""

        limit = max(1, min(int(max_rows or 80), 120))
        return json.dumps(
            {
                "document_sections": document_section_rows[:limit],
                "document_tables": document_table_rows[: max(1, limit // 2)],
                "extracted_facts": extracted_fact_rows,
            },
            ensure_ascii=False,
            default=str,
        )

    def read_blackboard_sheet(sheet_name: str) -> str:
        """Return current in-memory draft rows as JSON."""

        if sheet_name not in allowed_draft_tables:
            raise ValueError(f"Sheet {sheet_name!r} is not available as an AgentChat draft.")
        return json.dumps(draft_tables.get(sheet_name) or [], ensure_ascii=False, default=str)

    def write_blackboard_table(sheet_name: str, rows_json: str) -> str:
        """Update one in-memory AgentChat draft table without writing Excel."""

        if sheet_name not in allowed_draft_tables:
            raise ValueError(f"Sheet {sheet_name!r} is not writable by AgentChat tools.")
        if sheet_name not in writable_tables:
            agent_label = writer_agent or "unknown_agent"
            allowed = ", ".join(sorted(writable_tables)) or "none"
            raise ValueError(
                f"{agent_label} may not write {sheet_name!r}. Allowed draft sheets: {allowed}."
            )
        if writer_agent == "coordinator_agent":
            coordinator_writes = int(tool_state.get("coordinator_write_blackboard_table_calls") or 0)
            if coordinator_writes >= COORDINATOR_WRITE_BLACKBOARD_LIMIT:
                tool_state["coordinator_write_limit_reached"] = True
                return _build_final_content_from_drafts(draft_tables)
        try:
            parsed = json.loads(rows_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"rows_json is not valid JSON: {exc}") from exc
        if isinstance(parsed, dict) and "rows" in parsed:
            parsed = parsed["rows"]
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise ValueError("rows_json must be a JSON array of row objects.")
        normalized = [_normalize_tool_row(sheet_name, item) for item in parsed]
        draft_tables[sheet_name] = normalized
        result = {
            "sheet": sheet_name,
            "draft_rows": len(normalized),
            "excel_written": False,
            "note": "Draft kept in memory; Excel will be written once after final validation.",
        }
        if writer_agent == "coordinator_agent":
            coordinator_write_count = int(
                tool_state.get("coordinator_write_blackboard_table_calls") or 0
            ) + 1
            tool_state["coordinator_write_blackboard_table_calls"] = coordinator_write_count
            result["coordinator_write_blackboard_table_calls"] = coordinator_write_count
            result["coordinator_write_limit"] = COORDINATOR_WRITE_BLACKBOARD_LIMIT
        return json.dumps(result, ensure_ascii=False)

    def validate_candidate_output(candidate_json: str) -> str:
        """Validate a candidate final JSON payload without writing to Excel."""

        payload = parse_agentchat_json(candidate_json)
        validate_agentchat_payload(payload, source_documents=documents)
        return "validation_passed"

    def build_final_payload_from_drafts() -> str:
        """Assemble the final marked JSON payload from shared in-memory drafts."""

        if not _all_target_drafts_ready(draft_tables):
            missing = [
                sheet_name
                for sheet_name in TARGET_DRAFT_TABLES
                if not draft_tables.get(sheet_name)
            ]
            raise ValueError(
                "Cannot build final payload; missing draft rows for: "
                + ", ".join(missing)
            )
        final_content = _build_final_content_from_drafts(draft_tables)
        payload = parse_agentchat_json(final_content)
        validate_agentchat_payload(payload, source_documents=documents)
        return final_content

    def required_output_contract() -> str:
        """Return the compact final JSON contract."""

        return (
            "Final JSON: {\"tables\": {\"parameter_checklist\": [], "
            "\"project_parameters\": [], \"wbs_tasks_final\": [], "
            "\"resource_plan_final\": [], \"event_log\": [], "
            "\"adjustment_plan\": []}}. Use only existing Excel schema fields. "
            "For wbs_tasks_final, every row must include task_id, wbs_code, "
            "task_name, duration_days, predecessor_ids, relation_type, lag_days, "
            "source, confidence, and note. Must use wbs_code. Do not use non-schema "
            "fields such as predecessor_task_ids, successor_task_ids, predecessors, "
            "or resources. predecessor_ids must reference existing task_id values or "
            "be empty for start tasks. Minimal WBS row shape example only: "
            "{\"task_id\":\"TASK-0001\",\"wbs_code\":\"01.01.001\","
            "\"task_name\":\"...\",\"duration_days\":1,\"predecessor_ids\":\"\","
            "\"relation_type\":\"FS\",\"lag_days\":0,\"source\":\"...\","
            "\"confidence\":\"0.70\",\"note\":\"...\"}. "
            "Every core generated row must include source, confidence, and note "
            "where the sheet schema provides those fields. "
            "Use evidence_id when a row comes from document_sections/document_tables/extracted_facts. "
            "For high-rise residential work with basement, target 40-80 connected WBS tasks; "
            "the relaxed fatal floor is 20 tasks, so do not compress to a 8-16 task summary. "
            "Segment WBS by preparation, earthwork/support/dewatering, basement structure, "
            "floor bands, secondary structure, roof, finishes, MEP/fire/HVAC, outdoor works, and acceptance. "
            "Keep resource_plan_final source-aware; never override explicit equipment facts with inferred models. "
            "adjustment_plan must not be empty; write a no-trigger/no-adjustment row when no event exists. "
            "Intermediate write_blackboard_table calls update only in-memory JSON drafts; "
            f"coordinator_agent may perform at most {COORDINATOR_WRITE_BLACKBOARD_LIMIT} repair writes per run; "
            "Excel is written after validation or best-effort forced persistence."
        )

    return [
        read_source_context,
        read_extracted_parameter_candidates,
        read_document_evidence,
        read_blackboard_sheet,
        write_blackboard_table,
        validate_candidate_output,
        build_final_payload_from_drafts,
        required_output_contract,
    ]


def build_agentchat_task(documents: list[SourceDocument]) -> str:
    """Build the initial task prompt for the production team."""

    source_names = ", ".join(document.name for document in documents)
    return f"""
你正在基于真实工程资料协作生成施工进度计划。Source files: {source_names}

Team goal:
- 先形成可信 draft tables，再由 coordinator_agent 收敛为最终 JSON，而不是直接跳到最终答案。
- 每个专业 agent 只负责自己的专业判断和自己负责的 draft sheet，不越权代写别人的结论。
- 所有结论都必须区分 explicit evidence 与 reasonable inference。

Global principles:
{_build_global_prompt_principles()}

Final output contract:
- Required tables: parameter_checklist, project_parameters, wbs_tasks_final, resource_plan_final, event_log, adjustment_plan.
- Final message must start with the completion marker defined in the coordinator system message and then output one compact JSON object only.
- Only coordinator_agent may output the final JSON.
- coordinator_agent must read required_output_contract, read_extracted_parameter_candidates, read_document_evidence, and every draft sheet before finalizing.
- coordinator_agent must assemble a candidate JSON and call validate_candidate_output before emitting the completion marker, or call build_final_payload_from_drafts and let that FINAL_SCHEDULE_READY tool result end the run directly.
- coordinator_agent may call write_blackboard_table at most {COORDINATOR_WRITE_BLACKBOARD_LIMIT} times per run; beyond that the runtime persists best-effort output and records remaining issues.

Domain quality bar:
- For high-rise residential work with basement, target 40-80 connected WBS tasks. The relaxed fatal floor is 20 tasks; do not compress the plan to a 8-16 task summary.
- Segment WBS by preparation, earthwork/support/dewatering, basement structure, floor bands, secondary structure, roof, finishes, MEP/fire/HVAC, outdoor works, and acceptance.
- resource_plan_final must reference valid task_id values and use source equipment facts before inferred values.
- adjustment_plan must not be empty; if no event exists, write one no-trigger/no-adjustment row.
- Every row with source/confidence/note schema fields must fill them.
- Use evidence_id when a parameter, resource, or WBS row is tied to document_sections, document_tables, or extracted_facts evidence.
- If source documents lack P-002/P-003, keep them visibly inferred; the runtime can generate low-confidence reference dates only for scheduling.

Normal handoff contract for non-coordinator agents:
{_build_handoff_contract()}
""".strip()
    return f"""
请基于真实工程资料完成施工进度计划 AgentChat 协作。资料文件：{source_names}

硬性要求：
1. 禁止使用固定样例、默认清单、模板 WBS、样例事件或预设调整方案。
2. 可以基于资料和工程经验进行模型推断，并自动采用，但必须在 source/confidence/note 中说明。
3. 必须保留既有 Excel 黑板契约，最终输出一个 JSON object，包含 tables 字段。
4. tables 必须至少包含：parameter_checklist、project_parameters、
   wbs_tasks_final、resource_plan_final。
5. 可选包含：event_log、adjustment_plan；如 adjustment_plan 非空，只能选择一个 selected_flag=true。
6. wbs_tasks_final 的 task_id 不得重复，predecessor_ids 必须引用已存在 task_id，
   duration_days 必须为正数。
7. resource_plan_final 必须引用已存在 task_id，并包含 demand、capacity、period。
8. 所有核心成果行只要 schema 有 source/confidence/note 字段就必须填写；
   模型推断自动采用时 source 写“模型推断+资料上下文”，confidence 写 0-1 数字。
9. 在最终消息中先写系统消息里指定的完成标记，然后给出 JSON，不要输出其他解释性段落。
10. WBS 目标为 40-80 个连通任务；高层住宅且含地下室时低阈值硬门禁为 20 个任务。
11. 资料参数优先调用 read_extracted_parameter_candidates，不要自己重造参数清单。

中间 Agent 只输出 500 字以内摘要，不要输出完整表格数组。
每个专业 Agent 通过 write_blackboard_table 写入自己负责的 Excel 草稿表。
coordinator_agent 最终调用工具查看资料、草稿表和契约，直接输出完整 JSON。
""".strip()


def _normalize_tool_row(sheet_name: str, row: dict[str, Any]) -> dict[str, Any]:
    spec = get_sheet_spec(sheet_name)
    output = {header: row.get(header) for header in spec.headers if header in row}
    if sheet_name == "wbs_tasks_final":
        _copy_legacy_predecessor_field(row, output)
        output.setdefault("source", "模型推断+资料上下文")
        output.setdefault("confidence", "0.70")
        output.setdefault("note", "AgentChat draft row")
        output.setdefault("owner_agent", "wbs_planner_agent")
        output.setdefault("relation_type", "FS")
        output.setdefault("lag_days", 0)
    elif sheet_name == "resource_plan_final":
        output.setdefault("source", "模型推断+资料上下文")
        output.setdefault("confidence", "0.70")
        output.setdefault("note", "AgentChat draft row")
        output.setdefault("owner_agent", "resource_allocator_agent")
        output.setdefault("conflict_flag", False)
    elif sheet_name == "event_log":
        output.setdefault("created_at", now_iso())
        output.setdefault("created_by", "dynamic_responder_agent")
        output.setdefault("status", "generated")
        output.setdefault("source", "模型推断+资料上下文")
        output.setdefault("confidence", "0.65")
        output.setdefault("note", "AgentChat draft row")
    elif sheet_name == "adjustment_plan":
        output.setdefault("created_at", now_iso())
        output.setdefault("created_by", "plan_arbiter_agent")
        output.setdefault("selected_flag", False)
        output.setdefault("source", "模型推断+资料上下文")
        output.setdefault("confidence", "0.65")
        output.setdefault("note", "AgentChat draft row")
    elif sheet_name == "parameter_checklist":
        output.setdefault("source", "模型推断+资料上下文")
        output.setdefault("note", "AgentChat draft row")
        output.setdefault("created_by", "data_parser_agent")
        output.setdefault("created_at", now_iso())
    elif sheet_name == "project_parameters":
        output.setdefault("source", "模型推断+资料上下文")
        output.setdefault("confidence", "0.70")
        output.setdefault("note", "AgentChat draft row")
        output.setdefault("updated_at", now_iso())
        output.setdefault("created_by", "data_parser_agent")
        output.setdefault("confirmed_by", "agentchat_model")
    return output


def _snapshot_row(
    sheet_name: str,
    row: dict[str, Any],
    *,
    attempt: int,
    index: int,
) -> dict[str, Any]:
    """Return a schema-valid snapshot row without changing the in-memory draft."""

    output = _normalize_tool_row(sheet_name, row)
    if sheet_name == "parameter_checklist":
        output.setdefault("parameter_id", f"SNAP-P-{attempt:02d}-{index:03d}")
        output.setdefault("name", output.get("parameter_id"))
        output.setdefault("required", "unknown")
        output.setdefault("status", output.get("extraction_status") or "draft")
    elif sheet_name == "project_parameters":
        output.setdefault("parameter_id", f"SNAP-P-{attempt:02d}-{index:03d}")
        output.setdefault("value", "draft_unresolved")
        output.setdefault("source", "agentchat_draft_snapshot")
        output.setdefault("confidence", "0.00")
    elif sheet_name == "wbs_tasks_final":
        output.setdefault("task_id", f"SNAP-WBS-{attempt:02d}-{index:03d}")
        output.setdefault("wbs_code", f"SNAP.{attempt:02d}.{index:03d}")
        output.setdefault("task_name", "draft task awaiting validation")
        output.setdefault("duration_days", 1)
    elif sheet_name == "resource_plan_final":
        output.setdefault("task_id", f"SNAP-WBS-{attempt:02d}-{index:03d}")
        output.setdefault("resource_type", "draft")
        output.setdefault("resource_name", "draft resource awaiting validation")
        output.setdefault("demand", 0)
        output.setdefault("capacity", 0)
    elif sheet_name == "event_log":
        output.setdefault("event_id", f"SNAP-EVT-{attempt:02d}-{index:03d}")
        output.setdefault("event_type", "draft.event")
        output.setdefault("priority", "low")
        output.setdefault("status", "draft")
    elif sheet_name == "adjustment_plan":
        output.setdefault("plan_id", f"SNAP-PLAN-{attempt:02d}-{index:03d}")
        output.setdefault("event_id", "")
        output.setdefault("measure", "draft adjustment awaiting validation")
        output.setdefault("score", 0)
    return output


def _copy_legacy_predecessor_field(row: dict[str, Any], output: dict[str, Any]) -> None:
    """Map the old `predecessor` field name to the formal WBS schema."""

    if output.get("predecessor_ids"):
        return
    predecessor = row.get("predecessor")
    if predecessor is not None:
        output["predecessor_ids"] = predecessor


def build_revision_task(
    *,
    documents: list[SourceDocument],
    previous_final: str,
    validation_error: str,
    attempt: int,
) -> str:
    """Build a compact correction task after local schema/CPM validation fails."""

    source_names = ", ".join(document.name for document in documents)
    previous_excerpt = _mask_completion_marker(previous_final).replace("\r", " ").replace("\n", " ")
    if len(previous_excerpt) > 2000:
        previous_excerpt = previous_excerpt[:2000] + "...<truncated>"
    safe_validation_error = _mask_completion_marker(validation_error)
    return f"""
Validation revision attempt {attempt} for the real construction schedule case.
Source files: {source_names}

The prior final JSON failed local Excel schema / predecessor / CPM validation:
{safe_validation_error}

Previous final output excerpt:
{previous_excerpt}

Regenerate the complete final JSON only. Keep the same contract:
- Start the final coordinator message with the completion marker from the coordinator
  system message.
- Output a single JSON object with tables.parameter_checklist, project_parameters,
  wbs_tasks_final, resource_plan_final, event_log, adjustment_plan.
- If the validation error points to a specific draft sheet or row family, use
  write_blackboard_table to correct the relevant draft rows before re-validating.
- Do not use examples, templates, default rows, fixed WBS, sample events, or preset plans.
- Use source="模型推断+资料上下文" when the model infers a row from context.
- Include confidence and note for every row whose sheet schema supports them.
- For high-rise residential work with basement, target 40-80 connected WBS tasks.
  The relaxed fatal floor is 20 tasks; do not compress to a 8-16 task summary.
- Segment WBS by preparation, earthwork/support/dewatering, basement structure,
  floor bands, secondary structure, roof, finishes, MEP/fire/HVAC, outdoor works,
  and acceptance.
- Use exact Excel schema field names: wbs_tasks_final requires task_id, wbs_code,
  task_name, duration_days, predecessor_ids, relation_type, lag_days, source,
  confidence, note; do not use predecessor_task_ids/successor_task_ids/predecessors/resources.
  resource_plan_final requires task_id, resource_type,
  resource_name, demand, capacity.
- project_parameters P-002 value must be a real ISO date like 2026-03-01 when source provides it; if source lacks it, mark it as model_inference_for_scheduling with low confidence.
- demand, capacity, duration_days, lag_days, impact_days, recovered_days, and score
  must be numeric values, not strings with units or Chinese day labels.
""".strip()


def build_responsible_agent_repair_task(
    *,
    documents: list[SourceDocument],
    validation_error: str,
    attempt: int,
    draft_tables: dict[str, list[dict[str, Any]]],
    previous_final: str,
) -> str:
    """Build the first repair task that sends concrete coordinator feedback to owners."""

    source_names = ", ".join(document.name for document in documents)
    safe_validation_error = _mask_completion_marker(validation_error)
    owner_agent = _first_problem_owner_agent(draft_tables)
    related_sheets = ", ".join(_infer_problem_sheets(validation_error, draft_tables))
    draft_counts = ", ".join(
        f"{sheet_name}={len(draft_tables.get(sheet_name, []))}"
        for sheet_name in TARGET_DRAFT_TABLES
    )
    previous_excerpt = _mask_completion_marker(previous_final).replace("\r", " ").replace("\n", " ")
    if len(previous_excerpt) > 1200:
        previous_excerpt = previous_excerpt[:1200] + "...<truncated>"
    return f"""
Responsible-agent repair attempt {attempt} for the real construction schedule case.
Source files: {source_names}

coordinator_agent must first state a concise repair brief for {owner_agent}, including:
1. Specific problem(s): quote the exact failing sheet/field/row family from the validation error.
2. Modification direction: describe how the responsible agent should change its own draft sheet.
3. Acceptance check: name the validation condition that must pass after the change.

Local validation error:
{safe_validation_error}

Likely responsible agent: {owner_agent}
Likely affected sheet(s): {related_sheets}
Current draft row counts: {draft_counts}
Previous final output excerpt:
{previous_excerpt or "<empty>"}

Required flow for this attempt:
- coordinator_agent sends the concrete repair brief above as information to the responsible agent.
- {owner_agent} must read the repair brief and the relevant draft sheet, then call write_blackboard_table only for its own allowed sheet(s).
- coordinator_agent then reads all six draft sheets, validates the candidate output, and emits the final marked JSON.
- If the responsible agent does not modify the issue, do not hide that failure; the next attempt gives coordinator_agent direct modification authority.
- Keep the final JSON contract unchanged.
""".strip()


def build_coordinator_takeover_task(
    *,
    documents: list[SourceDocument],
    validation_error: str,
    attempt: int,
    draft_tables: dict[str, list[dict[str, Any]]],
    previous_final: str,
) -> str:
    """Build the final repair task where coordinator may directly rewrite draft rows."""

    source_names = ", ".join(document.name for document in documents)
    safe_validation_error = _mask_completion_marker(validation_error)
    related_sheets = ", ".join(_infer_problem_sheets(validation_error, draft_tables))
    draft_counts = ", ".join(
        f"{sheet_name}={len(draft_tables.get(sheet_name, []))}"
        for sheet_name in TARGET_DRAFT_TABLES
    )
    previous_excerpt = _mask_completion_marker(previous_final).replace("\r", " ").replace("\n", " ")
    if len(previous_excerpt) > 1200:
        previous_excerpt = previous_excerpt[:1200] + "...<truncated>"
    return f"""
Coordinator takeover repair attempt {attempt} for the real construction schedule case.
Source files: {source_names}

The responsible-agent repair did not produce a valid result. coordinator_agent is now authorized
to directly modify the affected draft rows with write_blackboard_table.

Remaining validation error:
{safe_validation_error}

Likely affected sheet(s): {related_sheets}
Current draft row counts: {draft_counts}
Previous final output excerpt:
{previous_excerpt or "<empty>"}

Only coordinator_agent should act now:
- Read required_output_contract, read_extracted_parameter_candidates, read_document_evidence,
  and all six draft sheets with read_blackboard_sheet.
- Directly rewrite only the rows needed to fix the concrete validation issue.
- Call validate_candidate_output or build_final_payload_from_drafts after editing.
- Emit one final message that starts with the coordinator completion marker from the system
  message, followed by exactly one JSON object.
- Whether validation eventually passes or not, the runtime will persist the best available
  blackboard tables to Excel.
""".strip()


def build_missing_final_marker_task(
    *,
    documents: list[SourceDocument],
    validation_error: str,
    attempt: int,
    draft_tables: dict[str, list[dict[str, Any]]],
    previous_output: str,
    stop_reason: str | None,
) -> str:
    """Build a coordinator-only retry when the team stopped before final output."""

    source_names = ", ".join(document.name for document in documents)
    safe_validation_error = _mask_completion_marker(validation_error)
    draft_counts = ", ".join(
        f"{sheet_name}={len(draft_tables.get(sheet_name, []))}"
        for sheet_name in TARGET_DRAFT_TABLES
    )
    previous_excerpt = _mask_completion_marker(previous_output).replace("\r", " ").replace(
        "\n", " "
    )
    if len(previous_excerpt) > 1200:
        previous_excerpt = previous_excerpt[:1200] + "...<truncated>"
    return f"""
Coordinator finalization retry attempt {attempt} for the real construction schedule case.
Source files: {source_names}

Validation failure:
{safe_validation_error}

This is a coordinator_agent finalization failure, not a WBS rewrite request.
The specialist draft handoffs already ran; current draft row counts are: {draft_counts}.
Previous last output/source excerpt: {previous_excerpt or "<empty>"}
Prior stop_reason: {stop_reason or "<none>"}

Only coordinator_agent should act now:
- Read required_output_contract, read_extracted_parameter_candidates, read_document_evidence,
  and all six draft sheets with read_blackboard_sheet.
- If every target draft sheet is populated, call build_final_payload_from_drafts.
- If a draft sheet is missing or invalid, correct only the specific relevant rows with
  write_blackboard_table, then call validate_candidate_output.
- Emit one final message that starts with the coordinator completion marker from the
  system message, followed by exactly one JSON object.
- Do not ask wbs_planner_agent to redo wbs_tasks_final unless validation reports a
  concrete WBS schema, predecessor, or CPM defect.
""".strip()


def build_full_team_retry_task(
    *,
    documents: list[SourceDocument],
    validation_error: str,
    attempt: int,
    original_task: str,
) -> str:
    """Build a retry task that still runs the full team when no final payload exists."""

    source_names = ", ".join(document.name for document in documents)
    safe_validation_error = _mask_completion_marker(validation_error)
    return f"""
Full-team retry attempt {attempt} for the real construction schedule case.
Source files: {source_names}

The prior attempt did not produce a valid final coordinator JSON payload:
{safe_validation_error}

Run the complete collaboration again instead of jumping directly to finalization:
- data_parser_agent prepares parameter_checklist and project_parameters.
- wbs_planner_agent prepares wbs_tasks_final.
- resource_allocator_agent prepares resource_plan_final.
- constraint_checker_agent checks blocking issues.
- dynamic_responder_agent prepares event_log.
- plan_arbiter_agent prepares adjustment_plan.
- coordinator_agent reads all draft sheets, can rewrite the relevant draft rows with write_blackboard_table when validation finds a problem, validates a candidate, and only then emits the completion marker from its system message.

Original task:
{_mask_completion_marker(original_task)}
""".strip()


def _agent_system_message(agent_name: str) -> str:
    return _build_agent_prompt(agent_name)
    return (
        f"你是 {agent_name}。你正在真实 AutoGen AgentChat 团队中协作生成施工进度计划。"
        "所有输出必须来自真实资料、工具读取结果或明确标注的模型推断。"
        "禁止引用固定样例、模板任务、默认参数或演示数据。"
        "你只能输出 500 字以内的职责摘要和关键依据，不要输出完整 JSON 表格数组。"
        f"{write_instruction}"
        "最终 JSON 只能由 coordinator_agent 输出。"
    )


def _coordinator_system_message() -> str:
    return (
        "你是 coordinator_agent，负责收敛团队草稿并产出最终 JSON，不负责随意跳过自检。 "
        "Final message must start with FINAL_SCHEDULE_READY followed by exactly one JSON object. "
        "Before finalizing, you must read required_output_contract, "
        "read_extracted_parameter_candidates, read_document_evidence, and every draft sheet via read_blackboard_sheet. "
        f"{_build_global_prompt_principles()} "
        "When all six draft sheets are populated, call build_final_payload_from_drafts instead of hand-writing the large JSON. "
        "If build_final_payload_from_drafts returns FINAL_SCHEDULE_READY with a JSON object, that tool result is the final result; do not wait to restate it in chat. "
        "If you build your own candidate final JSON, call validate_candidate_output before emitting FINAL_SCHEDULE_READY. "
        "After the first generated plan, if validation or your own review finds problems, you must name the specific failing sheet/field/row family, give a concrete modification direction, and pass that brief to the responsible specialist agent before taking over. "
        "If the responsible specialist receives that information but does not fix the issue, you are authorized to directly rewrite the affected draft rows with write_blackboard_table and then validate again. "
        f"Within one run, coordinator_agent may call write_blackboard_table at most {COORDINATOR_WRITE_BLACKBOARD_LIMIT} times; after that, stop editing and let the runtime persist best-effort output with debug_records/quality_gates. "
        f"{_build_coordinator_checklist()} "
        "Conflict resolution priority: explicit evidence > cross-table consistency > schedulability > completeness > concise wording. "
        "JSON shape must remain {\"tables\": {\"parameter_checklist\": [...], \"project_parameters\": [...], "
        "\"wbs_tasks_final\": [...], \"resource_plan_final\": [...], \"event_log\": [...], "
        "\"adjustment_plan\": [...]}}. "
        "Each row may only use fields from the Excel schema. Do not output explanations after the final JSON."
    )
    return (
        "你是 coordinator_agent，总控调度 Agent。你必须收敛团队讨论并输出最终 JSON。"
        "最终消息必须以 FINAL_SCHEDULE_READY 开头，随后只输出一个 JSON object。"
        "最终 JSON 需要覆盖分段 WBS；高层住宅且含地下室时目标为 40-80 个任务。"
        "必须调用 required_output_contract 和 read_extracted_parameter_candidates。"
        "JSON 形如 {\"tables\": {\"parameter_checklist\": [...], \"project_parameters\": [...], "
        "\"wbs_tasks_final\": [...], \"resource_plan_final\": [...], \"event_log\": [...], "
        "\"adjustment_plan\": [...]}}。"
        "每个 row 只能使用 Excel schema 中已有字段。"
        "你可以直接调用 read_source_context 和 required_output_contract，然后一次性生成 JSON。"
    )


def _build_global_prompt_principles() -> str:
    """Return team-wide prompt principles shared by all AgentChat roles."""

    return (
        "显式资料优先；不得覆盖已明确事实；先写 draft tables 再给结论；"
        "所有 inference 都要能回溯到 source context 或已写草稿；"
        "禁止使用 samples、fixed templates、default rows、demo data；"
        "只要 schema 提供 source/confidence/note，就必须填写；"
        "有 evidence_id 时必须保留 evidence_id。"
    )


def _build_handoff_contract() -> str:
    """Return the compact handoff format for intermediate agents."""

    return (
        "Use five short lines only: "
        "Status:, Evidence:, Draft written:, Risks/Gaps:, Next agent hint:. "
        "Keep the full reply within about 350-500 characters. "
        "Do not print full arrays, do not restate the background, do not announce final completion, "
        "and do not judge another agent's domain unless you found a blocking issue."
    )


def _build_agent_prompt(agent_name: str) -> str:
    """Build a role-specific system message for one AgentChat participant."""

    prompts = {
        "data_parser_agent": (
            "你是 data_parser_agent。\n"
            "Role: 从真实资料中抽取项目参数，建立 parameter_checklist 与 project_parameters，为后续 WBS 和资源规划提供可信输入。\n"
            "What you must read first: 先调用 read_extracted_parameter_candidates 和 read_document_evidence；必要时补读 read_source_context。\n"
            "What you may write: 只能通过 write_blackboard_table 写 parameter_checklist 和 project_parameters。\n"
            "What you must not do: 不生成 WBS、不生成资源计划、不生成 event_log、不生成 adjustment_plan；不要把缺失参数伪装成已确认事实。\n"
            "Quality bar: 明确区分 confirmed / missing / needs_confirmation / inferred parameters；优先保留显式资料值；推断值必须在 source/confidence/note 中标明；有 evidence_id 时必须带上。\n"
            f"Handoff format: {_build_handoff_contract()}"
        ),
        "wbs_planner_agent": (
            "你是 wbs_planner_agent。\n"
            "Role: 基于 source context 和 project parameters 生成可排程、可挂资源、可建立前置关系的 wbs_tasks_final。\n"
            "What you must read first: 先调用 read_source_context，再读取 read_blackboard_sheet('project_parameters')；必要时读取 parameter_checklist 和 document evidence。\n"
            "What you may write: 只能通过 write_blackboard_table 写 wbs_tasks_final。\n"
            "What you must not do: 不写 resource_plan_final、不写 event_log、不写 adjustment_plan；不要把计划压缩成 8-16 个摘要任务；不要输出完整表数组；不得使用 predecessor_task_ids / successor_task_ids / predecessors / resources 等非 schema 字段。\n"
            "Required output contract: 必须使用 wbs_code，不得使用 predecessor_task_ids / successor_task_ids / predecessors / resources 等非 schema 字段。predecessor_ids 必须引用已存在 task_id。每行必须包含 task_id, wbs_code, task_name, duration_days, predecessor_ids, relation_type, lag_days, source, confidence, note。极短结构示例：{\"task_id\":\"TASK-0001\",\"wbs_code\":\"01.01.001\",\"task_name\":\"...\",\"duration_days\":1,\"predecessor_ids\":\"\",\"relation_type\":\"FS\",\"lag_days\":0,\"source\":\"...\",\"confidence\":\"0.70\",\"note\":\"...\"}。\n"
            "Quality bar: WBS 需要覆盖 preparation、earthwork/support/dewatering、basement structure、floor bands、secondary structure、roof、finishes、MEP/fire/HVAC、outdoor works、acceptance；任务粒度要能直接进入排程和资源配置；显式工序事实优先于经验补全。\n"
            f"Handoff format: {_build_handoff_contract()}"
        ),
        "resource_allocator_agent": (
            "你是 resource_allocator_agent。\n"
            "Role: 基于 wbs_tasks_final 和真实资料，生成 task linked 的 resource_plan_final，并识别容量冲突。\n"
            "What you must read first: 先读取 read_blackboard_sheet('wbs_tasks_final')；必要时回看 read_document_evidence 和 read_source_context。\n"
            "What you may write: 只能通过 write_blackboard_table 写 resource_plan_final。\n"
            "What you must not do: 不修改 WBS 结构；不凭空覆盖资料中已经明确的设备、班组、产能事实；不输出最终 JSON。\n"
            "Quality bar: 每条资源行都要引用有效 task_id，并给出 numeric demand、capacity、period；先使用 source equipment facts，再补充合理 inference；对冲突行明确 conflict_flag 和依据。\n"
            f"Handoff format: {_build_handoff_contract()}"
        ),
        "constraint_checker_agent": (
            "你是 constraint_checker_agent。\n"
            "Role: 识别 schema、前置关系、CPM、资源字段和成果一致性问题，负责指出阻塞问题而不是代写整表。\n"
            "What you must read first: 先读取各 draft sheets，至少包括 parameter_checklist、project_parameters、wbs_tasks_final、resource_plan_final、event_log、adjustment_plan；必要时调用 validate_candidate_output 检查候选最终 JSON。\n"
            "What you may write: 默认不补写大表；你的核心职责是指出 blocking issues 与 improvement suggestions，并提醒 coordinator 修正。\n"
            "What you must not do: 不输出最终 JSON；不要因为发现问题就自行重写别人的专业表；不要把建议说成已经修复完成。\n"
            "Quality bar: 明确区分 blocking issues 和 improvement suggestions；重点关注断链 predecessor、非法 task_id 引用、缺字段、非 numeric 工期/资源值、表间冲突和不可排程点。\n"
            f"Handoff format: {_build_handoff_contract()}"
        ),
        "dynamic_responder_agent": (
            "你是 dynamic_responder_agent。\n"
            "Role: 从资料中的事件线索、供应风险、季节条件、场地限制或进度风险中提取 event_log。\n"
            "What you must read first: 先读取 read_document_evidence；必要时补读 read_source_context 和相关 draft sheets。\n"
            "What you may write: 只能通过 write_blackboard_table 写 event_log。\n"
            "What you must not do: 没有证据时不要硬造事件；不要直接生成 adjustment_plan；不要把一般性施工常识包装成已发生事件。\n"
            "Quality bar: 只有真实资料支持时才写事件；若未发现可写事件，要明确说明未发现，并为后续 adjustment_plan 保留 no-trigger/no-adjustment 路径；事件行需说明触发依据、影响对象和影响程度。\n"
            f"Handoff format: {_build_handoff_contract()}"
        ),
        "plan_arbiter_agent": (
            "你是 plan_arbiter_agent。\n"
            "Role: 基于 event_log 或 constraint evidence 生成、比较并选择 adjustment_plan。\n"
            "What you must read first: 先读取 read_blackboard_sheet('event_log')、read_blackboard_sheet('resource_plan_final')、read_blackboard_sheet('wbs_tasks_final')；必要时补读约束相关信息。\n"
            "What you may write: 只能通过 write_blackboard_table 写 adjustment_plan。\n"
            "What you must not do: 没有触发条件时不要凭空设计复杂调整方案；不要选择多个 selected_flag=true；不要输出最终 JSON。\n"
            "Quality bar: 有事件或约束时给出可执行方案并说明选择理由；没有触发条件时写一条 no-trigger/no-adjustment 行满足非空要求；只能保留一个推荐方案 selected_flag=true。\n"
            f"Handoff format: {_build_handoff_contract()}"
        ),
    }
    prompt = prompts.get(agent_name)
    if prompt is None:
        raise ValueError(f"Unknown AgentChat agent name: {agent_name}")
    return (
        f"{prompt}\n"
        f"Global principles: {_build_global_prompt_principles()}\n"
        "Only coordinator_agent may output the final JSON."
    )


def _build_coordinator_checklist() -> str:
    """Return the mandatory coordinator self-check list."""

    return (
        "Before final output, verify all six target tables exist; "
        "verify WBS depth is not a summary-only outline and stays within the intended construction phases; "
        "verify resource_plan_final references valid task_id values; "
        "verify adjustment_plan is not empty and has at most one selected_flag=true row; "
        "verify explicit evidence was not overridden by later inference; "
        "if you find a problem, rewrite the relevant draft rows with write_blackboard_table and re-run validation."
        " On first review failure, first hand the specific issue and modification direction to the responsible specialist agent; "
        "only take direct edit ownership after that specialist has had a repair attempt and the issue remains. "
        f"coordinator_agent may call write_blackboard_table at most {COORDINATOR_WRITE_BLACKBOARD_LIMIT} times per run."
    )


def _message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _all_target_drafts_ready(draft_tables: dict[str, list[dict[str, Any]]]) -> bool:
    return all(bool(draft_tables.get(sheet_name)) for sheet_name in TARGET_DRAFT_TABLES)


def _extract_final_payload_from_message(message: Any) -> str:
    content = _message_content(message)
    extracted = _extract_final_payload_from_text(content)
    if extracted:
        return extracted
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return ""
    tool_results = getattr(message, "tool_results", None)
    if not tool_results:
        return ""
    for result in tool_results:
        result_content = _message_content(result)
        extracted = _extract_final_payload_from_text(result_content)
        if extracted:
            return extracted
    return ""


def _extract_final_payload_from_text(content: str) -> str:
    text = str(content or "").strip()
    marker = "FINAL_SCHEDULE_READY"
    marker_index = text.find(marker)
    if marker_index < 0:
        return ""
    payload = text[marker_index:]
    json_start = payload.find("{")
    if json_start < 0:
        return ""
    decoder = json.JSONDecoder()
    try:
        parsed, end = decoder.raw_decode(payload[json_start:])
    except json.JSONDecodeError:
        return payload if _is_final_payload_message(payload) else ""
    return marker + " " + json.dumps(parsed, ensure_ascii=False, default=str)


def _build_final_content_from_drafts(draft_tables: dict[str, list[dict[str, Any]]]) -> str:
    payload = {
        "tables": {
            "parameter_checklist": draft_tables.get("parameter_checklist", []),
            "project_parameters": draft_tables.get("project_parameters", []),
            "wbs_tasks_final": draft_tables.get("wbs_tasks_final", []),
            "resource_plan_final": draft_tables.get("resource_plan_final", []),
            "event_log": draft_tables.get("event_log", []),
            "adjustment_plan": draft_tables.get("adjustment_plan", []),
        }
    }
    return "FINAL_SCHEDULE_READY " + json.dumps(payload, ensure_ascii=False, default=str)


def _build_best_effort_payload(
    *,
    final_content: str,
    draft_tables: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build the best available payload for a forced final write."""

    if _is_final_payload_message(final_content):
        try:
            return parse_agentchat_json(final_content)
        except AgentOutputValidationError:
            pass
    if _all_target_drafts_ready(draft_tables):
        return parse_agentchat_json(_build_final_content_from_drafts(draft_tables))
    return {
        "tables": {
            "parameter_checklist": draft_tables.get("parameter_checklist", []),
            "project_parameters": draft_tables.get("project_parameters", []),
            "wbs_tasks_final": draft_tables.get("wbs_tasks_final", []),
            "resource_plan_final": draft_tables.get("resource_plan_final", []),
            "event_log": draft_tables.get("event_log", []),
            "adjustment_plan": draft_tables.get("adjustment_plan", []),
        }
    }


def _build_forced_final_content(payload: dict[str, Any]) -> str:
    """Serialize the final forced payload."""

    return "FINAL_SCHEDULE_READY " + json.dumps(payload, ensure_ascii=False, default=str)


def _is_final_payload_message(content: str) -> bool:
    stripped = content.strip()
    return stripped.startswith("FINAL_SCHEDULE_READY") and "{" in stripped and "}" in stripped


def _is_final_marker_missing_error(error: str) -> bool:
    return error.strip() == FINAL_MARKER_MISSING_ERROR


def _mask_completion_marker(value: str) -> str:
    return value.replace("FINAL_SCHEDULE_READY", "[completion-marker]")


def _runtime_failure_message(exc: Exception, *, attempt: int) -> str:
    if _exception_indicates_rate_limit(exc):
        return (
            f"AutoGen AgentChat runtime failed on attempt {attempt}: model provider "
            "rate limit was reached (HTTP 429). Stop retrying immediately, wait for "
            "the provider quota window to reset, or reduce AgentChat request volume."
        )
    if _exception_chain_contains(exc, {"APITimeoutError", "ReadTimeout", "TimeoutError"}):
        return (
            f"AutoGen AgentChat runtime failed on attempt {attempt}: model request timed out. "
            "The model endpoint did not return before OPENAI_TIMEOUT_SECONDS/config timeout. "
            "Increase OPENAI_TIMEOUT_SECONDS, reduce source document size, or retry when the "
            "provider is responsive."
        )
    return f"AutoGen AgentChat runtime failed on attempt {attempt}: {exc}"


def _runtime_fix_action(exc: Exception) -> str:
    if _exception_indicates_rate_limit(exc):
        return (
            "Wait before rerunning, avoid parallel real-case runs, and keep "
            "reflect_on_tool_use disabled to reduce model requests."
        )
    if _exception_chain_contains(exc, {"APITimeoutError", "ReadTimeout", "TimeoutError"}):
        return (
            "Retry after the model endpoint recovers, or set OPENAI_TIMEOUT_SECONDS=600 "
            "and OPENAI_MAX_RETRIES=4 in .env/config."
        )
    return "Check model endpoint and retry."


def _team_run_timeout_seconds(settings: dict[str, Any]) -> float:
    timeout = float(settings.get("team_run_timeout_seconds") or DEFAULT_TEAM_RUN_TIMEOUT_SECONDS)
    return max(timeout, 60.0)


def _repeated_validation_error_limit(settings: dict[str, Any]) -> int:
    try:
        limit = int(
            settings.get("repeated_validation_error_limit")
            or DEFAULT_REPEATED_VALIDATION_ERROR_LIMIT
        )
    except (TypeError, ValueError):
        limit = DEFAULT_REPEATED_VALIDATION_ERROR_LIMIT
    return max(1, limit)


def _track_repeated_validation_error(
    content: str,
    counts: dict[str, int],
    limit: int,
) -> str | None:
    signature = _validation_error_signature(content)
    if not signature:
        return None
    counts[signature] = counts.get(signature, 0) + 1
    if counts[signature] >= limit:
        return signature
    return None


def _validation_error_signature(content: str) -> str:
    lines = [
        line.strip()
        for line in str(content or "").replace("\\n", "\n").splitlines()
        if line.strip()
    ]
    blocker_markers = (
        "below relaxed fatal threshold",
        "exceed relaxed fatal threshold",
        "missing required values",
        "missing evidence field",
        "references missing task_id",
        "resource demand/capacity must be numeric",
        "missing valid start date",
        "wbs_tasks_final predecessor/CPM validation failed",
    )
    blockers = [line for line in lines if any(marker in line for marker in blocker_markers)]
    if not blockers:
        return ""
    return "\n".join(blockers[:6])


def _exception_indicates_rate_limit(exc: BaseException) -> bool:
    rate_limit_names = {"RateLimitError"}
    rate_limit_markers = ("429", "rate limit", "ratelimit", "速率限制", "请求频率")
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if current.__class__.__name__ in rate_limit_names:
            return True
        text = str(current).lower()
        if any(marker in text for marker in rate_limit_markers):
            return True
        current = current.__cause__ or current.__context__
    return False


def _exception_chain_contains(exc: BaseException, names: set[str]) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if current.__class__.__name__ in names:
            return True
        current = current.__cause__ or current.__context__
    return False


def _tool_call_summary_formatter(function_call: Any, result: Any) -> str:
    content = str(getattr(result, "content", result))
    if len(content) > 500:
        content = content[:500] + "...<truncated>"
    return f"{getattr(function_call, 'name', 'tool')} result: {content}"


def _new_draft_tables() -> dict[str, list[dict[str, Any]]]:
    """Create shared in-memory AgentChat drafts for one workflow run."""

    return {sheet_name: [] for sheet_name in AGENTCHAT_DRAFT_TABLES}


def _draft_has_rows(
    draft_tables: dict[str, list[dict[str, Any]]],
    sheet_name: str,
) -> bool:
    return bool(draft_tables.get(sheet_name))


def _last_message_source(messages: Any) -> str:
    for message in reversed(messages):
        source = str(getattr(message, "source", ""))
        if source:
            return source
    return ""


def _build_candidate_func(
    draft_tables: dict[str, list[dict[str, Any]]],
) -> Callable[[Any], list[str]]:
    """Route by actual draft readiness instead of only speaker history."""

    def candidate_func(messages: Any) -> list[str]:
        if not (
            _draft_has_rows(draft_tables, "parameter_checklist")
            and _draft_has_rows(draft_tables, "project_parameters")
        ):
            return ["data_parser_agent"]
        if _needs_wbs_revision(draft_tables.get("wbs_tasks_final") or []):
            return ["wbs_planner_agent"]
        if not _draft_has_rows(draft_tables, "resource_plan_final"):
            return ["resource_allocator_agent"]
        if not _draft_has_rows(draft_tables, "event_log"):
            return ["dynamic_responder_agent"]
        if not _draft_has_rows(draft_tables, "adjustment_plan"):
            return ["plan_arbiter_agent"]

        previous = _last_message_source(messages)
        if previous == "constraint_checker_agent":
            return ["coordinator_agent"]
        if previous == "coordinator_agent":
            return ["constraint_checker_agent"]
        return ["constraint_checker_agent"]

    return candidate_func


def _build_responsible_repair_candidate_func(
    draft_tables: dict[str, list[dict[str, Any]]],
) -> Callable[[Any], list[str]]:
    """Route the first validation repair to the owner of the affected draft sheet."""

    def candidate_func(messages: Any) -> list[str]:
        previous = _last_message_source(messages)
        if previous == "coordinator_agent":
            return [_first_problem_owner_agent(draft_tables)]
        if previous in set(TABLE_OWNER_AGENTS.values()):
            return ["coordinator_agent"]
        return ["coordinator_agent"]

    return candidate_func


def _first_problem_owner_agent(draft_tables: dict[str, list[dict[str, Any]]]) -> str:
    """Pick the most likely responsible specialist from current draft defects."""

    if not (
        _draft_has_rows(draft_tables, "parameter_checklist")
        and _draft_has_rows(draft_tables, "project_parameters")
    ):
        return "data_parser_agent"
    if _needs_wbs_revision(draft_tables.get("wbs_tasks_final") or []):
        return "wbs_planner_agent"
    if not _resource_refs_are_valid(
        draft_tables.get("resource_plan_final") or [],
        draft_tables.get("wbs_tasks_final") or [],
    ):
        return "resource_allocator_agent"
    if not _draft_has_rows(draft_tables, "event_log"):
        return "dynamic_responder_agent"
    if not _adjustment_plan_is_usable(draft_tables.get("adjustment_plan") or []):
        return "plan_arbiter_agent"
    return "wbs_planner_agent"


def _resource_refs_are_valid(
    resource_rows: list[dict[str, Any]],
    wbs_rows: list[dict[str, Any]],
) -> bool:
    if not resource_rows:
        return False
    task_ids = {str(row.get("task_id") or "").strip() for row in wbs_rows}
    task_ids.discard("")
    if not task_ids:
        return False
    return all(str(row.get("task_id") or "").strip() in task_ids for row in resource_rows)


def _adjustment_plan_is_usable(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    selected = [
        row for row in rows
        if str(row.get("selected_flag") or "").strip().lower() in {"true", "1", "yes", "是"}
    ]
    return len(selected) <= 1


def _infer_problem_sheets(
    validation_error: str,
    draft_tables: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Infer affected sheets from validator text and draft state."""

    error_text = validation_error.lower()
    matches = [
        sheet_name
        for sheet_name in TARGET_DRAFT_TABLES
        if sheet_name.lower() in error_text
    ]
    if "predecessor" in error_text or "cpm" in error_text or "duration" in error_text:
        matches.append("wbs_tasks_final")
    if "resource" in error_text or "demand" in error_text or "capacity" in error_text:
        matches.append("resource_plan_final")
    if "event" in error_text:
        matches.append("event_log")
    if "adjustment" in error_text or "selected_flag" in error_text:
        matches.append("adjustment_plan")
    if "parameter" in error_text or "start date" in error_text or "p-002" in error_text:
        matches.extend(["parameter_checklist", "project_parameters"])
    if not matches:
        owner = _first_problem_owner_agent(draft_tables)
        matches = [
            sheet_name
            for sheet_name, agent_name in TABLE_OWNER_AGENTS.items()
            if agent_name == owner
        ]
    deduped: list[str] = []
    for sheet_name in matches:
        if sheet_name in TARGET_DRAFT_TABLES and sheet_name not in deduped:
            deduped.append(sheet_name)
    return deduped or ["wbs_tasks_final"]


def _needs_wbs_revision(wbs_rows: list[dict[str, Any]]) -> bool:
    """Return True when WBS rows exist but the CPM network is still unusable."""

    if not wbs_rows:
        return True
    predecessor_values = [
        str(row.get("predecessor_ids") or "").strip()
        for row in wbs_rows
    ]
    if not any(predecessor_values):
        return True
    try:
        calculate_cpm(wbs_rows)
    except Exception:
        return True
    return False


def _candidate_func(messages: Any) -> list[str]:
    """Compatibility fallback for callers that do not provide draft state."""

    return _build_candidate_func(_new_draft_tables())(messages)


def _revision_candidate_func(messages: Any) -> list[str]:
    """Route validation retries straight to the coordinator for a compact final fix."""

    return ["coordinator_agent"]


def reset_agentchat_output_tables(store: ExcelBlackboardStore) -> None:
    """Clear prior generated rows so stale template outputs cannot leak into a run."""

    output_tables = (
        "parameter_checklist",
        "project_parameters",
        "wbs_tasks",
        "wbs_tasks_final",
        "resource_plan",
        "resource_plan_final",
        "resource_load_daily",
        "resource_resolution",
        "schedule_initial",
        "cpm_analysis",
        "network_edges",
        "milestone_check",
        "constraint_check",
        "event_log",
        "adjustment_plan",
        "document_sections",
        "document_tables",
        "extracted_facts",
        "parameter_audit",
        "assumption_register",
        "quality_gates",
        "debug_records",
    )
    store.replace_sheets_rows({sheet_name: [] for sheet_name in output_tables})
