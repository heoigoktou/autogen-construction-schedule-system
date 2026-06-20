from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agentchat_runtime.exceptions import AgentOutputValidationError, ModelConfigurationError
from agentchat_runtime.model_factory import (
    build_openai_chat_completion_client,
    validate_live_model_settings,
)
from agentchat_runtime.output_writer import write_agentchat_output
from agentchat_runtime.workflow import (
    AgentChatWorkflow,
    COORDINATOR_WRITE_BLACKBOARD_LIMIT,
    MAX_VALIDATION_REVISIONS,
    _exception_indicates_rate_limit,
    _build_candidate_func,
    _build_responsible_repair_candidate_func,
    build_coordinator_takeover_task,
    build_agent_tools,
    build_missing_final_marker_task,
    build_responsible_agent_repair_task,
    _agent_system_message,
    _extract_final_payload_from_text,
    _revision_candidate_func,
    _runtime_failure_message,
    _runtime_fix_action,
    _team_run_timeout_seconds,
    _track_repeated_validation_error,
)
from blackboard.excel_store import ExcelBlackboardStore
from tests.helpers import (
    minimal_adjustment_rows,
    minimal_event_rows,
    minimal_parameter_checklist,
    minimal_resource_rows,
    minimal_wbs_rows,
)
from tools.document_tools import SourceDocument
from tools.env_tools import build_model_settings


def _payload() -> dict[str, object]:
    checklist = minimal_parameter_checklist()
    return {
        "tables": {
            "parameter_checklist": checklist,
            "project_parameters": [
                {
                    "parameter_id": "P-001",
                    "value": "30天",
                    "unit": "天",
                    "source": "pytest真实资料",
                    "confidence": "0.90",
                    "confirmed_by": "agentchat_model",
                    "updated_at": "2026-01-01T00:00:00+08:00",
                    "created_by": "data_parser_agent",
                    "note": "由资料抽取",
                },
                {
                    "parameter_id": "P-002",
                    "value": "2026-03-01",
                    "unit": "",
                    "source": "pytest真实资料",
                    "confidence": "0.90",
                    "confirmed_by": "agentchat_model",
                    "updated_at": "2026-01-01T00:00:00+08:00",
                    "created_by": "data_parser_agent",
                    "note": "由资料抽取",
                },
            ],
            "wbs_tasks_final": minimal_wbs_rows(),
            "resource_plan_final": minimal_resource_rows(),
            "event_log": minimal_event_rows(),
            "adjustment_plan": minimal_adjustment_rows(),
        }
    }


def test_agentchat_fake_runner_writes_blackboard_and_schedule(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    documents = [SourceDocument(tmp_path / "case.md", "项目资料：开工日期 2026-03-01")]

    def fake_runner(task: str) -> str:
        assert "禁止使用 samples" in task
        return "FINAL_SCHEDULE_READY\n" + json.dumps(_payload(), ensure_ascii=False)

    result = AgentChatWorkflow(
        store=store,
        model_settings={"provider": "openai_compatible", "model": "fake", "api_key": "fake"},
        documents=documents,
        runner=fake_runner,
    ).run()

    assert result.written_counts["wbs_tasks_final"] == 3
    assert len(store.read_rows("schedule_initial")) == 3
    assert len(store.read_rows("cpm_analysis")) == 3
    assert len(store.read_rows("resource_load_daily")) == 2
    assert len(store.read_rows("resource_resolution")) == 1


def test_agentchat_force_writes_after_repair_and_takeover_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    documents = [SourceDocument(tmp_path / "case.md", "项目资料：开工日期 2026-03-01")]

    calls = {"count": 0}

    class FakeMessage:
        source = "coordinator_agent"
        content = "FINAL_SCHEDULE_READY {\"tables\": {}}"

    class FakeResult:
        messages = [FakeMessage()]
        stop_reason = "fake"

    class FakeTeam:
        async def run_stream(self, task: str):
            calls["count"] += 1
            yield FakeResult()

    def fake_build_selector_team(**kwargs):
        return FakeTeam(), []

    monkeypatch.setattr("agentchat_runtime.workflow.build_selector_team", fake_build_selector_team)

    result = AgentChatWorkflow(
        store=store,
        model_settings={"provider": "openai_compatible", "model": "fake", "api_key": "fake"},
        documents=documents,
    ).run()

    assert MAX_VALIDATION_REVISIONS == 3
    assert calls["count"] == 3
    assert result.final_content.startswith("FINAL_SCHEDULE_READY")
    assert store.read_rows("debug_records")
    assert store.read_rows("agent_message_log")
    assert len(store.read_rows("wbs_tasks_final")) == 0


def test_quality_gate_parameter_gaps_are_persisted_as_warnings(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    payload = _payload()
    tables = payload["tables"]
    assert isinstance(tables, dict)
    tables["parameter_checklist"] = minimal_parameter_checklist()[:2]

    counts = write_agentchat_output(
        store,
        payload,
        source_documents=[SourceDocument(tmp_path / "case.md", "limited source")],
    )

    assert counts["quality_gates"] >= 1
    coverage_gate = next(
        row for row in store.read_rows("quality_gates") if row["gate_name"] == "parameter_coverage"
    )
    assert coverage_gate["severity"] == "warn"
    assert coverage_gate["result"] == "warn"


def test_missing_schedule_dates_are_generated_as_low_confidence_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    payload = _payload()
    tables = payload["tables"]
    assert isinstance(tables, dict)
    tables["project_parameters"] = [
        row
        for row in tables["project_parameters"]  # type: ignore[index]
        if row["parameter_id"] not in {"P-002", "P-003"}
    ]
    tables["parameter_checklist"] = [
        row
        for row in tables["parameter_checklist"]  # type: ignore[index]
        if row["parameter_id"] not in {"P-002", "P-003"}
    ]
    monkeypatch.setattr("agentchat_runtime.output_writer.date", _FrozenDate)

    counts = write_agentchat_output(
        store,
        payload,
        source_documents=[SourceDocument(tmp_path / "case.md", "项目资料未提供开工日期")],
    )

    project_rows = {row["parameter_id"]: row for row in store.read_rows("project_parameters")}
    assert counts["schedule_initial"] == 3
    assert project_rows["P-002"]["value"] == "2026-06-20"
    assert project_rows["P-002"]["source"] == "model_inference_for_scheduling"
    assert float(project_rows["P-002"]["confidence"]) == 0.50
    assert project_rows["P-003"]["value"] == "2026-07-19"
    assert "仅供参考" in project_rows["P-003"]["note"]
    assert any(row["target_id"] == "P-002" for row in store.read_rows("assumption_register"))


class _FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return cls(2026, 6, 20)


def test_source_dates_are_preserved_when_present(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()

    write_agentchat_output(store, _payload())

    project_rows = {row["parameter_id"]: row for row in store.read_rows("project_parameters")}
    assert project_rows["P-002"]["value"] == "2026-03-01"
    assert project_rows["P-002"]["source"] == "pytest真实资料"


def test_repeated_validation_error_signature_stops_repair_loop() -> None:
    counts: dict[str, int] = {}
    content = "high severity audit issues 5 exceed relaxed fatal threshold 2"

    assert _track_repeated_validation_error(content, counts, 3) is None
    assert _track_repeated_validation_error(content, counts, 3) is None
    assert _track_repeated_validation_error(content, counts, 3) == content


def test_extract_final_payload_from_tool_result_text() -> None:
    content = """["content='FINAL_SCHEDULE_READY {\"tables\":{\"parameter_checklist\":[]}}'"]"""

    extracted = _extract_final_payload_from_text(content)

    assert extracted.startswith("FINAL_SCHEDULE_READY")
    assert json.loads(extracted.split(" ", 1)[1]) == {"tables": {"parameter_checklist": []}}


def test_wbs_prompt_contract_restricts_to_schema_fields() -> None:
    prompt = _agent_system_message("wbs_planner_agent")

    assert "必须使用 wbs_code" in prompt
    assert "predecessor_task_ids / successor_task_ids / predecessors / resources" in prompt
    assert "predecessor_ids 必须引用已存在 task_id" in prompt
    assert "task_id, wbs_code, task_name, duration_days" in prompt


def test_output_contract_documents_wbs_schema_fields(tmp_path: Path) -> None:
    tools = build_agent_tools(
        store=ExcelBlackboardStore(tmp_path / "blackboard.xlsx"),
        documents=[SourceDocument(tmp_path / "case.md", "case text")],
        writer_agent="wbs_planner_agent",
    )
    contract_tool = next(tool for tool in tools if tool.__name__ == "required_output_contract")

    contract = contract_tool()

    assert "Must use wbs_code" in contract
    assert "predecessor_task_ids" in contract
    assert "predecessor_ids must reference existing task_id" in contract


def test_coordinator_write_blackboard_table_limit_returns_final_payload(tmp_path: Path) -> None:
    draft_tables = {
        "parameter_checklist": minimal_parameter_checklist(),
        "project_parameters": _payload()["tables"]["project_parameters"],  # type: ignore[index]
        "wbs_tasks_final": minimal_wbs_rows(),
        "resource_plan_final": minimal_resource_rows(),
        "event_log": minimal_event_rows(),
        "adjustment_plan": minimal_adjustment_rows(),
    }
    tool_state: dict[str, object] = {}
    tools = build_agent_tools(
        store=ExcelBlackboardStore(tmp_path / "blackboard.xlsx"),
        documents=[SourceDocument(tmp_path / "case.md", "case text")],
        draft_tables=draft_tables,
        writer_agent="coordinator_agent",
        tool_state=tool_state,
    )
    write_tool = next(tool for tool in tools if tool.__name__ == "write_blackboard_table")

    for index in range(COORDINATOR_WRITE_BLACKBOARD_LIMIT):
        result = json.loads(
            write_tool(
                "event_log",
                json.dumps(
                    [
                        {
                            **minimal_event_rows()[0],
                            "event_id": f"EVT-LIMIT-{index}",
                        }
                    ]
                ),
            )
        )
        assert result["coordinator_write_blackboard_table_calls"] == index + 1

    final_result = write_tool("event_log", json.dumps(minimal_event_rows()))

    assert final_result.startswith("FINAL_SCHEDULE_READY")
    assert tool_state["coordinator_write_limit_reached"] is True


def test_agentchat_persists_latest_draft_snapshot_before_validation_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    documents = [SourceDocument(tmp_path / "case.md", "case text")]

    class FakeMessage:
        source = "wbs_planner_agent"
        content = "Status: wbs_tasks_final draft complete"

    class FakeResult:
        messages = [FakeMessage()]
        stop_reason = "Maximum number of turns reached."

    class FakeTeam:
        def __init__(self, draft_tables):
            self.draft_tables = draft_tables

        async def run_stream(self, task: str):
            self.draft_tables["wbs_tasks_final"] = minimal_wbs_rows()
            yield FakeResult()

    def fake_build_selector_team(**kwargs):
        return FakeTeam(kwargs["draft_tables"]), []

    monkeypatch.setattr("agentchat_runtime.workflow.build_selector_team", fake_build_selector_team)

    result = AgentChatWorkflow(
        store=store,
        model_settings={"provider": "openai_compatible", "model": "fake", "api_key": "fake"},
        documents=documents,
    ).run()

    assert result.final_content.startswith("FINAL_SCHEDULE_READY")
    assert len(store.read_rows("wbs_tasks_final")) == 3
    assert any(
        row["sender"] == "wbs_planner_agent" and row["event_type"] == "agentchat.transcript"
        for row in store.read_rows("agent_message_log")
    )
    assert store.read_rows("debug_records")


def test_final_payload_tool_result_ends_workflow_without_chat_restating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    documents = [SourceDocument(tmp_path / "case.md", "项目资料：开工日期 2026-03-01")]
    calls = {"count": 0}

    class FakeToolEvent:
        source = "build_final_payload_from_drafts"
        content = "FINAL_SCHEDULE_READY " + json.dumps(_payload(), ensure_ascii=False)

    class FakeTeam:
        async def run_stream(self, task: str):
            calls["count"] += 1
            yield FakeToolEvent()

    def fake_build_selector_team(**kwargs):
        return FakeTeam(), []

    monkeypatch.setattr("agentchat_runtime.workflow.build_selector_team", fake_build_selector_team)

    result = AgentChatWorkflow(
        store=store,
        model_settings={"provider": "openai_compatible", "model": "fake", "api_key": "fake"},
        documents=documents,
    ).run()

    assert calls["count"] == 1
    assert result.stop_reason == "final_payload_tool_result"
    assert result.final_content.startswith("FINAL_SCHEDULE_READY")
    assert len(store.read_rows("schedule_initial")) == 3


def test_missing_final_marker_retry_routes_to_coordinator_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    documents = [SourceDocument(tmp_path / "case.md", "椤圭洰璧勬枡锛氬紑宸ユ棩鏈?2026-03-01")]
    seen_revision_modes: list[bool] = []

    class FakeMessage:
        def __init__(self, source: str, content: str) -> None:
            self.source = source
            self.content = content

    class FakeResult:
        def __init__(self, messages: list[FakeMessage], stop_reason: str) -> None:
            self.messages = messages
            self.stop_reason = stop_reason

    class FakeTeam:
        def __init__(self, result: FakeResult) -> None:
            self.result = result

        async def run_stream(self, task: str):
            if seen_revision_modes and seen_revision_modes[-1]:
                assert "coordinator_agent finalization failure" in task
                assert "not a WBS rewrite request" in task
            yield self.result

    results = [
        FakeResult(
            [
                FakeMessage(
                    "wbs_planner_agent",
                    "Status: wbs_tasks_final draft complete\nNext agent hint: coordinator_agent should finalize.",
                )
            ],
            "Maximum number of turns reached.",
        ),
        FakeResult(
            [
                FakeMessage(
                    "coordinator_agent",
                    "FINAL_SCHEDULE_READY " + json.dumps(_payload(), ensure_ascii=False),
                )
            ],
            "fake",
        ),
    ]

    def fake_build_selector_team(**kwargs):
        seen_revision_modes.append(bool(kwargs["revision_mode"]))
        return FakeTeam(results.pop(0)), []

    monkeypatch.setattr("agentchat_runtime.workflow.build_selector_team", fake_build_selector_team)

    AgentChatWorkflow(
        store=store,
        model_settings={"provider": "openai_compatible", "model": "fake", "api_key": "fake"},
        documents=documents,
    ).run()

    assert seen_revision_modes == [False, True]
    assert any(row["sender"] == "wbs_planner_agent" for row in store.read_rows("agent_message_log"))


def test_missing_final_marker_task_names_coordinator_problem(tmp_path: Path) -> None:
    documents = [SourceDocument(tmp_path / "case.md", "case text")]
    task = build_missing_final_marker_task(
        documents=documents,
        validation_error="Final coordinator message did not include FINAL_SCHEDULE_READY.",
        attempt=2,
        draft_tables={
            "parameter_checklist": [{}],
            "project_parameters": [{}],
            "wbs_tasks_final": [{}, {}],
            "resource_plan_final": [{}],
            "event_log": [{}],
            "adjustment_plan": [{}],
        },
        previous_output="Status: wbs_tasks_final draft complete",
        stop_reason="Maximum number of turns reached.",
    )

    assert "coordinator_agent finalization failure" in task
    assert "not a WBS rewrite request" in task
    assert "wbs_tasks_final=2" in task
    assert "Maximum number of turns reached." in task
    assert "FINAL_SCHEDULE_READY" not in task


def test_responsible_repair_task_names_owner_problem_and_direction(tmp_path: Path) -> None:
    documents = [SourceDocument(tmp_path / "case.md", "case text")]
    draft_tables = {
        "parameter_checklist": minimal_parameter_checklist(),
        "project_parameters": _payload()["tables"]["project_parameters"],  # type: ignore[index]
        "wbs_tasks_final": minimal_wbs_rows(),
        "resource_plan_final": minimal_resource_rows(),
        "event_log": minimal_event_rows(),
        "adjustment_plan": minimal_adjustment_rows(),
    }
    draft_tables["wbs_tasks_final"][1]["predecessor_ids"] = "TASK-NOT-FOUND"  # type: ignore[index]

    task = build_responsible_agent_repair_task(
        documents=documents,
        validation_error="wbs_tasks_final predecessor/CPM validation failed: missing TASK-NOT-FOUND",
        attempt=2,
        draft_tables=draft_tables,
        previous_final="FINAL_SCHEDULE_READY {\"tables\": {}}",
    )

    assert "Specific problem(s)" in task
    assert "Modification direction" in task
    assert "Likely responsible agent: wbs_planner_agent" in task
    assert "Likely affected sheet(s): wbs_tasks_final" in task
    assert "If the responsible agent does not modify the issue" in task


def test_responsible_repair_candidate_routes_owner_then_coordinator() -> None:
    draft_tables = {
        "parameter_checklist": minimal_parameter_checklist(),
        "project_parameters": _payload()["tables"]["project_parameters"],  # type: ignore[index]
        "wbs_tasks_final": minimal_wbs_rows(),
        "resource_plan_final": minimal_resource_rows(),
        "event_log": minimal_event_rows(),
        "adjustment_plan": minimal_adjustment_rows(),
    }
    draft_tables["wbs_tasks_final"][1]["predecessor_ids"] = "TASK-NOT-FOUND"  # type: ignore[index]
    candidate = _build_responsible_repair_candidate_func(draft_tables)

    class Message:
        source = "coordinator_agent"

    class OwnerMessage:
        source = "wbs_planner_agent"

    assert candidate([Message()]) == ["wbs_planner_agent"]
    assert candidate([OwnerMessage()]) == ["coordinator_agent"]


def test_coordinator_takeover_task_grants_direct_edit_authority(tmp_path: Path) -> None:
    documents = [SourceDocument(tmp_path / "case.md", "case text")]
    task = build_coordinator_takeover_task(
        documents=documents,
        validation_error="resource_plan_final references unknown task_id TASK-X",
        attempt=3,
        draft_tables={
            "parameter_checklist": minimal_parameter_checklist(),
            "project_parameters": _payload()["tables"]["project_parameters"],  # type: ignore[index]
            "wbs_tasks_final": minimal_wbs_rows(),
            "resource_plan_final": minimal_resource_rows(),
            "event_log": minimal_event_rows(),
            "adjustment_plan": minimal_adjustment_rows(),
        },
        previous_final="FINAL_SCHEDULE_READY {\"tables\": {}}",
    )

    assert "coordinator_agent is now authorized" in task
    assert "directly modify the affected draft rows" in task
    assert "persist the best available" in task


def test_agentchat_output_rejects_bad_predecessor(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    payload = _payload()
    payload["tables"]["wbs_tasks_final"][1]["predecessor_ids"] = "TASK-NOT-FOUND"  # type: ignore[index]

    with pytest.raises(AgentOutputValidationError):
        write_agentchat_output(store, payload)


def test_agentchat_output_accepts_legacy_predecessor_field(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    payload = _payload()
    legacy_row = dict(payload["tables"]["wbs_tasks_final"][1])  # type: ignore[index]
    legacy_row.pop("predecessor_ids", None)
    legacy_row["predecessor"] = "TASK-0001"
    payload["tables"]["wbs_tasks_final"][1] = legacy_row  # type: ignore[index]

    counts = write_agentchat_output(store, payload)

    assert counts["wbs_tasks_final"] == 3
    assert store.read_rows("wbs_tasks_final")[1]["predecessor_ids"] == "TASK-0001"


def test_live_model_settings_reject_mock() -> None:
    with pytest.raises(ModelConfigurationError):
        validate_live_model_settings({"provider": "mock", "model": "fake", "api_key": "fake"})


def test_model_settings_prefer_moonshot_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "MODEL_PROVIDER=openai_compatible",
                "MOONSHOT_" + "API_KEY=moonshot-key",
                "OPENAI_" + "API_KEY=openai-key",
                "OPENAI_MODEL=kimi-k2.6",
                "OPENAI_BASE_URL=https://api.moonshot.cn/v1",
                "MODEL_API_STYLE=chat_completions",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    settings = build_model_settings(tmp_path)

    assert settings["api_key"] == "moonshot-key"
    assert settings["model"] == "kimi-k2.6"
    assert settings["base_url"] == "https://api.moonshot.cn/v1"
    assert settings["temperature"] is None


@pytest.mark.parametrize(
    ("vision", "thinking"),
    [(True, "disabled"), (False, "enabled")],
)
def test_kimi_client_uses_role_specific_capabilities(vision: bool, thinking: str) -> None:
    client = build_openai_chat_completion_client(
        {
            "provider": "openai_compatible",
            "model": "kimi-k2.6",
            "api_key": "fake",
            "base_url": "https://api.moonshot.cn/v1",
        },
        vision=vision,
        thinking=thinking,
    )

    try:
        assert client.model_info["vision"] is vision
        assert client._create_args["extra_body"] == {"thinking": {"type": thinking}}  # type: ignore[attr-defined]
        assert "temperature" not in client._create_args  # type: ignore[attr-defined]
        assert client._include_name_in_message is False  # type: ignore[attr-defined]
    finally:
        close = client.close()
        if hasattr(close, "close"):
            close.close()


def test_kimi_client_normalizes_empty_assistant_content() -> None:
    from autogen_core.models import AssistantMessage, SystemMessage, UserMessage

    client = build_openai_chat_completion_client(
        {
            "provider": "openai_compatible",
            "model": "kimi-k2.6",
            "api_key": "fake",
            "base_url": "https://api.moonshot.cn/v1",
        }
    )

    try:
        create_params = client._process_create_args(  # type: ignore[attr-defined]
            [
                SystemMessage(content="system", source="system"),
                UserMessage(content="hello", source="user"),
                AssistantMessage(content="", source="assistant"),
            ],
            tools=[],
            tool_choice="auto",
            json_output=None,
            extra_create_args={},
        )
        assert create_params.messages[-1]["role"] == "assistant"
        assert create_params.messages[-1]["content"] == " "
    finally:
        close = client.close()
        if hasattr(close, "close"):
            close.close()


def test_revision_candidate_routes_directly_to_coordinator() -> None:
    assert _revision_candidate_func([]) == ["coordinator_agent"]


def test_candidate_func_routes_back_to_wbs_planner_when_wbs_is_broken() -> None:
    draft_tables = {
        "parameter_checklist": minimal_parameter_checklist(),
        "project_parameters": _payload()["tables"]["project_parameters"],  # type: ignore[index]
        "wbs_tasks_final": minimal_wbs_rows(),
        "resource_plan_final": minimal_resource_rows(),
        "event_log": minimal_event_rows(),
        "adjustment_plan": minimal_adjustment_rows(),
    }
    draft_tables["wbs_tasks_final"][1]["predecessor_ids"] = ""  # type: ignore[index]
    draft_tables["wbs_tasks_final"][2]["predecessor_ids"] = ""  # type: ignore[index]

    candidate = _build_candidate_func(draft_tables)

    assert candidate([]) == ["wbs_planner_agent"]


def test_runtime_error_text_is_treated_as_rate_limit() -> None:
    exc = RuntimeError("RateLimitError: Error code: 429 - 您的账户已达到速率限制，请您控制请求频率")

    assert _exception_indicates_rate_limit(exc)
    assert "HTTP 429" in _runtime_failure_message(exc, attempt=1)
    assert "Wait before rerunning" in _runtime_fix_action(exc)


def test_team_run_timeout_has_safe_floor() -> None:
    assert _team_run_timeout_seconds({"team_run_timeout_seconds": 10}) == 60.0
    assert _team_run_timeout_seconds({"team_run_timeout_seconds": 120}) == 120.0
