"""Preprocess uploaded project documents into a standardized intake package."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blackboard.excel_store import ExcelBlackboardStore
from tools.parameter_tools import now_iso
from tools.document_tools import (
    SourceDocument,
    build_document_evidence_rows,
    concatenate_documents,
    read_source_documents,
)
from tools.parameter_extraction import (
    extract_facts_from_evidence,
    extract_parameter_checklist_by_rules,
)

PREPROCESS_DIRNAME = "preprocess"
PREPROCESS_JSON = "standardized_input_package.json"
PREPROCESS_MARKDOWN = "standardized_input_package.md"
GENERATED_INPUT_MARKDOWN = "standardized_input_package.md"


@dataclass(frozen=True)
class PreprocessResult:
    package: dict[str, Any]
    json_path: Path
    markdown_path: Path
    generated_input_path: Path


def preprocess_job_documents(
    input_docs_dir: Path,
    outputs_root: Path,
    blackboard_path: Path,
) -> PreprocessResult:
    """Read uploaded files and create a reusable standardized input package."""

    documents = [
        document
        for document in read_source_documents(input_docs_dir)
        if document.path.name != GENERATED_INPUT_MARKDOWN
    ]
    section_rows, table_rows = build_document_evidence_rows(documents)
    facts = extract_facts_from_evidence(section_rows, table_rows)
    source_names = ", ".join(document.name for document in documents) or "no source documents"
    combined_text = concatenate_documents(documents, max_chars=80000)
    parameter_rows = extract_parameter_checklist_by_rules(combined_text, source_names=source_names)

    store = ExcelBlackboardStore(blackboard_path)
    store.initialize()
    manual_parameters = store.read_rows("project_parameters")
    parameter_rows = merge_manual_parameters(parameter_rows, manual_parameters)

    package = build_standardized_package(
        documents=documents,
        section_rows=section_rows,
        table_rows=table_rows,
        facts=facts,
        parameter_rows=parameter_rows,
        combined_text=combined_text,
    )

    output_dir = outputs_root / PREPROCESS_DIRNAME
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / PREPROCESS_JSON
    markdown_path = output_dir / PREPROCESS_MARKDOWN
    generated_input_path = input_docs_dir / GENERATED_INPUT_MARKDOWN

    json_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_standardized_markdown(package)
    markdown_path.write_text(markdown, encoding="utf-8")
    generated_input_path.write_text(markdown, encoding="utf-8")

    store.replace_sheets_rows(
        {
            "document_sections": section_rows,
            "document_tables": table_rows,
            "extracted_facts": facts,
            "parameter_checklist": parameter_rows,
        }
    )
    return PreprocessResult(
        package=package,
        json_path=json_path,
        markdown_path=markdown_path,
        generated_input_path=generated_input_path,
    )


def load_preprocess_package(outputs_root: Path) -> dict[str, Any] | None:
    path = outputs_root / PREPROCESS_DIRNAME / PREPROCESS_JSON
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def save_preprocess_supplements(
    blackboard_path: Path,
    form: dict[str, Any],
) -> dict[str, Any]:
    """Save user supplied missing parameters and resource capacities."""

    store = ExcelBlackboardStore(blackboard_path)
    store.initialize()
    parameter_rows = parse_parameter_supplements(form)
    resource_rows = parse_resource_supplements(form)

    if parameter_rows:
        upsert_project_parameters(store, parameter_rows)
        upsert_parameter_checklist(store, parameter_rows)
    if resource_rows:
        append_resource_rows(store, resource_rows)
    return {
        "parameters_saved": len(parameter_rows),
        "resources_saved": len(resource_rows),
    }


def parse_parameter_supplements(form: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    indexes = indexed_keys(form, "param_id__")
    for index in indexes:
        parameter_id = clean(form.get(f"param_id__{index}"))
        value = clean(form.get(f"param_value__{index}"))
        if not parameter_id or not value:
            continue
        rows.append(
            {
                "parameter_id": parameter_id,
                "name": clean(form.get(f"param_name__{index}")),
                "category": clean(form.get(f"param_category__{index}")),
                "value": value,
                "unit": clean(form.get(f"param_unit__{index}")),
                "note": clean(form.get(f"param_note__{index}")) or "用户在资料预处理页补充",
            }
        )
    return rows


def parse_resource_supplements(form: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    indexes = indexed_keys(form, "resource_task_id__")
    for index in indexes:
        row = {
            "task_id": clean(form.get(f"resource_task_id__{index}")),
            "resource_type": clean(form.get(f"resource_type__{index}")),
            "resource_name": clean(form.get(f"resource_name__{index}")),
            "demand": parse_float(form.get(f"resource_demand__{index}")),
            "unit": clean(form.get(f"resource_unit__{index}")),
            "capacity": parse_float(form.get(f"resource_capacity__{index}")),
            "period": clean(form.get(f"resource_period__{index}")),
            "source": "web_preprocess_supplement",
            "confidence": "1.00",
            "note": clean(form.get(f"resource_note__{index}")) or "用户在资料预处理页补充",
            "owner_agent": "web_user",
        }
        if not any(str(row.get(field) or "").strip() for field in ("task_id", "resource_type", "resource_name", "demand", "capacity")):
            continue
        missing = [field for field in ("task_id", "resource_type", "resource_name", "demand", "capacity") if row.get(field) in {"", None}]
        if missing:
            raise ValueError(f"资源补充第 {index + 1} 行缺少必填项：{', '.join(missing)}")
        row["conflict_flag"] = bool(row["capacity"] and row["demand"] > row["capacity"])
        rows.append(row)
    return rows


def upsert_project_parameters(store: ExcelBlackboardStore, rows: list[dict[str, Any]]) -> None:
    existing = store.read_rows("project_parameters")
    by_id = {str(row.get("parameter_id") or ""): dict(row) for row in existing}
    timestamp = now_iso()
    for row in rows:
        parameter_id = row["parameter_id"]
        current = by_id.get(parameter_id, {})
        current.update(
            {
                "parameter_id": parameter_id,
                "value": row["value"],
                "unit": row["unit"],
                "source": "web_preprocess_supplement",
                "evidence_id": "",
                "extraction_status": "user_confirmed",
                "confidence": "1.00",
                "confirmed_by": "web_user",
                "updated_at": timestamp,
                "created_by": current.get("created_by") or "web_user",
                "note": row["note"],
            }
        )
        by_id[parameter_id] = current
    store.replace_rows("project_parameters", list(by_id.values()))


def upsert_parameter_checklist(store: ExcelBlackboardStore, rows: list[dict[str, Any]]) -> None:
    existing = store.read_rows("parameter_checklist")
    by_id = {str(row.get("parameter_id") or ""): dict(row) for row in existing}
    timestamp = now_iso()
    for row in rows:
        parameter_id = row["parameter_id"]
        current = by_id.get(parameter_id, {})
        current.update(
            {
                "parameter_id": parameter_id,
                "category": row["category"] or current.get("category") or "user_supplement",
                "name": row["name"] or current.get("name") or parameter_id,
                "required": current.get("required") or "yes",
                "value": row["value"],
                "unit": row["unit"],
                "source": "web_preprocess_supplement",
                "evidence_id": "",
                "extraction_status": "user_confirmed",
                "confidence": "1.00",
                "status": "user_confirmed",
                "owner_agent": current.get("owner_agent") or "data_parser_agent",
                "note": row["note"],
                "created_by": current.get("created_by") or "web_user",
                "created_at": current.get("created_at") or timestamp,
            }
        )
        by_id[parameter_id] = current
    store.replace_rows("parameter_checklist", list(by_id.values()))


def append_resource_rows(store: ExcelBlackboardStore, rows: list[dict[str, Any]]) -> None:
    existing = store.read_rows("resource_plan_final")
    existing.extend(rows)
    store.replace_rows("resource_plan_final", existing)


def merge_manual_parameters(
    parameter_rows: list[dict[str, Any]],
    manual_parameters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not manual_parameters:
        return parameter_rows
    by_id = {str(row.get("parameter_id") or ""): dict(row) for row in parameter_rows}
    timestamp = now_iso()
    for row in manual_parameters:
        parameter_id = str(row.get("parameter_id") or "").strip()
        value = str(row.get("value") or "").strip()
        if not parameter_id or not value:
            continue
        current = by_id.get(parameter_id, {})
        current.update(
            {
                "parameter_id": parameter_id,
                "category": current.get("category") or "user_supplement",
                "name": current.get("name") or parameter_id,
                "required": current.get("required") or "yes",
                "value": value,
                "unit": row.get("unit") or current.get("unit") or "",
                "source": row.get("source") or "project_parameters",
                "evidence_id": row.get("evidence_id") or "",
                "extraction_status": row.get("extraction_status") or "user_confirmed",
                "confidence": row.get("confidence") or "1.00",
                "status": row.get("extraction_status") or "user_confirmed",
                "owner_agent": current.get("owner_agent") or "data_parser_agent",
                "note": row.get("note") or "用户补充参数",
                "created_by": current.get("created_by") or "web_user",
                "created_at": current.get("created_at") or timestamp,
            }
        )
        by_id[parameter_id] = current
    return list(by_id.values())


def build_standardized_package(
    *,
    documents: list[SourceDocument],
    section_rows: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    parameter_rows: list[dict[str, Any]],
    combined_text: str,
) -> dict[str, Any]:
    missing_required = [
        normalize_parameter_row(row)
        for row in parameter_rows
        if str(row.get("required") or "").lower() in {"yes", "是", "true", "1"}
        and not str(row.get("value") or "").strip()
    ]
    recognized_parameters = [
        normalize_parameter_row(row)
        for row in parameter_rows
        if str(row.get("value") or "").strip()
    ]
    resource_candidates = extract_resource_candidates(combined_text)
    schedule_candidates = extract_schedule_candidates(combined_text)
    quantity_candidates = extract_quantity_candidates(combined_text)
    risk_candidates = extract_risk_candidates(combined_text)
    readiness = build_readiness(
        documents=documents,
        recognized_parameters=recognized_parameters,
        missing_required=missing_required,
        resource_candidates=resource_candidates,
        schedule_candidates=schedule_candidates,
    )
    return {
        "schema_version": 1,
        "summary": {
            "document_count": len(documents),
            "readable_document_count": sum(1 for document in documents if bool(document.text.strip())),
            "section_count": len(section_rows),
            "table_count": len(table_rows),
            "recognized_parameter_count": len(recognized_parameters),
            "missing_required_count": len(missing_required),
            "resource_candidate_count": len(resource_candidates),
            "schedule_candidate_count": len(schedule_candidates),
            "quantity_candidate_count": len(quantity_candidates),
            "risk_candidate_count": len(risk_candidates),
            "readiness_score": readiness["score"],
            "readiness_level": readiness["level"],
        },
        "documents": [
            {
                "name": document.name,
                "chars": len(document.text or ""),
                "warning": document.warning,
                "readable": bool((document.text or "").strip()),
            }
            for document in documents
        ],
        "recognized_parameters": recognized_parameters,
        "missing_required_parameters": missing_required,
        "resource_candidates": resource_candidates,
        "schedule_candidates": schedule_candidates,
        "quantity_candidates": quantity_candidates,
        "risk_event_candidates": risk_candidates,
        "supplement_questions": build_supplement_questions(
            missing_required=missing_required,
            resource_candidates=resource_candidates,
            schedule_candidates=schedule_candidates,
        ),
        "readiness": readiness,
        "notes": [
            "该预处理包由规则解析生成，不替代人工复核。",
            "原始资料不会被覆盖；系统会额外生成一份标准化 Markdown 作为后续 Agent 的辅助输入。",
            "若资源只有名称但缺少数量/能力，后续资源负荷计算仍需要人工补充 demand 与 capacity。",
        ],
    }


def normalize_parameter_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameter_id": str(row.get("parameter_id") or ""),
        "category": str(row.get("category") or ""),
        "name": str(row.get("name") or ""),
        "value": str(row.get("value") or ""),
        "unit": str(row.get("unit") or ""),
        "status": str(row.get("extraction_status") or row.get("status") or ""),
        "confidence": str(row.get("confidence") or ""),
        "source": str(row.get("source") or ""),
        "note": str(row.get("note") or ""),
    }


def extract_resource_candidates(text: str) -> list[dict[str, Any]]:
    keywords = (
        "塔吊",
        "塔式起重机",
        "汽车吊",
        "起重机",
        "挖掘机",
        "压桩机",
        "混凝土泵",
        "输送泵",
        "电焊机",
        "整平机",
        "钢筋工",
        "木工",
        "混凝土工",
        "安装工",
        "机电",
        "劳动力",
        "班组",
        "机械",
        "设备",
        "材料",
    )
    candidates = []
    for snippet in keyword_snippets(text, keywords, limit=80):
        count_match = re.search(r"(\d+(?:\.\d+)?)\s*(台|人|套|个|组|班|t|吨|m³|m3|㎡|m2)", snippet)
        candidates.append(
            {
                "name_hint": guess_resource_name(snippet, keywords),
                "quantity_hint": count_match.group(1) if count_match else "",
                "unit_hint": count_match.group(2) if count_match else "",
                "needs_capacity": True,
                "snippet": snippet,
            }
        )
    return dedupe_by_snippet(candidates)[:40]


def extract_schedule_candidates(text: str) -> list[dict[str, Any]]:
    patterns = [
        r"[^。\n\r]{0,24}(?:开工|竣工|完工|完成|封顶|验收|节点|里程碑)[^。\n\r]{0,60}",
        r"[^。\n\r]{0,24}(?:\d{4}[年./-]\s*\d{1,2}[月./-]\s*\d{0,2}\s*日?)[^。\n\r]{0,60}",
        r"[^。\n\r]{0,24}(?:\d+(?:\.\d+)?)\s*(?:日历天|天|个月|月)[^。\n\r]{0,60}",
    ]
    rows = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            snippet = normalize_space(match.group(0))
            if len(snippet) >= 8:
                rows.append({"type": guess_schedule_type(snippet), "snippet": snippet})
    return dedupe_by_snippet(rows)[:50]


def extract_quantity_candidates(text: str) -> list[dict[str, Any]]:
    keywords = ("工程量", "建筑面积", "混凝土", "钢结构", "钢筋", "土方", "桩", "地坪", "砌体", "管道", "喷淋", "防火卷帘")
    rows = []
    for snippet in keyword_snippets(text, keywords, limit=80):
        quantity_match = re.search(r"(\d+(?:\.\d+)?)\s*(m³|m3|㎡|m2|m|米|t|吨|根|套|个|点|台)", snippet)
        rows.append(
            {
                "item_hint": guess_resource_name(snippet, keywords),
                "quantity_hint": quantity_match.group(1) if quantity_match else "",
                "unit_hint": quantity_match.group(2) if quantity_match else "",
                "snippet": snippet,
            }
        )
    return dedupe_by_snippet(rows)[:40]


def extract_risk_candidates(text: str) -> list[dict[str, Any]]:
    keywords = (
        "暴雨",
        "降雨",
        "台风",
        "高温",
        "延误",
        "供应",
        "停工",
        "设计变更",
        "规范",
        "审批",
        "窝工",
        "安全",
        "质量",
        "场地",
        "交通",
        "地质",
        "溶洞",
    )
    rows = []
    for snippet in keyword_snippets(text, keywords, limit=90):
        impact_match = re.search(r"(?:延后|延误|停滞|增加|影响|需额外)[^。\n\r]{0,12}(\d+(?:\.\d+)?)\s*(天|日|个月|月)", snippet)
        rows.append(
            {
                "event_hint": guess_resource_name(snippet, keywords),
                "impact_hint": impact_match.group(1) if impact_match else "",
                "impact_unit": impact_match.group(2) if impact_match else "",
                "snippet": snippet,
            }
        )
    return dedupe_by_snippet(rows)[:35]


def build_supplement_questions(
    *,
    missing_required: list[dict[str, Any]],
    resource_candidates: list[dict[str, Any]],
    schedule_candidates: list[dict[str, Any]],
) -> list[dict[str, str]]:
    questions = []
    for row in missing_required[:8]:
        questions.append(
            {
                "field": row["name"],
                "reason": "资料中未稳定识别到该必需参数。",
                "suggestion": f"请补充 {row['name']}，或确认资料中对应表述位置。",
            }
        )
    if resource_candidates:
        questions.append(
            {
                "field": "资源需求量 demand",
                "reason": "资料中出现了资源名称，但不一定包含每个工序的需求量。",
                "suggestion": "请确认关键机械、班组、材料在各工序上的需求数量。",
            }
        )
        questions.append(
            {
                "field": "资源能力 capacity",
                "reason": "资源负荷计算需要可用能力，否则会出现 demand/capacity 缺失。",
                "suggestion": "请确认每类资源的可用能力，例如台数、人数、日产能或班组能力。",
            }
        )
    if not schedule_candidates:
        questions.append(
            {
                "field": "关键节点",
                "reason": "未识别到明确节点日期或工期描述。",
                "suggestion": "请补充开工、竣工、主要节点或阶段工期。",
            }
        )
    return questions[:12]


def build_readiness(
    *,
    documents: list[SourceDocument],
    recognized_parameters: list[dict[str, Any]],
    missing_required: list[dict[str, Any]],
    resource_candidates: list[dict[str, Any]],
    schedule_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    score = 0
    if any(document.text.strip() for document in documents):
        score += 20
    score += min(30, len(recognized_parameters) * 4)
    score += min(20, len(schedule_candidates) * 2)
    score += min(15, len(resource_candidates) * 2)
    score -= min(35, len(missing_required) * 4)
    score = max(0, min(100, score))
    if score >= 75:
        level = "较完整"
    elif score >= 50:
        level = "可试运行，建议补充"
    else:
        level = "资料不足，建议先补充"
    blockers = []
    if not any(document.text.strip() for document in documents):
        blockers.append("没有可读取的资料正文。")
    if missing_required:
        blockers.append(f"仍有 {len(missing_required)} 个必需参数未识别。")
    if resource_candidates and not any(row.get("quantity_hint") for row in resource_candidates):
        blockers.append("识别到资源信息，但资源数量/能力不明确。")
    return {"score": score, "level": level, "blockers": blockers}


def render_standardized_markdown(package: dict[str, Any]) -> str:
    lines = [
        "# 标准化输入包",
        "",
        "## 资料状态",
        "",
        f"- 可读文件数：{package['summary']['readable_document_count']} / {package['summary']['document_count']}",
        f"- 识别参数数：{package['summary']['recognized_parameter_count']}",
        f"- 缺失必需参数数：{package['summary']['missing_required_count']}",
        f"- 运行准备度：{package['summary']['readiness_score']}（{package['summary']['readiness_level']}）",
        "",
        "## 已识别关键参数",
        "",
    ]
    lines.extend(markdown_table(package["recognized_parameters"], ("parameter_id", "name", "value", "unit", "status", "note")))
    lines.extend(["", "## 缺失必需参数", ""])
    lines.extend(markdown_table(package["missing_required_parameters"], ("parameter_id", "name", "source", "note")))
    lines.extend(["", "## 资源候选信息", ""])
    lines.extend(markdown_table(package["resource_candidates"], ("name_hint", "quantity_hint", "unit_hint", "needs_capacity", "snippet")))
    lines.extend(["", "## 工期与节点候选信息", ""])
    lines.extend(markdown_table(package["schedule_candidates"], ("type", "snippet")))
    lines.extend(["", "## 工程量候选信息", ""])
    lines.extend(markdown_table(package["quantity_candidates"], ("item_hint", "quantity_hint", "unit_hint", "snippet")))
    lines.extend(["", "## 风险与扰动候选信息", ""])
    lines.extend(markdown_table(package["risk_event_candidates"], ("event_hint", "impact_hint", "impact_unit", "snippet")))
    lines.extend(["", "## 建议补充问题", ""])
    lines.extend(markdown_table(package["supplement_questions"], ("field", "reason", "suggestion")))
    return "\n".join(lines).strip() + "\n"


def markdown_table(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[str]:
    if not rows:
        return ["暂无。"]
    output = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    for row in rows[:60]:
        output.append("| " + " | ".join(escape_md(str(row.get(field, ""))) for field in fields) + " |")
    return output


def keyword_snippets(text: str, keywords: tuple[str, ...], *, limit: int) -> list[str]:
    snippets = []
    compact = normalize_space(text)
    for keyword in keywords:
        for match in re.finditer(re.escape(keyword), compact):
            start = max(0, match.start() - limit)
            end = min(len(compact), match.end() + limit)
            snippets.append(compact[start:end])
    return snippets


def guess_resource_name(snippet: str, keywords: tuple[str, ...]) -> str:
    for keyword in keywords:
        if keyword in snippet:
            return keyword
    return ""


def guess_schedule_type(snippet: str) -> str:
    if "开工" in snippet:
        return "开工"
    if "竣工" in snippet:
        return "竣工"
    if "验收" in snippet:
        return "验收"
    if "封顶" in snippet:
        return "封顶"
    if "节点" in snippet or "里程碑" in snippet:
        return "节点"
    return "工期/日期"


def dedupe_by_snippet(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for row in rows:
        snippet = normalize_space(str(row.get("snippet") or ""))
        key = snippet[:120]
        if not snippet or key in seen:
            continue
        seen.add(key)
        row = {**row, "snippet": snippet[:260]}
        output.append(row)
    return output


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def indexed_keys(form: dict[str, Any], prefix: str) -> list[int]:
    indexes = []
    for key in form:
        if not key.startswith(prefix):
            continue
        raw = key[len(prefix) :]
        if raw.isdigit():
            indexes.append(int(raw))
    return sorted(set(indexes))


def clean(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def parse_float(value: Any) -> float | None:
    text = clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"数值格式不正确：{text}") from exc
