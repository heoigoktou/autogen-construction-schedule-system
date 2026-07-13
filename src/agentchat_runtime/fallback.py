"""Deterministic fallback payloads for stalled AgentChat runs."""

from __future__ import annotations

import re
from typing import Any

from tools.document_tools import SourceDocument, build_document_evidence_rows, concatenate_documents
from tools.parameter_extraction import (
    build_assumption_rows_from_facts,
    build_parameter_audit_rows,
    extract_facts_from_evidence,
    extract_parameter_checklist_by_rules,
)
from tools.parameter_tools import build_project_parameter_rows, now_iso


def build_runtime_fallback_payload(
    *,
    documents: list[SourceDocument],
    draft_tables: dict[str, list[dict[str, Any]]],
    problem: str,
) -> dict[str, Any]:
    """Build a minimal, editable schedule payload when model orchestration stalls.

    The payload is intentionally conservative. It keeps any successful draft rows,
    fills missing core tables with deterministic rows, and marks the result as a
    fallback that needs human review instead of pretending to be final engineering
    judgment.
    """

    source_names = ", ".join(document.name for document in documents) or "uploaded sources"
    source_text = concatenate_documents(documents, max_chars=16000)
    section_rows, table_rows = build_document_evidence_rows(documents)
    facts = extract_facts_from_evidence(section_rows, table_rows)

    parameter_checklist = draft_tables.get("parameter_checklist") or extract_parameter_checklist_by_rules(
        source_text,
        source_names=source_names,
    )
    project_parameters = draft_tables.get("project_parameters") or build_project_parameter_rows(
        parameter_checklist
    )
    wbs_rows = draft_tables.get("wbs_tasks_final") or _fallback_wbs_rows(
        total_days=_total_days(project_parameters),
        source_names=source_names,
    )
    resource_rows = draft_tables.get("resource_plan_final") or _fallback_resource_rows(wbs_rows)
    event_rows = draft_tables.get("event_log") or [_fallback_event_row(problem)]
    adjustment_rows = draft_tables.get("adjustment_plan") or [_fallback_adjustment_row()]
    assumption_rows = draft_tables.get("assumption_register") or build_assumption_rows_from_facts(facts)

    return {
        "tables": {
            "parameter_checklist": parameter_checklist,
            "project_parameters": project_parameters,
            "wbs_tasks_final": wbs_rows,
            "resource_plan_final": _repair_resource_rows(resource_rows, wbs_rows),
            "event_log": event_rows,
            "adjustment_plan": adjustment_rows,
            "document_sections": draft_tables.get("document_sections") or section_rows,
            "document_tables": draft_tables.get("document_tables") or table_rows,
            "extracted_facts": draft_tables.get("extracted_facts") or facts,
            "parameter_audit": draft_tables.get("parameter_audit")
            or build_parameter_audit_rows(
                facts=facts,
                parameter_checklist=parameter_checklist,
                project_parameters=project_parameters,
            ),
            "assumption_register": assumption_rows,
        }
    }


def _fallback_wbs_rows(*, total_days: int, source_names: str) -> list[dict[str, Any]]:
    stages = [
        ("01.01.001", "施工准备", "准备阶段"),
        ("01.02.001", "测量放线", "准备阶段"),
        ("01.03.001", "临建与场地布置", "准备阶段"),
        ("02.01.001", "土方与场地处理", "基础阶段"),
        ("02.02.001", "地基与基础施工", "基础阶段"),
        ("02.03.001", "基础结构验收", "基础阶段"),
        ("03.01.001", "主体结构施工", "主体阶段"),
        ("03.02.001", "砌体或围护结构施工", "主体阶段"),
        ("03.03.001", "屋面工程", "主体阶段"),
        ("04.01.001", "外立面工程", "装饰阶段"),
        ("04.02.001", "给排水预留预埋", "机电阶段"),
        ("04.03.001", "电气预留预埋", "机电阶段"),
        ("04.04.001", "消防系统安装", "机电阶段"),
        ("04.05.001", "暖通与综合机电安装", "机电阶段"),
        ("05.01.001", "室内装饰装修", "装饰阶段"),
        ("05.02.001", "地坪或楼地面工程", "装饰阶段"),
        ("06.01.001", "室外管网与道路", "室外阶段"),
        ("06.02.001", "系统调试与联动试运行", "调试阶段"),
        ("07.01.001", "专项验收与整改", "验收阶段"),
        ("07.02.001", "竣工验收与移交", "验收阶段"),
    ]
    durations = _split_duration(total_days, len(stages))
    rows: list[dict[str, Any]] = []
    for index, ((wbs_code, task_name, phase), duration) in enumerate(zip(stages, durations), start=1):
        task_id = f"TASK-{index:04d}"
        predecessor = f"TASK-{index - 1:04d}" if index > 1 else ""
        rows.append(
            {
                "task_id": task_id,
                "wbs_code": wbs_code,
                "phase": phase,
                "section": "全项目",
                "floor_or_area": "",
                "task_name": task_name,
                "work_package": task_name,
                "quantity": "",
                "unit": "",
                "duration_days": duration,
                "predecessor_ids": predecessor,
                "relation_type": "FS",
                "lag_days": 0,
                "source": "rules_fallback+source_context",
                "confidence": "0.45",
                "note": (
                    "AgentChat runtime stalled; deterministic fallback WBS generated from "
                    f"{source_names}. Review and edit before formal use."
                ),
                "owner_agent": "fallback_scheduler",
            }
        )
    return rows


def _fallback_resource_rows(wbs_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anchors = [
        (4, "equipment", "土方机械", 1, "台班", 1, "基础阶段"),
        (5, "labor", "基础施工班组", 1, "班组", 1, "基础阶段"),
        (7, "labor", "主体结构班组", 1, "班组", 1, "主体阶段"),
        (13, "labor", "消防安装班组", 1, "班组", 1, "机电阶段"),
        (15, "labor", "装饰装修班组", 1, "班组", 1, "装饰阶段"),
    ]
    task_ids = [str(row.get("task_id") or "") for row in wbs_rows]
    rows = []
    for index, resource_type, resource_name, demand, unit, capacity, period in anchors:
        task_id = task_ids[min(index - 1, len(task_ids) - 1)] if task_ids else "TASK-0001"
        rows.append(
            {
                "task_id": task_id,
                "resource_type": resource_type,
                "resource_name": resource_name,
                "demand": demand,
                "unit": unit,
                "capacity": capacity,
                "period": period,
                "conflict_flag": False,
                "source": "rules_fallback+source_context",
                "confidence": "0.40",
                "note": "Fallback placeholder resource. Replace with project-specific demand/capacity.",
                "owner_agent": "fallback_scheduler",
            }
        )
    return rows


def _fallback_event_row(problem: str) -> dict[str, Any]:
    return {
        "event_id": "EVT-FALLBACK-0001",
        "event_type": "runtime_fallback",
        "related_task": "",
        "impact_days": 0,
        "priority": "low",
        "status": "generated",
        "created_at": now_iso(),
        "created_by": "fallback_scheduler",
        "source": "agentchat_runtime",
        "confidence": "0.40",
        "note": f"AgentChat did not complete normally: {problem[:500]}",
    }


def _fallback_adjustment_row() -> dict[str, Any]:
    return {
        "plan_id": "ADJ-FALLBACK-0001",
        "event_id": "EVT-FALLBACK-0001",
        "measure": "No model-generated adjustment was available; review WBS/resources manually and recalculate.",
        "recovered_days": 0,
        "cost_level": "待确认",
        "risk_level": "medium",
        "score": 0,
        "selected_flag": False,
        "source": "rules_fallback+source_context",
        "confidence": "0.40",
        "note": "Fallback row added to keep adjustment workflow editable.",
        "created_by": "fallback_scheduler",
        "created_at": now_iso(),
    }


def _repair_resource_rows(
    resource_rows: list[dict[str, Any]],
    wbs_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    task_ids = {str(row.get("task_id") or "") for row in wbs_rows}
    fallback_task = next(iter(task_ids), "TASK-0001")
    output = []
    for row in resource_rows:
        repaired = dict(row)
        if str(repaired.get("task_id") or "") not in task_ids:
            repaired["task_id"] = fallback_task
        if repaired.get("demand") in {None, ""}:
            repaired["demand"] = 1
            repaired["note"] = f"{repaired.get('note') or ''}; demand fallback=1".strip("; ")
        if repaired.get("capacity") in {None, ""}:
            repaired["capacity"] = repaired.get("demand") or 1
            repaired["note"] = f"{repaired.get('note') or ''}; capacity fallback=demand".strip("; ")
        repaired.setdefault("source", "rules_fallback+source_context")
        repaired.setdefault("confidence", "0.40")
        repaired.setdefault("owner_agent", "fallback_scheduler")
        output.append(repaired)
    return output


def _split_duration(total_days: int, count: int) -> list[int]:
    total = max(total_days, count)
    base = max(total // count, 1)
    durations = [base for _ in range(count)]
    remaining = total - base * count
    index = 0
    while remaining > 0:
        durations[index % count] += 1
        remaining -= 1
        index += 1
    return durations


def _total_days(project_parameters: list[dict[str, Any]]) -> int:
    for row in project_parameters:
        if str(row.get("parameter_id") or "") != "P-001":
            continue
        match = re.search(r"\d+", str(row.get("value") or ""))
        if match:
            return max(int(match.group(0)), 20)
    return 120
