"""Extract auditable project parameters from real source documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from tools.document_tools import (
    SourceDocument,
    build_document_evidence_rows,
    concatenate_documents,
)
from tools.model_client import ModelClient, ModelClientError
from tools.parameter_tools import (
    STATUS_CONFLICT,
    STATUS_DERIVED,
    STATUS_INFERRED,
    STATUS_MISSING,
    STATUS_SOURCE_EXACT,
    STATUS_SOURCE_TABLE,
    now_iso,
)


@dataclass(frozen=True)
class ParameterDefinition:
    """One key parameter expected by the real-case intake flow."""

    parameter_id: str
    category: str
    name: str
    required: str
    owner_agent: str
    source_hint: str
    patterns: tuple[str, ...]
    unit_hint: str = ""


PARAMETER_DEFINITIONS: tuple[ParameterDefinition, ...] = (
    ParameterDefinition(
        "P-001",
        "project_boundary",
        "\u603b\u5de5\u671f",
        "yes",
        "data_parser_agent",
        "contract/task book/construction organization design",
        (
            r"(?:\u603b\u5de5\u671f|\u5408\u540c\u5de5\u671f|\u8ba1\u5212\u5de5\u671f|\u5de5\u671f\u76ee\u6807)[^\d]{0,20}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>\u65e5\u5386\u5929|\u5929|\u65e5|\u4e2a\u6708|\u6708)",
        ),
        "\u65e5\u5386\u5929",
    ),
    ParameterDefinition(
        "P-002",
        "project_boundary",
        "\u5f00\u5de5\u65e5\u671f",
        "yes",
        "data_parser_agent",
        "contract/task book/start order",
        (
            r"(?:\u5f00\u5de5\u65e5\u671f|\u8ba1\u5212\u5f00\u5de5|\u5f00\u5de5\u65f6\u95f4|\u5f00\u5de5\u8282\u70b9)[^\d]{0,20}(?P<value>(?:19|20)\d{2}\s*(?:\u5e74|-|/|\.)\s*\d{1,2}(?:\s*(?:\u6708|-|/|\.)\s*\d{1,2}\s*\u65e5?)?)",
            r"(?P<value>(?:19|20)\d{2}\s*\u5e74\s*\d{1,2}\s*\u6708(?:\s*\d{1,2}\s*\u65e5)?)\s*\u5f00\u5de5",
        ),
        "date",
    ),
    ParameterDefinition(
        "P-003",
        "project_boundary",
        "\u7ae3\u5de5\u65e5\u671f",
        "yes",
        "data_parser_agent",
        "contract/task book/milestone plan",
        (
            r"(?:\u7ae3\u5de5\u65e5\u671f|\u8ba1\u5212\u7ae3\u5de5|\u7ae3\u5de5\u65f6\u95f4|\u5b8c\u5de5\u65e5\u671f|\u5b8c\u6210\u65e5\u671f)[^\d]{0,20}(?P<value>(?:19|20)\d{2}\s*(?:\u5e74|-|/|\.)\s*\d{1,2}\s*(?:\u6708|-|/|\.)\s*\d{1,2}\s*\u65e5?)",
        ),
        "date",
    ),
    ParameterDefinition(
        "P-011",
        "project_scale",
        "\u5efa\u7b51\u9762\u79ef",
        "yes",
        "data_parser_agent",
        "project overview/design description",
        (
            r"(?:\u603b\u5efa\u7b51\u9762\u79ef|\u5efa\u7b51\u9762\u79ef)[^\d]{0,20}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m2|m\u00b2|\u33a1|\u5e73\u65b9\u7c73)",
        ),
        "\u33a1",
    ),
    ParameterDefinition(
        "P-012",
        "project_scale",
        "\u5730\u4e0a\u5c42\u6570",
        "yes",
        "wbs_planner_agent",
        "project overview/design description",
        (
            r"(?:\u5730\u4e0a|\u5730\u4e0a\u5c42\u6570)[^\d]{0,12}(?P<value>\d+)\s*(?P<unit>\u5c42)",
            r"(?P<value>\d+)\s*(?P<unit>\u5c42)[^\n\r]{0,10}(?:\u5730\u4e0a|\u4f4f\u5b85)",
        ),
        "\u5c42",
    ),
    ParameterDefinition(
        "P-013",
        "project_scale",
        "\u5730\u4e0b\u5c42\u6570",
        "yes",
        "wbs_planner_agent",
        "project overview/design description",
        (
            r"(?:\u5730\u4e0b|\u5730\u4e0b\u5c42\u6570)[^\d]{0,12}(?P<value>\d+)\s*(?P<unit>\u5c42)",
        ),
        "\u5c42",
    ),
    ParameterDefinition(
        "P-014",
        "schedule_control",
        "\u4e3b\u4f53\u7ed3\u6784\u5de5\u671f",
        "yes",
        "wbs_planner_agent",
        "schedule chapter",
        (
            r"(?:\u4e3b\u4f53\u7ed3\u6784|\u4e3b\u4f53\u5de5\u7a0b)[^\d]{0,30}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>\u65e5\u5386\u5929|\u5929|\u65e5)",
        ),
        "\u65e5\u5386\u5929",
    ),
    ParameterDefinition(
        "P-015",
        "technical_boundary",
        "\u7ed3\u6784\u5f62\u5f0f",
        "yes",
        "wbs_planner_agent",
        "project overview/structure description",
        (
            r"(?P<value>\u73b0\u6d47\u6846\u67b6[-\u2010-\u2015]?\u526a\u529b\u5899\u7ed3\u6784|\u6846\u67b6[-\u2010-\u2015]?\u526a\u529b\u5899\u7ed3\u6784|\u526a\u529b\u5899\u7ed3\u6784|\u6846\u67b6\u7ed3\u6784)",
        ),
    ),
    ParameterDefinition(
        "P-016",
        "technical_boundary",
        "\u57fa\u7840\u5f62\u5f0f",
        "yes",
        "wbs_planner_agent",
        "project overview/foundation description",
        (
            r"(?P<value>\u7b4f\u677f\u57fa\u7840|\u6869\u57fa\u7840|\u72ec\u7acb\u57fa\u7840|\u6761\u5f62\u57fa\u7840)",
        ),
    ),
    ParameterDefinition(
        "P-008",
        "resource_boundary",
        "\u5854\u540a\u578b\u53f7\u4e0e\u6570\u91cf",
        "yes",
        "resource_allocator_agent",
        "equipment plan/site layout/construction organization design",
        (
            r"(?P<count>\d+)\s*\u53f0[^\n\r]{0,20}(?P<model>TC\d+|QTZ\d+)[^\n\r]{0,20}(?:\u5854\u540a|\u5854\u5f0f\u8d77\u91cd\u673a)?",
            r"(?:\u5854\u540a|\u5854\u5f0f\u8d77\u91cd\u673a)[^\n\r]{0,30}(?P<count>\d+)\s*\u53f0[^\n\r]{0,20}(?P<model>TC\d+|QTZ\d+)",
            r"(?P<model>TC\d+|QTZ\d+)[^\n\r]{0,20}(?P<count>\d+)\s*\u53f0",
            r"(?:\u5854\u540a|\u5854\u5f0f\u8d77\u91cd\u673a)[^\n\r]{0,20}(?P<count>\d+)\s*\u53f0",
        ),
        "\u53f0",
    ),
)

KEY_PARAMETER_IDS = {definition.parameter_id for definition in PARAMETER_DEFINITIONS}
EXPLICIT_STATUSES = {STATUS_SOURCE_EXACT, STATUS_SOURCE_TABLE}


def extract_parameter_checklist(
    documents: list[SourceDocument],
    *,
    model_settings: dict[str, Any] | None = None,
    use_model: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract parameter checklist rows from source documents."""

    source_names = ", ".join(document.name for document in documents) or "no source documents"
    warnings = [f"{document.name}: {document.warning}" for document in documents if document.warning]
    section_rows, table_rows = build_document_evidence_rows(documents)
    facts = extract_facts_from_evidence(section_rows, table_rows)
    rule_rows = build_parameter_checklist_from_facts(facts, source_names=source_names)
    combined_text = concatenate_documents(documents)

    if (
        not use_model
        or not model_settings
        or not _model_enabled(model_settings)
        or not combined_text
    ):
        return rule_rows, warnings

    try:
        model_rows, model_warnings = extract_parameter_checklist_by_model(
            combined_text,
            source_names=source_names,
            model_settings=model_settings,
        )
        warnings.extend(model_warnings)
        return merge_parameter_rows(rule_rows, model_rows), warnings
    except (ModelClientError, ValueError, KeyError, TypeError) as exc:
        warnings.append(f"model parameter extraction failed; deterministic rows kept: {exc}")
        return rule_rows, warnings


def extract_parameter_checklist_by_rules(
    text: str,
    *,
    source_names: str,
) -> list[dict[str, Any]]:
    """Build checklist rows with deterministic evidence-style extraction."""

    evidence = [
        {
            "evidence_id": "TEXT-0001",
            "document_name": source_names,
            "source_type": "section",
            "section_title": "",
            "page_or_order": "1",
            "raw_text": text,
            "normalized_text": _normalize_text(text),
            "created_by": "parameter_rules",
            "created_at": now_iso(),
        }
    ]
    facts = extract_facts_from_evidence(evidence, [])
    return build_parameter_checklist_from_facts(facts, source_names=source_names)


def extract_facts_from_evidence(
    section_rows: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract source facts from structured document evidence rows."""

    created_at = now_iso()
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    evidence_rows = [*table_rows, *section_rows]
    for evidence in evidence_rows:
        text = _normalize_text(str(evidence.get("normalized_text") or evidence.get("raw_text") or ""))
        if not text:
            continue
        status = (
            STATUS_SOURCE_TABLE
            if str(evidence.get("source_type") or "").lower() == "table"
            else STATUS_SOURCE_EXACT
        )
        priority = _source_priority(evidence)
        for definition in PARAMETER_DEFINITIONS:
            for match in _iter_definition_matches(definition, text):
                value, unit, fact_status = _normalize_match_value(definition, match)
                if not value:
                    continue
                final_status = fact_status or status
                key = (definition.parameter_id, _compare_key(definition.parameter_id, value), str(evidence.get("evidence_id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                facts.append(
                    {
                        "fact_id": f"FACT-{len(facts) + 1:04d}",
                        "parameter_id": definition.parameter_id,
                        "name": definition.name,
                        "value": value,
                        "unit": unit or definition.unit_hint,
                        "status": final_status,
                        "evidence_id": evidence.get("evidence_id") or "",
                        "source_priority": priority,
                        "confidence": _fact_confidence(final_status, priority),
                        "raw_text": _truncate(str(evidence.get("raw_text") or text), 1200),
                        "created_by": "parameter_resolver",
                        "created_at": created_at,
                    }
                )
    return _sort_facts(facts)


def build_parameter_checklist_from_facts(
    facts: list[dict[str, Any]],
    *,
    source_names: str,
) -> list[dict[str, Any]]:
    """Build parameter_checklist rows from best available facts."""

    created_at = now_iso()
    best = _best_fact_by_parameter(facts)
    rows: list[dict[str, Any]] = []
    for definition in PARAMETER_DEFINITIONS:
        fact = best.get(definition.parameter_id)
        if fact:
            rows.append(
                {
                    "parameter_id": definition.parameter_id,
                    "category": definition.category,
                    "name": definition.name,
                    "required": definition.required,
                    "source": f"document evidence: {source_names}",
                    "evidence_id": fact.get("evidence_id") or "",
                    "extraction_status": fact.get("status") or STATUS_SOURCE_EXACT,
                    "status": fact.get("status") or STATUS_SOURCE_EXACT,
                    "owner_agent": definition.owner_agent,
                    "note": _format_fact_value(fact),
                    "value": fact.get("value") or "",
                    "unit": fact.get("unit") or definition.unit_hint,
                    "confidence": fact.get("confidence") or "0.90",
                    "created_by": "data_parser_agent",
                    "created_at": created_at,
                }
            )
        else:
            rows.append(
                {
                    "parameter_id": definition.parameter_id,
                    "category": definition.category,
                    "name": definition.name,
                    "required": definition.required,
                    "source": definition.source_hint,
                    "evidence_id": "",
                    "extraction_status": STATUS_MISSING if definition.required == "yes" else STATUS_INFERRED,
                    "status": STATUS_MISSING if definition.required == "yes" else STATUS_INFERRED,
                    "owner_agent": definition.owner_agent,
                    "note": f"not located in source; check {definition.source_hint}",
                    "value": "",
                    "unit": definition.unit_hint,
                    "confidence": "0.00",
                    "created_by": "data_parser_agent",
                    "created_at": created_at,
                }
            )
    return rows


def build_parameter_audit_rows(
    *,
    facts: list[dict[str, Any]],
    parameter_checklist: list[dict[str, Any]],
    project_parameters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Audit output parameters against explicit source facts."""

    created_at = now_iso()
    rows: list[dict[str, Any]] = []
    best = _best_fact_by_parameter(facts)
    actual = _actual_rows_by_parameter(parameter_checklist, project_parameters)

    for definition in PARAMETER_DEFINITIONS:
        fact = best.get(definition.parameter_id)
        actual_row = actual.get(definition.parameter_id)
        if not fact:
            rows.append(
                _audit_row(
                    rows,
                    definition,
                    "missing_source_fact",
                    "medium" if definition.required == "yes" else "low",
                    "open",
                    "",
                    str((actual_row or {}).get("value") or (actual_row or {}).get("note") or ""),
                    "",
                    f"key parameter not found in parsed source: {definition.source_hint}",
                    created_at,
                )
            )
            continue
        if not actual_row:
            rows.append(
                _audit_row(
                    rows,
                    definition,
                    "missing_output_parameter",
                    "high",
                    "open",
                    str(fact.get("value") or ""),
                    "",
                    str(fact.get("evidence_id") or ""),
                    "source fact exists but final output lacks the parameter",
                    created_at,
                )
            )
            continue
        actual_value = str(actual_row.get("value") or actual_row.get("note") or "")
        if fact.get("status") in EXPLICIT_STATUSES and not _values_compatible(
            definition.parameter_id,
            str(fact.get("value") or ""),
            actual_value,
        ):
            rows.append(
                _audit_row(
                    rows,
                    definition,
                    "explicit_value_overridden",
                    "high",
                    "open",
                    _format_fact_value(fact),
                    actual_value,
                    str(fact.get("evidence_id") or ""),
                    "explicit source value must not be replaced by inferred/model value",
                    created_at,
                )
            )

    for definition in PARAMETER_DEFINITIONS:
        conflicts = _conflicting_facts(definition.parameter_id, facts)
        if conflicts:
            rows.append(
                _audit_row(
                    rows,
                    definition,
                    "source_conflict",
                    "high",
                    "open",
                    "; ".join(sorted(conflicts)),
                    "",
                    "",
                    "multiple source values found; resolver used highest priority evidence",
                    created_at,
                )
            )
    return rows


def build_assumption_rows_from_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build assumption_register rows for derived values."""

    created_at = now_iso()
    rows: list[dict[str, Any]] = []
    for fact in facts:
        if fact.get("status") != STATUS_DERIVED:
            continue
        if fact.get("parameter_id") == "P-002":
            rows.append(
                {
                    "assumption_id": f"ASM-{len(rows) + 1:04d}",
                    "target_type": "project_parameter",
                    "target_id": "P-002",
                    "assumption": "source only gives start month; default start day to 01",
                    "basis": f"evidence_id={fact.get('evidence_id')}, value={fact.get('value')}",
                    "risk_level": "medium",
                    "status": "active",
                    "created_by": "parameter_resolver",
                    "created_at": created_at,
                }
            )
    return rows


def extract_parameter_checklist_by_model(
    text: str,
    *,
    source_names: str,
    model_settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Use a live model to fill gaps; deterministic evidence still wins later."""

    client = ModelClient(model_settings)
    schema = {
        "type": "object",
        "properties": {
            "parameters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "parameter_id": {"type": "string"},
                        "category": {"type": "string"},
                        "name": {"type": "string"},
                        "required": {"type": "string"},
                        "source": {"type": "string"},
                        "evidence_id": {"type": "string"},
                        "extraction_status": {"type": "string"},
                        "status": {"type": "string"},
                        "owner_agent": {"type": "string"},
                        "note": {"type": "string"},
                        "value": {"type": "string"},
                        "unit": {"type": "string"},
                        "confidence": {"type": "string"},
                    },
                    "required": ["name", "status", "note"],
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["parameters"],
    }
    expected_names = "\n".join(
        f"- {item.parameter_id} {item.name} ({item.category}, required={item.required})"
        for item in PARAMETER_DEFINITIONS
    )
    prompt = f"""
Extract construction schedule parameters as JSON.
Source files: {source_names}

Expected key parameters:
{expected_names}

Rules:
- Prefer exact source text; do not invent exact values.
- status/extraction_status must be one of source_exact, source_table, derived, inferred, missing, conflict.
- If the source only gives a month, output a derived ISO date with day 01 and explain it in note.
- Include a short source quote or section hint in source/note when evidence_id is unknown.

Source text:
{text[:28000]}
""".strip()
    response = client.generate_json(
        instructions="You extract auditable construction schedule parameters and return only JSON.",
        prompt=prompt,
        schema=schema,
    )
    rows = [
        _normalize_model_row(row, index)
        for index, row in enumerate(response.get("parameters") or [], start=1)
        if isinstance(row, dict)
    ]
    warnings = [str(item) for item in response.get("warnings") or []]
    return rows, warnings


def merge_parameter_rows(
    rule_rows: list[dict[str, Any]],
    model_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge model rows into deterministic rows without overwriting source facts."""

    merged = [dict(row) for row in rule_rows]
    index_by_id = {
        str(row.get("parameter_id") or "").strip(): index
        for index, row in enumerate(merged)
        if row.get("parameter_id")
    }
    index_by_name = {_clean_name(row.get("name")): index for index, row in enumerate(merged)}

    for model_row in model_rows:
        key_id = str(model_row.get("parameter_id") or "").strip()
        key_name = _clean_name(model_row.get("name"))
        index = index_by_id.get(key_id)
        if index is None:
            index = index_by_name.get(key_name)
        if index is None:
            merged.append(model_row)
            continue

        current = merged[index]
        current_status = str(current.get("extraction_status") or current.get("status") or "")
        if current_status in {STATUS_SOURCE_EXACT, STATUS_SOURCE_TABLE, STATUS_DERIVED}:
            continue
        if _row_is_more_useful(model_row, current):
            merged[index] = {**current, **model_row}
    return merged


def _normalize_model_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    definition = _definition_by_id(str(row.get("parameter_id") or "")) or _definition_by_name(
        str(row.get("name") or "")
    )
    status = _normalize_model_status(
        str(row.get("extraction_status") or row.get("status") or "").strip()
    )
    if status not in {
        STATUS_SOURCE_EXACT,
        STATUS_SOURCE_TABLE,
        STATUS_DERIVED,
        STATUS_INFERRED,
        STATUS_MISSING,
        STATUS_CONFLICT,
    }:
        status = STATUS_INFERRED if str(row.get("value") or row.get("note") or "").strip() else STATUS_MISSING
    parameter_id = str(row.get("parameter_id") or "").strip()
    if not parameter_id:
        parameter_id = definition.parameter_id if definition else f"P-AUTO-{index:03d}"
    return {
        "parameter_id": parameter_id,
        "category": str(row.get("category") or (definition.category if definition else "model_extracted")),
        "name": str(row.get("name") or (definition.name if definition else f"model_parameter_{index}")),
        "required": str(row.get("required") or (definition.required if definition else "no")),
        "source": str(row.get("source") or "model extraction"),
        "evidence_id": str(row.get("evidence_id") or ""),
        "extraction_status": status,
        "status": status,
        "owner_agent": str(row.get("owner_agent") or (definition.owner_agent if definition else "data_parser_agent")),
        "note": str(row.get("note") or row.get("value") or "needs confirmation"),
        "value": str(row.get("value") or row.get("note") or ""),
        "unit": str(row.get("unit") or (definition.unit_hint if definition else "")),
        "confidence": str(row.get("confidence") or ("0.65" if status == STATUS_INFERRED else "0.75")),
        "created_by": "data_parser_agent",
        "created_at": now_iso(),
    }


def _iter_definition_matches(
    definition: ParameterDefinition,
    text: str,
) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    for pattern in definition.patterns:
        matches.extend(re.finditer(pattern, text, flags=re.IGNORECASE))
    return matches


def _normalize_match_value(
    definition: ParameterDefinition,
    match: re.Match[str],
) -> tuple[str, str, str]:
    if definition.parameter_id == "P-008":
        count = match.groupdict().get("count") or ""
        model = (match.groupdict().get("model") or "").upper()
        if count and model:
            return f"{count} {model}", definition.unit_hint, ""
        if count:
            return count, definition.unit_hint, ""
    raw_value = match.groupdict().get("value") or _first_group(match)
    raw_unit = match.groupdict().get("unit") or definition.unit_hint
    if definition.unit_hint == "date":
        parsed, derived = _parse_date_like(raw_value)
        return parsed, "", STATUS_DERIVED if derived else ""
    return _normalize_value(raw_value), _normalize_unit(raw_unit), ""


def _parse_date_like(value: str) -> tuple[str, bool]:
    match = re.search(
        r"(?P<year>(?:19|20)\d{2})\s*(?:\u5e74|-|/|\.)\s*(?P<month>\d{1,2})(?:\s*(?:\u6708|-|/|\.)\s*(?P<day>\d{1,2}))?",
        value or "",
    )
    if not match:
        return _normalize_value(value), False
    year = int(match.group("year"))
    month = int(match.group("month"))
    day_text = match.group("day")
    day = int(day_text) if day_text else 1
    try:
        return date(year, month, day).isoformat(), day_text is None
    except ValueError:
        return _normalize_value(value), False


def _first_group(match: re.Match[str]) -> str:
    for group in match.groups():
        if group:
            return group
    return match.group(0)


def _best_fact_by_parameter(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for fact in _sort_facts(facts):
        parameter_id = str(fact.get("parameter_id") or "")
        if parameter_id and parameter_id not in best:
            best[parameter_id] = fact
    return best


def _sort_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        facts,
        key=lambda row: (
            -int(float(row.get("source_priority") or 0)),
            -float(row.get("confidence") or 0),
            str(row.get("fact_id") or ""),
        ),
    )


def _source_priority(evidence: dict[str, Any]) -> int:
    source_type = str(evidence.get("source_type") or "").lower()
    title = str(evidence.get("section_title") or "")
    text = str(evidence.get("normalized_text") or evidence.get("raw_text") or "")
    if source_type == "table":
        return 100
    if any(token in title or token in text[:120] for token in ("\u5de5\u7a0b\u6982\u51b5", "\u9879\u76ee\u6982\u51b5")):
        return 90
    if any(token in title or token in text[:120] for token in ("\u8fdb\u5ea6", "\u5de5\u671f")):
        return 85
    if any(token in title or token in text[:120] for token in ("\u65bd\u5de5\u65b9\u6848", "\u65bd\u5de5\u65b9\u6cd5")):
        return 70
    return 60


def _fact_confidence(status: str, priority: int) -> str:
    base = 0.96 if status == STATUS_SOURCE_TABLE else 0.93
    if status == STATUS_DERIVED:
        base = 0.82
    return f"{min(base, 0.70 + priority / 400):.2f}"


def _format_fact_value(fact: dict[str, Any]) -> str:
    value = str(fact.get("value") or "")
    unit = str(fact.get("unit") or "")
    if unit == "date":
        return value
    if not unit or value.endswith(unit):
        return value
    return f"{value}{unit}"


def _audit_row(
    existing: list[dict[str, Any]],
    definition: ParameterDefinition,
    issue_type: str,
    severity: str,
    status: str,
    expected_value: str,
    actual_value: str,
    evidence_id: str,
    suggestion: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "audit_id": f"AUD-{len(existing) + 1:04d}",
        "parameter_id": definition.parameter_id,
        "name": definition.name,
        "issue_type": issue_type,
        "severity": severity,
        "status": status,
        "expected_value": expected_value,
        "actual_value": actual_value,
        "evidence_id": evidence_id,
        "suggestion": suggestion,
        "created_by": "parameter_auditor",
        "created_at": created_at,
    }


def _actual_rows_by_parameter(
    parameter_checklist: list[dict[str, Any]],
    project_parameters: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in parameter_checklist:
        parameter_id = str(row.get("parameter_id") or "")
        if parameter_id:
            rows[parameter_id] = row
    for row in project_parameters:
        parameter_id = str(row.get("parameter_id") or "")
        if parameter_id:
            rows[parameter_id] = row
    return rows


def _conflicting_facts(parameter_id: str, facts: list[dict[str, Any]]) -> set[str]:
    values = {
        _compare_key(parameter_id, str(fact.get("value") or ""))
        for fact in facts
        if str(fact.get("parameter_id") or "") == parameter_id
        and fact.get("status") in EXPLICIT_STATUSES | {STATUS_DERIVED}
    }
    return values if len(values) > 1 else set()


def _values_compatible(parameter_id: str, expected: str, actual: str) -> bool:
    expected_key = _compare_key(parameter_id, expected)
    actual_key = _compare_key(parameter_id, actual)
    if not expected_key:
        return True
    return expected_key in actual_key or actual_key in expected_key


def _compare_key(parameter_id: str, value: str) -> str:
    text = _normalize_value(value).lower()
    if parameter_id == "P-008":
        count = re.search(r"\d+", text)
        model = re.search(r"(tc|qtz)\d+", text)
        return f"{count.group(0) if count else ''}-{model.group(0) if model else ''}"
    if parameter_id in {"P-001", "P-011", "P-012", "P-013", "P-014"}:
        number = re.search(r"\d+(?:\.\d+)?", text)
        return number.group(0) if number else text
    if parameter_id in {"P-002", "P-003"}:
        parsed, _ = _parse_date_like(text)
        return parsed
    return re.sub(r"[\s,，。；;:：\-_]+", "", text)


def _normalize_model_status(status: str) -> str:
    status = (status or "").strip()
    mapping = {
        "\u5df2\u83b7\u53d6": STATUS_SOURCE_EXACT,
        "\u7f3a\u5931": STATUS_MISSING,
        "\u9700\u4eba\u5de5\u786e\u8ba4": STATUS_INFERRED,
        "\u5f85\u786e\u8ba4": STATUS_INFERRED,
    }
    return mapping.get(status, status)


def _definition_by_id(parameter_id: str) -> ParameterDefinition | None:
    for definition in PARAMETER_DEFINITIONS:
        if definition.parameter_id == parameter_id:
            return definition
    return None


def _definition_by_name(name: str) -> ParameterDefinition | None:
    cleaned = _clean_name(name)
    for definition in PARAMETER_DEFINITIONS:
        if _clean_name(definition.name) == cleaned:
            return definition
    return None


def _row_is_more_useful(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    candidate_status = _normalize_model_status(str(candidate.get("extraction_status") or candidate.get("status") or ""))
    current_status = _normalize_model_status(str(current.get("extraction_status") or current.get("status") or ""))
    if current_status in {STATUS_MISSING, "", "pending"} and candidate_status != STATUS_MISSING:
        return True
    if candidate_status in EXPLICIT_STATUSES and current_status not in EXPLICIT_STATUSES:
        return True
    return len(str(candidate.get("note") or candidate.get("value") or "")) > len(
        str(current.get("note") or current.get("value") or "")
    )


def _normalize_value(value: str) -> str:
    return (
        str(value or "")
        .replace("\u5e74", "-")
        .replace("\u6708", "-")
        .replace("\u65e5", "")
        .replace("/", "-")
        .replace("\uff0d", "-")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .strip(" \t\r\n,，。；;:：")
    )


def _normalize_unit(unit: str) -> str:
    value = str(unit or "").strip()
    if value in {"m2", "m\u00b2", "\u5e73\u65b9\u7c73"}:
        return "\u33a1"
    if value in {"\u5929", "\u65e5"}:
        return "\u65e5\u5386\u5929"
    return value


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\u3000", " ")).strip()


def _clean_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _truncate(text: str, max_chars: int) -> str:
    value = str(text or "")
    return value if len(value) <= max_chars else value[: max_chars - 12] + "...<truncated>"


def _model_enabled(settings: dict[str, Any]) -> bool:
    provider = str(settings.get("provider") or "mock").lower()
    return provider not in {"mock", "none"} and bool(settings.get("model")) and bool(
        settings.get("api_key") or settings.get("azure_api_key")
    )
