"""Data parser Agent for parameter checklist analysis."""

from __future__ import annotations

from typing import Any

from agents.base_agent import AgentResult, BaseAgent
from communication.event_topics import PARAMETER_CHECK_REQUESTED, PARAMETER_MISSING_DETECTED
from communication.message_schema import AgentMessage
from tools.document_tools import SourceDocument, build_document_evidence_rows, read_source_documents
from tools.parameter_extraction import (
    build_assumption_rows_from_facts,
    build_parameter_audit_rows,
    extract_facts_from_evidence,
    extract_parameter_checklist,
)
from tools.parameter_tools import (
    build_project_parameter_rows,
    classify_parameter_rows,
)


class DataParserAgent(BaseAgent):
    """Read checklist parameters and write usable project parameters."""

    def __init__(self, *, store) -> None:
        super().__init__(
            name="data_parser_agent",
            display_name="资料解析Agent",
            role="读取参数清单、识别缺失参数、输出项目参数候选",
            store=store,
        )

    def run(self) -> dict[str, Any]:
        """Execute the data parsing workflow and return a serializable result."""

        return self._run().to_dict()

    def run_from_documents(
        self,
        *,
        input_dir: str | None = None,
        documents: list[SourceDocument] | None = None,
        dwg_conversion_dir: str | None = None,
        install_oda_if_missing: bool = False,
        dwg_timeout_seconds: int = 120,
        model_settings: dict[str, Any] | None = None,
        use_model: bool = True,
    ) -> dict[str, Any]:
        """Read real project documents and populate the parameter checklist."""

        source_documents = (
            documents
            if documents is not None
            else read_source_documents(
                input_dir or "",
                dwg_conversion_dir=dwg_conversion_dir,
                install_oda_if_missing=install_oda_if_missing,
                dwg_timeout_seconds=dwg_timeout_seconds,
            )
        )
        return self._run_from_documents(
            source_documents,
            model_settings=model_settings,
            use_model=use_model,
        ).to_dict()

    def handle_message(self, message: AgentMessage) -> AgentResult:
        """Run parameter parsing when the router requests a checklist check."""

        if message.event_type == PARAMETER_CHECK_REQUESTED or message.mode == "direct":
            result = self._run()
            result.messages.insert(0, message.message_id)
            return result
        return super().handle_message(message)

    def _run_from_documents(
        self,
        documents: list[SourceDocument],
        *,
        model_settings: dict[str, Any] | None,
        use_model: bool,
    ) -> AgentResult:
        if not documents:
            return self.result(
                status="failed",
                summary="未发现可解析资料；生产运行必须通过 AutoGen AgentChat 读取真实资料",
                written_sheets=[],
                needs_human_confirmation=True,
                warnings=["禁止回退到现有参数清单或默认清单生成生产成果"],
            )

        checklist_rows, extraction_warnings = extract_parameter_checklist(
            documents,
            model_settings=model_settings,
            use_model=use_model,
        )
        if checklist_rows:
            self.store.replace_rows("parameter_checklist", checklist_rows)

        result = self._run()
        section_rows, table_rows = build_document_evidence_rows(documents)
        fact_rows = extract_facts_from_evidence(section_rows, table_rows)
        if section_rows:
            self.store.replace_rows("document_sections", section_rows)
        if table_rows:
            self.store.replace_rows("document_tables", table_rows)
        if fact_rows:
            self.store.replace_rows("extracted_facts", fact_rows)
        project_rows = self.store.read_rows("project_parameters")
        audit_rows = build_parameter_audit_rows(
            facts=fact_rows,
            parameter_checklist=checklist_rows,
            project_parameters=project_rows,
        )
        assumption_rows = build_assumption_rows_from_facts(fact_rows)
        self.store.replace_rows("parameter_audit", audit_rows)
        self.store.replace_rows("assumption_register", assumption_rows)
        result.written_sheets = _dedupe(
            [
                "parameter_checklist",
                "document_sections",
                "document_tables",
                "extracted_facts",
                "parameter_audit",
                "assumption_register",
                *result.written_sheets,
            ]
        )
        result.summary = (
            f"已读取 {len(documents)} 个资料文件，自动生成 {len(checklist_rows)} 条参数检查清单；"
            + result.summary
        )
        result.needs_human_confirmation = result.needs_human_confirmation or bool(
            extraction_warnings
        )
        result.warnings.extend(extraction_warnings)
        message_id = self.log_message(
            receiver="coordinator_agent,wbs_planner_agent,resource_allocator_agent",
            mode="broadcast",
            event_type="source.documents.parsed",
            priority="medium",
            summary=(
                f"资料解析Agent已从 {len(documents)} 个文件生成参数检查清单，"
                f"可进入项目参数解析和后续计划生成"
            ),
            related_sheet="parameter_checklist",
        )
        result.messages.append(message_id)
        return result

    def _run(self) -> AgentResult:
        checklist = self.store.read_rows("parameter_checklist")
        if not checklist:
            return self.result(
                status="failed",
                summary="未发现参数清单；生产运行必须先通过真实资料或 AgentChat 生成参数清单",
                written_sheets=[],
                needs_human_confirmation=True,
                warnings=["禁止回退到默认参数清单或样例数据"],
            )

        classified = classify_parameter_rows(checklist)
        project_parameter_rows = build_project_parameter_rows(checklist)
        self.store.replace_rows("project_parameters", project_parameter_rows)

        messages = []
        if classified["missing"]:
            missing_names = "、".join(str(row.get("name")) for row in classified["missing"])
            messages.append(
                self.log_message(
                    receiver="ALL",
                    mode="broadcast",
                    event_type=PARAMETER_MISSING_DETECTED,
                    priority="high",
                    summary=f"发现必需参数缺失：{missing_names}",
                    related_sheet="parameter_checklist",
                )
            )

        warnings = [f"缺失参数：{row.get('name')}" for row in classified["missing"]] + [
            f"需人工确认：{row.get('name')}" for row in classified["needs_confirmation"]
        ]
        return self.result(
            status="success",
            summary=(
                f"已解析 {len(checklist)} 项参数，写入 {len(project_parameter_rows)} 项可用参数，"
                f"发现 {len(classified['missing'])} 项缺失参数"
            ),
            written_sheets=["parameter_checklist", "project_parameters"],
            messages=messages,
            needs_human_confirmation=bool(
                classified["missing"] or classified["needs_confirmation"]
            ),
            warnings=warnings,
        )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
