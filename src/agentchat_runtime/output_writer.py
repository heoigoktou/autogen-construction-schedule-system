"""Parse, validate, enrich, and persist AgentChat-produced schedule data."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from agentchat_runtime.exceptions import AgentOutputValidationError
from blackboard.excel_store import ExcelBlackboardStore
from blackboard.sheet_schema import get_sheet_spec
from blackboard.validators import BlackboardValidationError, validate_row
from communication.message_schema import now_iso
from tools.cpm_tools import calculate_cpm
from tools.constraint_tools import check_constraints
from tools.document_tools import SourceDocument, build_document_evidence_rows, has_mojibake
from tools.parameter_extraction import (
    KEY_PARAMETER_IDS,
    build_assumption_rows_from_facts,
    build_parameter_audit_rows,
    extract_facts_from_evidence,
)
from tools.parameter_tools import (
    STATUS_CONFLICT,
    STATUS_DERIVED,
    STATUS_INFERRED,
    STATUS_MISSING,
    STATUS_SOURCE_EXACT,
    STATUS_SOURCE_TABLE,
)
from tools.resource_tools import build_resource_load, build_resource_resolution
from tools.schedule_tools import build_initial_schedule, build_milestone_checks
from tools.wbs_expansion import maybe_expand_segmented_wbs

CORE_TABLES = (
    "parameter_checklist",
    "project_parameters",
    "wbs_tasks_final",
    "resource_plan_final",
    "event_log",
    "adjustment_plan",
)

INCREMENTAL_TABLES = (
    "document_sections",
    "document_tables",
    "extracted_facts",
    "parameter_audit",
    "assumption_register",
    "quality_gates",
)

ALL_INPUT_TABLES = CORE_TABLES + INCREMENTAL_TABLES


def parse_agentchat_json(content: str) -> dict[str, Any]:
    """Extract a JSON object from an AgentChat final message."""

    cleaned = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AgentOutputValidationError(f"AgentChat final output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AgentOutputValidationError("AgentChat final output must be a JSON object.")
    return parsed


def write_agentchat_output(
    store: ExcelBlackboardStore,
    payload: dict[str, Any],
    *,
    start_date: date | None = None,
    source_documents: list[SourceDocument] | None = None,
) -> dict[str, Any]:
    """Validate model output, write blackboard tables, and calculate schedule tables."""

    normalized = normalize_payload(payload)
    _enrich_with_source_evidence(normalized, source_documents or [])
    _ensure_reference_schedule_dates(normalized)
    _ensure_adjustment_plan_row(normalized)
    normalized["wbs_tasks_final"], expanded_wbs = maybe_expand_segmented_wbs(
        normalized["wbs_tasks_final"],
        normalized["project_parameters"],
        normalized["assumption_register"],
    )
    if expanded_wbs:
        _repair_resource_task_refs(normalized)
        _apply_resource_fact_overrides(normalized, normalized.get("extracted_facts") or [])
    quality_rows, fatal_gate_errors = build_quality_gate_rows(normalized)
    normalized["quality_gates"] = quality_rows
    validate_normalized_payload(normalized, extra_errors=fatal_gate_errors)

    schedule_rows, cpm_rows, edge_rows = build_initial_schedule(
        normalized["wbs_tasks_final"],
        normalized["resource_plan_final"],
        start_date=start_date
        or _required_start_date_from_parameters(
            normalized["project_parameters"],
            normalized["assumption_register"],
        ),
    )
    milestone_rows = build_milestone_checks(
        schedule_rows,
        project_parameters=normalized["project_parameters"],
    )
    normalized["parameter_audit"] = [
        *normalized.get("parameter_audit", []),
        *_build_schedule_target_audit_rows(
            schedule_rows,
            normalized["project_parameters"],
            start_index=len(normalized.get("parameter_audit", [])) + 1,
        ),
    ]
    resource_load_rows = build_resource_load(normalized["resource_plan_final"])
    resource_resolution_rows = build_resource_resolution(resource_load_rows)
    constraint_rows = check_constraints(
        normalized["wbs_tasks_final"],
        normalized["resource_plan_final"],
    )

    store.replace_sheets_rows(
        {
            "parameter_checklist": normalized["parameter_checklist"],
            "project_parameters": normalized["project_parameters"],
            "wbs_tasks_final": normalized["wbs_tasks_final"],
            "resource_plan_final": normalized["resource_plan_final"],
            "event_log": normalized.get("event_log") or [],
            "adjustment_plan": normalized.get("adjustment_plan") or [],
            "document_sections": normalized.get("document_sections") or [],
            "document_tables": normalized.get("document_tables") or [],
            "extracted_facts": normalized.get("extracted_facts") or [],
            "parameter_audit": normalized.get("parameter_audit") or [],
            "assumption_register": normalized.get("assumption_register") or [],
            "quality_gates": normalized.get("quality_gates") or [],
            "resource_load_daily": resource_load_rows,
            "resource_resolution": resource_resolution_rows,
            "schedule_initial": schedule_rows,
            "cpm_analysis": cpm_rows,
            "network_edges": edge_rows,
            "milestone_check": milestone_rows,
            "constraint_check": constraint_rows,
        }
    )
    counts = {
        sheet_name: len(normalized.get(sheet_name) or [])
        for sheet_name in ALL_INPUT_TABLES
    }
    counts.update(
        {
            "resource_load_daily": len(resource_load_rows),
            "resource_resolution": len(resource_resolution_rows),
            "schedule_initial": len(schedule_rows),
            "cpm_analysis": len(cpm_rows),
            "network_edges": len(edge_rows),
            "milestone_check": len(milestone_rows),
            "constraint_check": len(constraint_rows),
        }
    )
    return counts


def force_write_agentchat_output(
    store: ExcelBlackboardStore,
    payload: dict[str, Any],
    *,
    start_date: date | None = None,
    source_documents: list[SourceDocument] | None = None,
    validation_error: str | None = None,
) -> dict[str, Any]:
    """Persist the best-effort AgentChat payload even if validation still fails."""

    normalized = normalize_payload(payload)
    _enrich_with_source_evidence(normalized, source_documents or [])
    _ensure_reference_schedule_dates(normalized)
    _ensure_adjustment_plan_row(normalized)
    normalized["wbs_tasks_final"], expanded_wbs = maybe_expand_segmented_wbs(
        normalized["wbs_tasks_final"],
        normalized["project_parameters"],
        normalized["assumption_register"],
    )
    if expanded_wbs:
        _repair_resource_task_refs(normalized)
        _apply_resource_fact_overrides(normalized, normalized.get("extracted_facts") or [])
    quality_rows, fatal_gate_errors = build_quality_gate_rows(normalized)
    normalized["quality_gates"] = quality_rows

    validation_errors: list[str] = []
    try:
        validate_normalized_payload(normalized, extra_errors=fatal_gate_errors)
    except AgentOutputValidationError as exc:
        validation_errors = str(exc).splitlines()

    derived_errors: list[str] = []
    schedule_rows: list[dict[str, Any]] = []
    cpm_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []
    milestone_rows: list[dict[str, Any]] = []
    resource_load_rows: list[dict[str, Any]] = []
    resource_resolution_rows: list[dict[str, Any]] = []
    constraint_rows: list[dict[str, Any]] = []

    derived_start_date = start_date or _best_effort_start_date(
        normalized["project_parameters"],
        normalized["assumption_register"],
    )
    if derived_start_date is None:
        derived_errors.append("schedule generation skipped because project start date could not be resolved.")
    else:
        try:
            schedule_rows, cpm_rows, edge_rows = build_initial_schedule(
                normalized["wbs_tasks_final"],
                normalized["resource_plan_final"],
                start_date=derived_start_date,
            )
            milestone_rows = build_milestone_checks(
                schedule_rows,
                project_parameters=normalized["project_parameters"],
            )
            normalized["parameter_audit"] = [
                *normalized.get("parameter_audit", []),
                *_build_schedule_target_audit_rows(
                    schedule_rows,
                    normalized["project_parameters"],
                    start_index=len(normalized.get("parameter_audit", [])) + 1,
                ),
            ]
        except Exception as exc:
            derived_errors.append(f"schedule generation failed: {exc}")
            schedule_rows = []
            cpm_rows = []
            edge_rows = []
            milestone_rows = []

    try:
        resource_load_rows = build_resource_load(normalized["resource_plan_final"])
        resource_resolution_rows = build_resource_resolution(resource_load_rows)
    except Exception as exc:
        derived_errors.append(f"resource aggregation failed: {exc}")

    try:
        constraint_rows = check_constraints(
            normalized["wbs_tasks_final"],
            normalized["resource_plan_final"],
        )
    except Exception as exc:
        derived_errors.append(f"constraint checking failed: {exc}")

    debug_rows = _build_debug_record_rows(
        validation_error=validation_error,
        validation_errors=validation_errors,
        derived_errors=derived_errors,
    )

    store.replace_sheets_rows(
        {
            "parameter_checklist": normalized["parameter_checklist"],
            "project_parameters": normalized["project_parameters"],
            "wbs_tasks_final": normalized["wbs_tasks_final"],
            "resource_plan_final": normalized["resource_plan_final"],
            "event_log": normalized.get("event_log") or [],
            "adjustment_plan": normalized.get("adjustment_plan") or [],
            "document_sections": normalized.get("document_sections") or [],
            "document_tables": normalized.get("document_tables") or [],
            "extracted_facts": normalized.get("extracted_facts") or [],
            "parameter_audit": normalized.get("parameter_audit") or [],
            "assumption_register": normalized.get("assumption_register") or [],
            "quality_gates": normalized.get("quality_gates") or [],
            "resource_load_daily": resource_load_rows,
            "resource_resolution": resource_resolution_rows,
            "schedule_initial": schedule_rows,
            "cpm_analysis": cpm_rows,
            "network_edges": edge_rows,
            "milestone_check": milestone_rows,
            "constraint_check": constraint_rows,
            "debug_records": debug_rows,
        }
    )
    counts = {
        sheet_name: len(normalized.get(sheet_name) or [])
        for sheet_name in ALL_INPUT_TABLES
    }
    counts.update(
        {
            "resource_load_daily": len(resource_load_rows),
            "resource_resolution": len(resource_resolution_rows),
            "schedule_initial": len(schedule_rows),
            "cpm_analysis": len(cpm_rows),
            "network_edges": len(edge_rows),
            "milestone_check": len(milestone_rows),
            "constraint_check": len(constraint_rows),
            "debug_records": len(debug_rows),
        }
    )
    return counts


def normalize_payload(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Normalize optional AgentChat wrapper shapes into blackboard table rows."""

    tables = payload.get("tables") if isinstance(payload.get("tables"), dict) else payload
    normalized: dict[str, list[dict[str, Any]]] = {}
    for sheet_name in ALL_INPUT_TABLES:
        value = tables.get(sheet_name, [])
        if value is None:
            value = []
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise AgentOutputValidationError(f"{sheet_name} must be an array of row objects.")
        normalized[sheet_name] = [_normalize_row(sheet_name, item) for item in value]
    return normalized


def _normalize_row(sheet_name: str, row: dict[str, Any]) -> dict[str, Any]:
    spec = get_sheet_spec(sheet_name)
    output = {header: row.get(header) for header in spec.headers if header in row}
    if sheet_name == "parameter_checklist":
        output.setdefault("created_by", "agentchat_runtime")
        output.setdefault("created_at", now_iso())
        output.setdefault("status", output.get("extraction_status") or STATUS_INFERRED)
        output.setdefault("extraction_status", output.get("status") or STATUS_INFERRED)
    elif sheet_name == "project_parameters":
        output.setdefault("updated_at", now_iso())
        output.setdefault("created_by", "agentchat_runtime")
        output.setdefault("confirmed_by", "agentchat_model")
        output.setdefault("extraction_status", STATUS_INFERRED)
    elif sheet_name == "wbs_tasks_final":
        _copy_legacy_predecessor_field(row, output)
        output.setdefault("source", "model_inference+source_context")
        output.setdefault("confidence", "0.70")
        output.setdefault("note", "AgentChat generated from source context")
        output.setdefault("owner_agent", "wbs_planner_agent")
        output.setdefault("relation_type", "FS")
        output.setdefault("lag_days", 0)
    elif sheet_name == "resource_plan_final":
        output.setdefault("source", "model_inference+source_context")
        output.setdefault("confidence", "0.70")
        output.setdefault("note", "AgentChat generated from WBS and source context")
        output.setdefault("owner_agent", "resource_allocator_agent")
        output.setdefault("conflict_flag", False)
        output["conflict_flag"] = _to_bool(output.get("conflict_flag"))
    elif sheet_name == "event_log":
        output.setdefault("created_at", now_iso())
        output.setdefault("created_by", "dynamic_responder_agent")
        output.setdefault("status", "generated")
        output.setdefault("source", "model_inference+source_context")
        output.setdefault("confidence", "0.65")
        output.setdefault("note", "AgentChat event or risk from source context")
    elif sheet_name == "adjustment_plan":
        output.setdefault("created_at", now_iso())
        output.setdefault("created_by", "plan_arbiter_agent")
        output.setdefault("selected_flag", False)
        output["selected_flag"] = _to_bool(output.get("selected_flag"))
        output.setdefault("source", "model_inference+source_context")
        output.setdefault("confidence", "0.65")
        output.setdefault("note", "AgentChat adjustment candidate")
    return output


def _copy_legacy_predecessor_field(row: dict[str, Any], output: dict[str, Any]) -> None:
    """Map the legacy `predecessor` field to the formal WBS schema."""

    if output.get("predecessor_ids"):
        return
    predecessor = row.get("predecessor")
    if predecessor is not None:
        output["predecessor_ids"] = predecessor


def validate_agentchat_payload(
    payload: dict[str, Any],
    *,
    source_documents: list[SourceDocument] | None = None,
) -> None:
    """Validate an AgentChat final payload without writing to Excel."""

    normalized = normalize_payload(payload)
    _enrich_with_source_evidence(normalized, source_documents or [])
    _ensure_reference_schedule_dates(normalized)
    _ensure_adjustment_plan_row(normalized)
    normalized["wbs_tasks_final"], expanded_wbs = maybe_expand_segmented_wbs(
        normalized["wbs_tasks_final"],
        normalized["project_parameters"],
        normalized["assumption_register"],
    )
    if expanded_wbs:
        _repair_resource_task_refs(normalized)
        _apply_resource_fact_overrides(normalized, normalized.get("extracted_facts") or [])
    quality_rows, fatal_gate_errors = build_quality_gate_rows(normalized)
    normalized["quality_gates"] = quality_rows
    validate_normalized_payload(normalized, extra_errors=fatal_gate_errors)


def validate_normalized_payload(
    payload: dict[str, list[dict[str, Any]]],
    *,
    extra_errors: list[str] | None = None,
) -> None:
    """Validate normalized AgentChat table rows."""

    errors: list[str] = list(extra_errors or [])
    if not payload["parameter_checklist"]:
        errors.append("parameter_checklist cannot be empty.")
    if not payload["project_parameters"]:
        errors.append("project_parameters cannot be empty.")
    if not payload["wbs_tasks_final"]:
        errors.append("wbs_tasks_final cannot be empty.")
    if not payload["resource_plan_final"]:
        errors.append("resource_plan_final cannot be empty.")

    for sheet_name, rows in payload.items():
        for index, row in enumerate(rows, start=1):
            try:
                validate_row(sheet_name, row)
            except BlackboardValidationError as exc:
                errors.append(f"{sheet_name}[{index}] {exc}")

    _validate_wbs(payload["wbs_tasks_final"], errors)
    _validate_resource_rows(payload["resource_plan_final"], payload["wbs_tasks_final"], errors)
    _validate_evidence_fields(payload, errors)
    _required_start_date_from_parameters(payload["project_parameters"], payload["assumption_register"], errors)
    _validate_adjustment_rows(payload.get("adjustment_plan") or [], errors)
    if errors:
        raise AgentOutputValidationError("\n".join(errors))


def _best_effort_start_date(
    project_parameters: list[dict[str, Any]],
    assumption_rows: list[dict[str, Any]] | None = None,
) -> date | None:
    try:
        return _required_start_date_from_parameters(project_parameters, assumption_rows)
    except AgentOutputValidationError:
        return None


def _ensure_reference_schedule_dates(payload: dict[str, list[dict[str, Any]]]) -> None:
    """Add low-confidence schedule dates when source/project rows omit them."""

    project_parameters = payload["project_parameters"]
    assumption_rows = payload["assumption_register"]
    checklist_rows = payload["parameter_checklist"]

    start_row = _find_parameter_row(project_parameters, "P-002")
    start_date = _parse_date_or_year_month(str((start_row or {}).get("value") or ""))
    if start_date is None:
        start_date = _reference_base_date(project_parameters)
        note = "源文档未提供开工日期；为排程计算生成，仅供参考"
        _upsert_project_parameter(
            project_parameters,
            "P-002",
            value=start_date.isoformat(),
            unit="日期",
            note=note,
        )
        _upsert_parameter_checklist(
            checklist_rows,
            "P-002",
            "project_boundary",
            "start_date",
            value=start_date.isoformat(),
            note=note,
        )
        _append_schedule_assumption(
            assumption_rows,
            "P-002",
            note,
            f"reference_base_date={start_date.isoformat()}",
        )

    finish_row = _find_parameter_row(project_parameters, "P-003")
    finish_date = _parse_date_or_year_month(str((finish_row or {}).get("value") or ""))
    if finish_date is None:
        total_days = _parameter_number_text(_parameter_value(project_parameters, "P-001"))
        if total_days is None:
            total_days = _sum_wbs_duration(payload.get("wbs_tasks_final") or []) or 1
        finish_date = start_date + timedelta(days=max(total_days, 1) - 1)
        note = "源文档未提供竣工日期；为排程计算生成，仅供参考"
        _upsert_project_parameter(
            project_parameters,
            "P-003",
            value=finish_date.isoformat(),
            unit="日期",
            note=note,
        )
        _upsert_parameter_checklist(
            checklist_rows,
            "P-003",
            "project_boundary",
            "finish_date",
            value=finish_date.isoformat(),
            note=note,
        )
        _append_schedule_assumption(
            assumption_rows,
            "P-003",
            note,
            f"P-002={start_date.isoformat()}, total_days={total_days}",
        )


def _reference_base_date(project_parameters: list[dict[str, Any]]) -> date:
    return date.today()


def _parse_date_or_year_month(value: str) -> date | None:
    parsed = _parse_date(value)
    if parsed:
        return parsed
    month = _parse_year_month(value)
    if month:
        return date(month[0], month[1], 1)
    return None


def _upsert_project_parameter(
    rows: list[dict[str, Any]],
    parameter_id: str,
    *,
    value: str,
    unit: str,
    note: str,
) -> None:
    row = _find_parameter_row(rows, parameter_id)
    reference_row = {
        "parameter_id": parameter_id,
        "value": value,
        "unit": unit,
        "source": "model_inference_for_scheduling",
        "evidence_id": "",
        "extraction_status": STATUS_INFERRED,
        "confidence": "0.50",
        "confirmed_by": "output_writer",
        "updated_at": now_iso(),
        "created_by": "output_writer",
        "note": note,
    }
    if row is None:
        rows.append(reference_row)
    else:
        row.update(reference_row)


def _upsert_parameter_checklist(
    rows: list[dict[str, Any]],
    parameter_id: str,
    category: str,
    name: str,
    *,
    value: str,
    note: str,
) -> None:
    row = _find_parameter_row(rows, parameter_id)
    checklist_row = {
        "parameter_id": parameter_id,
        "category": category,
        "name": name,
        "required": "yes",
        "value": value,
        "unit": "日期",
        "source": "model_inference_for_scheduling",
        "evidence_id": "",
        "extraction_status": STATUS_INFERRED,
        "confidence": "0.50",
        "status": STATUS_INFERRED,
        "owner_agent": "data_parser_agent",
        "note": note,
        "created_by": "output_writer",
        "created_at": now_iso(),
    }
    if row is None:
        rows.append(checklist_row)
    else:
        row.update(checklist_row)


def _append_schedule_assumption(
    rows: list[dict[str, Any]],
    target_id: str,
    assumption: str,
    basis: str,
) -> None:
    if any(
        str(row.get("target_id") or "") == target_id
        and str(row.get("created_by") or "") == "output_writer"
        for row in rows
    ):
        return
    rows.append(
        {
            "assumption_id": f"ASM-{len(rows) + 1:04d}",
            "target_type": "project_parameter",
            "target_id": target_id,
            "assumption": assumption,
            "basis": basis,
            "risk_level": "medium",
            "status": "active",
            "created_by": "output_writer",
            "created_at": now_iso(),
        }
    )


def _find_parameter_row(rows: list[dict[str, Any]], parameter_id: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("parameter_id") or "") == parameter_id:
            return row
    return None


def _sum_wbs_duration(rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in rows:
        try:
            total += int(float(row.get("duration_days") or 0))
        except (TypeError, ValueError):
            continue
    return total


def _enrich_with_source_evidence(
    payload: dict[str, list[dict[str, Any]]],
    documents: list[SourceDocument],
) -> None:
    if documents:
        section_rows, table_rows = build_document_evidence_rows(documents)
        payload["document_sections"] = section_rows
        payload["document_tables"] = table_rows
    facts = extract_facts_from_evidence(
        payload.get("document_sections") or [],
        payload.get("document_tables") or [],
    )
    correction_audit_rows: list[dict[str, Any]] = []
    if facts:
        correction_audit_rows = _build_auto_correction_audit_rows(payload, facts)
        payload["extracted_facts"] = facts
        _apply_fact_overrides(payload, facts)
        payload["assumption_register"] = _dedupe_rows(
            [*(payload.get("assumption_register") or []), *build_assumption_rows_from_facts(facts)],
            "assumption_id",
        )
    payload["parameter_audit"] = build_parameter_audit_rows(
        facts=payload.get("extracted_facts") or [],
        parameter_checklist=payload["parameter_checklist"],
        project_parameters=payload["project_parameters"],
    ) + correction_audit_rows
    _apply_resource_fact_overrides(payload, payload.get("extracted_facts") or [])


def _apply_fact_overrides(
    payload: dict[str, list[dict[str, Any]]],
    facts: list[dict[str, Any]],
) -> None:
    best = _best_fact_by_parameter(facts)
    checklist_by_id = _index_by_id(payload["parameter_checklist"])
    project_by_id = _index_by_id(payload["project_parameters"])
    created_at = now_iso()
    for parameter_id, fact in best.items():
        value = str(fact.get("value") or "")
        unit = str(fact.get("unit") or "")
        status = str(fact.get("status") or STATUS_SOURCE_EXACT)
        checklist_row = {
            "parameter_id": parameter_id,
            "category": _category_for_parameter(parameter_id),
            "name": fact.get("name") or parameter_id,
            "required": "yes",
            "value": value,
            "unit": unit,
            "source": "document evidence",
            "evidence_id": fact.get("evidence_id") or "",
            "extraction_status": status,
            "confidence": fact.get("confidence") or "0.90",
            "status": status,
            "owner_agent": _owner_for_parameter(parameter_id),
            "note": _format_fact_value(fact),
            "created_by": "parameter_resolver",
            "created_at": created_at,
        }
        if parameter_id in checklist_by_id:
            payload["parameter_checklist"][checklist_by_id[parameter_id]].update(checklist_row)
        else:
            payload["parameter_checklist"].append(checklist_row)

        project_row = {
            "parameter_id": parameter_id,
            "value": value,
            "unit": unit,
            "source": "document evidence",
            "evidence_id": fact.get("evidence_id") or "",
            "extraction_status": status,
            "confidence": fact.get("confidence") or "0.90",
            "confirmed_by": "parameter_resolver",
            "updated_at": created_at,
            "created_by": "parameter_resolver",
            "note": _format_fact_value(fact),
        }
        if parameter_id in project_by_id:
            payload["project_parameters"][project_by_id[parameter_id]].update(project_row)
        else:
            payload["project_parameters"].append(project_row)


def _build_auto_correction_audit_rows(
    payload: dict[str, list[dict[str, Any]]],
    facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Record values that were corrected by explicit source facts."""

    created_at = now_iso()
    rows: list[dict[str, Any]] = []
    best = _best_fact_by_parameter(facts)
    actual = {
        str(row.get("parameter_id") or ""): row
        for row in [*payload.get("parameter_checklist", []), *payload.get("project_parameters", [])]
        if row.get("parameter_id")
    }
    for parameter_id, fact in best.items():
        current = actual.get(parameter_id)
        if not current:
            continue
        current_value = str(current.get("value") or current.get("note") or "")
        fact_value = str(fact.get("value") or "")
        if not current_value or _values_compatible_for_audit(parameter_id, fact_value, current_value):
            continue
        rows.append(
            {
                "audit_id": f"AUD-COR-{len(rows) + 1:04d}",
                "parameter_id": parameter_id,
                "name": fact.get("name") or parameter_id,
                "issue_type": "auto_corrected_from_source",
                "severity": "medium",
                "status": "resolved",
                "expected_value": _format_fact_value(fact),
                "actual_value": current_value,
                "evidence_id": fact.get("evidence_id") or "",
                "suggestion": "Output value was replaced by explicit source evidence before writing.",
                "created_by": "parameter_resolver",
                "created_at": created_at,
            }
        )
    return rows


def _apply_resource_fact_overrides(
    payload: dict[str, list[dict[str, Any]]],
    facts: list[dict[str, Any]],
) -> None:
    tower_fact = _best_fact_by_parameter(facts).get("P-008")
    if not tower_fact:
        return
    value = str(tower_fact.get("value") or "")
    count_match = re.search(r"\d+", value)
    model_match = re.search(r"(TC|QTZ)\d+", value, flags=re.IGNORECASE)
    if not count_match or not model_match:
        return
    count = float(count_match.group(0))
    model = model_match.group(0).upper()
    resource_name = f"{model} tower crane"
    matched = False
    for row in payload["resource_plan_final"]:
        text = f"{row.get('resource_type', '')} {row.get('resource_name', '')}".lower()
        if any(token in text for token in ("tower", "crane", "tc", "qtz", "\u5854\u540a", "\u8d77\u91cd")):
            row.update(
                {
                    "resource_type": "equipment",
                    "resource_name": resource_name,
                    "demand": count,
                    "unit": "\u53f0",
                    "capacity": count,
                    "source": "document evidence",
                    "confidence": tower_fact.get("confidence") or "0.90",
                    "note": f"resolved from source fact {tower_fact.get('evidence_id')}",
                }
            )
            matched = True
    if not matched and payload["wbs_tasks_final"]:
        task_id = _first_structure_task_id(payload["wbs_tasks_final"])
        payload["resource_plan_final"].append(
            {
                "task_id": task_id,
                "resource_type": "equipment",
                "resource_name": resource_name,
                "demand": count,
                "unit": "\u53f0",
                "capacity": count,
                "period": "structure stage",
                "conflict_flag": False,
                "source": "document evidence",
                "confidence": tower_fact.get("confidence") or "0.90",
                "note": f"resolved from source fact {tower_fact.get('evidence_id')}",
                "owner_agent": "resource_allocator_agent",
            }
        )


def _repair_resource_task_refs(payload: dict[str, list[dict[str, Any]]]) -> None:
    wbs_rows = payload.get("wbs_tasks_final") or []
    resource_rows = payload.get("resource_plan_final") or []
    if not wbs_rows:
        return
    task_ids = {str(row.get("task_id") or "") for row in wbs_rows}
    fallback_task_id = _first_structure_task_id(wbs_rows)
    if not resource_rows:
        payload["resource_plan_final"] = [
            {
                "task_id": fallback_task_id,
                "resource_type": "labor",
                "resource_name": "general construction crew",
                "demand": 1,
                "unit": "crew",
                "capacity": 1,
                "period": "whole project",
                "conflict_flag": False,
                "source": "parameter-driven segmented WBS fallback",
                "confidence": "0.60",
                "note": "Placeholder crew row added so expanded WBS has a valid resource anchor.",
                "owner_agent": "resource_allocator_agent",
            }
        ]
        return
    for row in resource_rows:
        if str(row.get("task_id") or "") not in task_ids:
            row["task_id"] = fallback_task_id
            row["note"] = f"{row.get('note') or ''}; task_id remapped after WBS auto-expansion".strip("; ")


def build_quality_gate_rows(
    payload: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build quality gates and return structural fatal blockers.

    Source documents often omit construction-planning parameters. Preserve those
    gaps as warning gates instead of blocking persistence and causing model
    repair loops.
    """

    created_at = now_iso()
    rows: list[dict[str, Any]] = []
    fatal_errors: list[str] = []

    coverage = _key_parameter_coverage(payload)
    if coverage < 70:
        _gate(rows, "parameter_coverage", "warn", "warn", f"{coverage:.1f}%", "target>=70%", "key parameter coverage is low; keep assumptions visible", created_at)
    elif coverage < 95:
        _gate(rows, "parameter_coverage", "warn", "warn", f"{coverage:.1f}%", "target>=95%", "key parameter coverage below target but output is allowed", created_at)
    else:
        _gate(rows, "parameter_coverage", "info", "pass", f"{coverage:.1f}%", "target>=95%", "key parameter coverage passes target", created_at)

    high_audit_count = sum(
        1
        for row in payload.get("parameter_audit") or []
        if str(row.get("severity") or "").lower() == "high"
        and str(row.get("status") or "").lower() not in {"resolved", "closed"}
    )
    if high_audit_count > 2:
        _gate(rows, "high_audit_issues", "warn", "warn", str(high_audit_count), "target<=2", "high severity audit issues remain; output is persisted with warnings", created_at)
    elif high_audit_count:
        _gate(rows, "high_audit_issues", "warn", "warn", str(high_audit_count), "target=0", "high severity audit issues exist but output is allowed", created_at)
    else:
        _gate(rows, "high_audit_issues", "info", "pass", "0", "target=0", "no high severity audit issues", created_at)

    wbs_count = len(payload.get("wbs_tasks_final") or [])
    if _is_highrise_with_basement(payload):
        if wbs_count < 20:
            _gate(rows, "wbs_granularity", "fatal", "fail", str(wbs_count), "warn<40, fatal<20", "high-rise basement WBS is too coarse", created_at)
            fatal_errors.append(f"WBS task count {wbs_count} is below relaxed fatal threshold 20")
        elif wbs_count < 40:
            _gate(rows, "wbs_granularity", "warn", "warn", str(wbs_count), "target 40-80", "high-rise basement WBS below target but output is allowed", created_at)
        else:
            _gate(rows, "wbs_granularity", "info", "pass", str(wbs_count), "target 40-80", "WBS granularity passes target", created_at)
    else:
        _gate(rows, "wbs_granularity", "info", "pass", str(wbs_count), "context dependent", "high-rise basement threshold not applicable", created_at)

    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    if has_mojibake(serialized):
        _gate(rows, "mojibake_scan", "warn", "warn", "detected", "none", "possible mojibake detected in generated output", created_at)
    else:
        _gate(rows, "mojibake_scan", "info", "pass", "clean", "none", "no obvious mojibake detected", created_at)
    return rows, fatal_errors


def _build_schedule_target_audit_rows(
    schedule_rows: list[dict[str, Any]],
    project_parameters: list[dict[str, Any]],
    *,
    start_index: int,
) -> list[dict[str, Any]]:
    if not schedule_rows:
        return []
    created_at = now_iso()
    rows: list[dict[str, Any]] = []
    finish = max(str(row.get("planned_finish") or "") for row in schedule_rows)
    target_finish = _parameter_value(project_parameters, "P-003")
    parsed_target_finish = _parse_date(target_finish)
    if parsed_target_finish and finish and finish != parsed_target_finish.isoformat():
        rows.append(
            {
                "audit_id": f"AUD-{start_index + len(rows):04d}",
                "parameter_id": "P-003",
                "name": "\u7ae3\u5de5\u65e5\u671f",
                "issue_type": "cpm_target_finish_mismatch",
                "severity": "medium",
                "status": "open",
                "expected_value": parsed_target_finish.isoformat(),
                "actual_value": finish,
                "evidence_id": _parameter_evidence(project_parameters, "P-003"),
                "suggestion": "Review WBS durations/dependencies against source finish milestone.",
                "created_by": "schedule_auditor",
                "created_at": created_at,
            }
        )
    total_duration = _parameter_number_text(_parameter_value(project_parameters, "P-001"))
    if total_duration is not None:
        start = min(str(row.get("planned_start") or "") for row in schedule_rows)
        parsed_start = _parse_date(start)
        parsed_finish = _parse_date(finish)
        if parsed_start and parsed_finish:
            actual_days = (parsed_finish - parsed_start).days + 1
            if abs(actual_days - total_duration) > 3:
                rows.append(
                    {
                        "audit_id": f"AUD-{start_index + len(rows):04d}",
                        "parameter_id": "P-001",
                        "name": "\u603b\u5de5\u671f",
                        "issue_type": "cpm_total_duration_mismatch",
                        "severity": "medium",
                        "status": "open",
                        "expected_value": str(total_duration),
                        "actual_value": str(actual_days),
                        "evidence_id": _parameter_evidence(project_parameters, "P-001"),
                        "suggestion": "Review CPM duration against explicit total duration.",
                        "created_by": "schedule_auditor",
                        "created_at": created_at,
                    }
                )
    return rows


def _gate(
    rows: list[dict[str, Any]],
    gate_name: str,
    severity: str,
    result: str,
    metric_value: str,
    threshold: str,
    message: str,
    created_at: str,
) -> None:
    rows.append(
        {
            "gate_id": f"GATE-{len(rows) + 1:04d}",
            "gate_name": gate_name,
            "severity": severity,
            "result": result,
            "metric_value": metric_value,
            "threshold": threshold,
            "message": message,
            "created_by": "output_writer",
            "created_at": created_at,
        }
    )


def _build_debug_record_rows(
    *,
    validation_error: str | None,
    validation_errors: list[str],
    derived_errors: list[str],
) -> list[dict[str, Any]]:
    """Build debug rows for a forced write path."""

    rows: list[dict[str, Any]] = []
    messages: list[tuple[str, str, str, str]] = []
    if validation_error:
        messages.append(
            (
                "validation",
                "final_validation_failed",
                validation_error,
                "Review the coordinator edits and rerun validation.",
            )
        )
    for error in validation_errors:
        messages.append(
            (
                "validation",
                "validation_error",
                error,
                "Review the implicated rows and repair the draft tables.",
            )
        )
    for error in derived_errors:
        messages.append(
            (
                "runtime",
                "forced_persistence",
                error,
                "Repair the input tables or rerun after fixing the draft rows.",
            )
        )
    for index, (module, scenario, problem, fix_action) in enumerate(messages, start=1):
        rows.append(
            {
                "debug_id": f"DBG-{index:04d}",
                "module": module,
                "scenario": scenario,
                "problem": problem,
                "severity": "high" if module == "validation" else "medium",
                "root_cause": "validation_failed_after_max_revisions"
                if module == "validation"
                else "best_effort_forced_write",
                "fix_action": fix_action,
                "retest_result": "pending",
                "owner": "coordinator_agent",
                "status": "written_with_warnings",
                "evidence_path": "",
            }
        )
    return rows


def _validate_wbs(rows: list[dict[str, Any]], errors: list[str]) -> None:
    task_ids = [str(row.get("task_id") or "") for row in rows]
    if len(set(task_ids)) != len(task_ids):
        errors.append("wbs_tasks_final has duplicated task_id values.")
    wbs_codes = [str(row.get("wbs_code") or "") for row in rows]
    if len(set(wbs_codes)) != len(wbs_codes):
        errors.append("wbs_tasks_final has duplicated wbs_code values.")
    for row in rows:
        try:
            if int(row.get("duration_days") or 0) <= 0:
                errors.append(f"{row.get('task_id')} duration_days must be positive.")
        except (TypeError, ValueError):
            errors.append(f"{row.get('task_id')} duration_days must be an integer.")
        if str(row.get("source") or "").strip().lower() in {"", "sample", "template", "default"}:
            errors.append(f"{row.get('task_id')} source cannot be sample/template/default.")
    try:
        calculate_cpm(rows)
    except Exception as exc:
        errors.append(f"wbs_tasks_final predecessor/CPM validation failed: {exc}")


def _validate_resource_rows(
    resource_rows: list[dict[str, Any]],
    wbs_rows: list[dict[str, Any]],
    errors: list[str],
) -> None:
    task_ids = {str(row.get("task_id") or "") for row in wbs_rows}
    for row in resource_rows:
        if str(row.get("task_id") or "") not in task_ids:
            errors.append(f"resource_plan_final references missing task_id: {row.get('task_id')}")
        try:
            float(row.get("demand"))
            float(row.get("capacity"))
        except (TypeError, ValueError):
            errors.append(f"{row.get('task_id')} resource demand/capacity must be numeric.")


def _validate_adjustment_rows(rows: list[dict[str, Any]], errors: list[str]) -> None:
    selected = [row for row in rows if row.get("selected_flag") is True]
    if selected and len(selected) != 1:
        errors.append("adjustment_plan can have only one selected_flag=True row.")


def _validate_evidence_fields(
    payload: dict[str, list[dict[str, Any]]],
    errors: list[str],
) -> None:
    evidence_fields = {
        "parameter_checklist": ("source", "note"),
        "project_parameters": ("source", "confidence", "note"),
        "wbs_tasks_final": ("source", "confidence", "note"),
        "resource_plan_final": ("source", "confidence", "note"),
        "event_log": ("source", "confidence", "note"),
        "adjustment_plan": ("source", "confidence", "note"),
    }
    banned_sources = {"", "sample", "template", "default"}
    for sheet_name, fields in evidence_fields.items():
        for index, row in enumerate(payload.get(sheet_name) or [], start=1):
            for field in fields:
                value = str(row.get(field) or "").strip()
                if not value:
                    errors.append(f"{sheet_name}[{index}] missing evidence field {field}.")
                if field == "source" and value.lower() in banned_sources:
                    errors.append(f"{sheet_name}[{index}] source cannot be sample/template/default.")
                if field == "confidence":
                    try:
                        confidence = float(value)
                    except ValueError:
                        errors.append(f"{sheet_name}[{index}] confidence must be a 0-1 number.")
                        continue
                    if not 0 <= confidence <= 1:
                        errors.append(f"{sheet_name}[{index}] confidence must be within 0-1.")


def _required_start_date_from_parameters(
    rows: list[dict[str, Any]],
    assumption_rows: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None,
) -> date:
    for row in rows:
        if str(row.get("parameter_id") or "") == "P-002":
            value = str(row.get("value") or "")
            parsed = _parse_date(value)
            if parsed:
                return parsed
            month = _parse_year_month(value)
            if month:
                parsed = date(month[0], month[1], 1)
                if assumption_rows is not None:
                    assumption_rows.append(
                        {
                            "assumption_id": f"ASM-{len(assumption_rows) + 1:04d}",
                            "target_type": "project_parameter",
                            "target_id": "P-002",
                            "assumption": "start day defaulted to 01 because source only provided year-month",
                            "basis": value,
                            "risk_level": "medium",
                            "status": "active",
                            "created_by": "output_writer",
                            "created_at": now_iso(),
                        }
                    )
                return parsed
            message = f"project start parameter P-002 is not a valid date: {value}"
            if errors is not None:
                errors.append(message)
                return date.min
            raise AgentOutputValidationError(message)
    message = "project_parameters missing valid start date P-002."
    if errors is not None:
        errors.append(message)
        return date.min
    raise AgentOutputValidationError(message)


def _parse_date(value: str) -> date | None:
    match = re.search(r"([0-9]{4})[-\u5e74/.]([0-9]{1,2})[-\u6708/.]([0-9]{1,2})", value)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _parse_year_month(value: str) -> tuple[int, int] | None:
    match = re.search(r"([0-9]{4})[-\u5e74/.]([0-9]{1,2})(?:\u6708)?", value)
    if not match:
        return None
    year, month = (int(part) for part in match.groups())
    if 1 <= month <= 12:
        return year, month
    return None


def _ensure_adjustment_plan_row(payload: dict[str, list[dict[str, Any]]]) -> None:
    if payload.get("adjustment_plan"):
        return
    payload["adjustment_plan"] = [
        {
            "plan_id": "ADJ-NONE-001",
            "event_id": "NO_EVENT",
            "measure": "No trigger event; no schedule adjustment required",
            "recovered_days": 0,
            "cost_level": "none",
            "risk_level": "low",
            "score": 0,
            "selected_flag": False,
            "source": "quality gate default",
            "confidence": "1.00",
            "note": "Generated because no event_log trigger was provided.",
            "created_by": "output_writer",
            "created_at": now_iso(),
        }
    ]


def _key_parameter_coverage(payload: dict[str, list[dict[str, Any]]]) -> float:
    covered: set[str] = set()
    for row in [*payload.get("parameter_checklist", []), *payload.get("project_parameters", [])]:
        parameter_id = str(row.get("parameter_id") or "")
        if parameter_id not in KEY_PARAMETER_IDS:
            continue
        status = str(row.get("extraction_status") or row.get("status") or "").lower()
        value = str(row.get("value") or row.get("note") or "").strip()
        if value and status not in {STATUS_MISSING, STATUS_CONFLICT, "missing", "conflict"}:
            covered.add(parameter_id)
    return len(covered) / max(len(KEY_PARAMETER_IDS), 1) * 100


def _is_highrise_with_basement(payload: dict[str, list[dict[str, Any]]]) -> bool:
    above = _parameter_number(payload, "P-012")
    basement = _parameter_number(payload, "P-013")
    return (above or 0) >= 10 and (basement or 0) >= 1


def _parameter_number(payload: dict[str, list[dict[str, Any]]], parameter_id: str) -> float | None:
    for row in [*payload.get("project_parameters", []), *payload.get("parameter_checklist", [])]:
        if str(row.get("parameter_id") or "") != parameter_id:
            continue
        match = re.search(r"\d+(?:\.\d+)?", str(row.get("value") or row.get("note") or ""))
        if match:
            return float(match.group(0))
    return None


def _parameter_value(project_parameters: list[dict[str, Any]], parameter_id: str) -> str:
    for row in project_parameters:
        if str(row.get("parameter_id") or "") == parameter_id:
            return str(row.get("value") or row.get("note") or "")
    return ""


def _parameter_evidence(project_parameters: list[dict[str, Any]], parameter_id: str) -> str:
    for row in project_parameters:
        if str(row.get("parameter_id") or "") == parameter_id:
            return str(row.get("evidence_id") or "")
    return ""


def _parameter_number_text(value: str) -> int | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    return int(round(float(match.group(0))))


def _best_fact_by_parameter(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for fact in sorted(
        facts,
        key=lambda row: (
            -int(float(row.get("source_priority") or 0)),
            -float(row.get("confidence") or 0),
            str(row.get("fact_id") or ""),
        ),
    ):
        parameter_id = str(fact.get("parameter_id") or "")
        if parameter_id and parameter_id not in best:
            best[parameter_id] = fact
    return best


def _index_by_id(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(row.get("parameter_id") or ""): index
        for index, row in enumerate(rows)
        if row.get("parameter_id")
    }


def _format_fact_value(fact: dict[str, Any]) -> str:
    value = str(fact.get("value") or "")
    unit = str(fact.get("unit") or "")
    if not unit or value.endswith(unit):
        return value
    return f"{value}{unit}"


def _values_compatible_for_audit(parameter_id: str, expected: str, actual: str) -> bool:
    expected_key = _compare_key_for_audit(parameter_id, expected)
    actual_key = _compare_key_for_audit(parameter_id, actual)
    if not expected_key:
        return True
    return expected_key in actual_key or actual_key in expected_key


def _compare_key_for_audit(parameter_id: str, value: str) -> str:
    text = str(value or "").lower()
    if parameter_id == "P-008":
        count = re.search(r"\d+", text)
        model = re.search(r"(tc|qtz)\d+", text)
        return f"{count.group(0) if count else ''}-{model.group(0) if model else ''}"
    if parameter_id in {"P-001", "P-011", "P-012", "P-013", "P-014"}:
        number = re.search(r"\d+(?:\.\d+)?", text)
        return number.group(0) if number else text
    if parameter_id in {"P-002", "P-003"}:
        parsed = _parse_date(text)
        return parsed.isoformat() if parsed else text
    return re.sub(r"[\s,，。；;:：\-_]+", "", text)


def _category_for_parameter(parameter_id: str) -> str:
    if parameter_id in {"P-001", "P-002", "P-003"}:
        return "project_boundary"
    if parameter_id in {"P-011", "P-012", "P-013"}:
        return "project_scale"
    if parameter_id == "P-008":
        return "resource_boundary"
    return "technical_boundary"


def _owner_for_parameter(parameter_id: str) -> str:
    if parameter_id == "P-008":
        return "resource_allocator_agent"
    if parameter_id in {"P-012", "P-013", "P-014", "P-015", "P-016"}:
        return "wbs_planner_agent"
    return "data_parser_agent"


def _first_structure_task_id(wbs_rows: list[dict[str, Any]]) -> str:
    for row in wbs_rows:
        text = f"{row.get('phase', '')} {row.get('task_name', '')}"
        if any(token in text for token in ("\u4e3b\u4f53", "\u7ed3\u6784", "\u5730\u4e0b", "\u57fa\u7840")):
            return str(row.get("task_id") or "")
    return str(wbs_rows[0].get("task_id") or "")


def _dedupe_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        value = str(row.get(key) or "")
        if value and value in seen:
            continue
        if value:
            seen.add(value)
        output.append(row)
    return output


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "\u662f"}
