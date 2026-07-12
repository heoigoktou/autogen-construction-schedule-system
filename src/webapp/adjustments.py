"""Schedule adjustment helpers for Web forms."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any

from blackboard.excel_store import ExcelBlackboardStore


EVENT_PRESETS: dict[str, dict[str, Any]] = {
    "heavy_rain": {
        "label": "特大暴雨",
        "default_impact_days": 3,
        "measure": "暂停受影响作业，顺延相关任务并重新计算关键线路。",
        "risk_level": "high",
    },
    "extreme_heat": {
        "label": "高温限时施工",
        "default_impact_days": 2,
        "measure": "压缩高温时段作业窗口，顺延受影响任务并复核资源负载。",
        "risk_level": "medium",
    },
    "typhoon": {
        "label": "台风/大风停工",
        "default_impact_days": 4,
        "measure": "暂停吊装、外立面和室外作业，顺延相关任务并检查关键线路。",
        "risk_level": "high",
    },
    "supply_delay": {
        "label": "材料/设备延迟",
        "default_impact_days": 5,
        "measure": "顺延受影响任务，优先检查关键线路和资源冲突。",
        "risk_level": "medium",
    },
    "labor_shortage": {
        "label": "劳动力不足",
        "default_impact_days": 3,
        "measure": "降低受影响任务施工效率，延长工期并复核资源冲突。",
        "risk_level": "medium",
    },
    "equipment_failure": {
        "label": "关键设备故障",
        "default_impact_days": 2,
        "measure": "顺延依赖该设备的任务，重新检查资源与关键线路。",
        "risk_level": "medium",
    },
    "design_change": {
        "label": "设计变更",
        "default_impact_days": 7,
        "measure": "顺延相关专业任务，记录变更事件并重新计算里程碑。",
        "risk_level": "high",
    },
    "approval_delay": {
        "label": "审批/验收延迟",
        "default_impact_days": 3,
        "measure": "顺延审批或验收后置任务，复核后续里程碑。",
        "risk_level": "medium",
    },
    "site_restriction": {
        "label": "场地/交通受限",
        "default_impact_days": 2,
        "measure": "调整现场作业窗口，顺延相关任务并重新检查约束。",
        "risk_level": "medium",
    },
    "safety_quality_stop": {
        "label": "安全/质量整改停工",
        "default_impact_days": 3,
        "measure": "顺延整改关联任务，重新检查约束和里程碑风险。",
        "risk_level": "high",
    },
    "custom": {
        "label": "自定义事件",
        "default_impact_days": 1,
        "measure": "按人工输入的影响天数调整计划并重新计算。",
        "risk_level": "medium",
    },
}


@dataclass(frozen=True)
class AdjustmentResult:
    message: str
    changed_tasks: int
    new_start_date: str


def current_project_start_date(store: ExcelBlackboardStore) -> str:
    for row in store.read_rows("project_parameters"):
        if str(row.get("parameter_id") or "") == "P-002":
            return str(row.get("value") or "")
    return ""


def apply_schedule_adjustment(
    store: ExcelBlackboardStore,
    form: dict[str, Any],
) -> AdjustmentResult:
    """Apply user-entered deterministic adjustments to blackboard sheets."""

    start_date_text = str(form.get("start_date") or "").strip()
    finish_date_text = str(form.get("finish_date") or "").strip()
    total_days_text = str(form.get("total_days") or "").strip()
    parameter_id = str(form.get("parameter_id") or "").strip()
    parameter_value = str(form.get("parameter_value") or "").strip()
    parameter_unit = str(form.get("parameter_unit") or "").strip()
    event_type = str(form.get("event_type") or "").strip()
    event_note = str(form.get("event_note") or "").strip()
    related_task = str(form.get("related_task") or "").strip()
    impact_days = _parse_non_negative_int(form.get("impact_days"), field="影响天数")
    task_id = str(form.get("task_id") or "").strip()
    duration_delta = _parse_int(form.get("duration_delta"), field="任务工期增减")
    duration_set = _parse_non_negative_int(form.get("duration_set"), field="任务新工期")
    predecessor_ids = str(form.get("predecessor_ids") or "").strip()
    relation_type = str(form.get("relation_type") or "").strip().upper()
    lag_days = _parse_int(form.get("lag_days"), field="前置滞后天数")
    resource_task_id = str(form.get("resource_task_id") or "").strip()
    resource_name = str(form.get("resource_name") or "").strip()
    resource_demand = _parse_optional_float(form.get("resource_demand"), field="资源需求")
    resource_capacity = _parse_optional_float(form.get("resource_capacity"), field="资源能力")

    changed_start = ""
    if start_date_text:
        parsed = parse_date(start_date_text)
        if parsed is None:
            raise ValueError("调整后的开工日期格式不正确，请使用 YYYY-MM-DD。")
        changed_start = parsed.isoformat()
        _upsert_project_start_date(store, parsed)

    changed_parameters = 0
    if finish_date_text:
        parsed = parse_date(finish_date_text)
        if parsed is None:
            raise ValueError("调整后的目标完工日期格式不正确，请使用 YYYY-MM-DD。")
        _upsert_project_parameter(store, "P-003", parsed.isoformat(), "date", "目标完工日期由用户调整。")
        changed_parameters += 1
    if total_days_text:
        total_days = _parse_positive_int(total_days_text, field="总工期")
        _upsert_project_parameter(store, "P-001", str(total_days), "day", "总工期由用户调整。")
        changed_parameters += 1
    if parameter_id or parameter_value:
        if not parameter_id or not parameter_value:
            raise ValueError("自定义参数需要同时填写参数编号和参数值。")
        _upsert_project_parameter(
            store,
            parameter_id,
            parameter_value,
            parameter_unit,
            "项目参数由用户在 Web 调整页面修改。",
        )
        changed_parameters += 1

    changed_tasks = 0
    changed_resources = 0
    task_adjusted = False
    if task_id:
        changed_tasks += _adjust_task_fields(
            store,
            task_id=task_id,
            duration_delta=duration_delta,
            duration_set=duration_set,
            predecessor_ids=predecessor_ids,
            relation_type=relation_type,
            lag_days=lag_days,
        )
        task_adjusted = True
    elif any(
        value not in ("", 0, None)
        for value in (duration_delta, duration_set, predecessor_ids, relation_type, lag_days)
    ):
        raise ValueError("调整任务工期或前置关系时，必须填写 task_id。")

    if resource_task_id or resource_name:
        changed_resources = _adjust_resource_fields(
            store,
            task_id=resource_task_id,
            resource_name=resource_name,
            demand=resource_demand,
            capacity=resource_capacity,
        )
    elif resource_demand is not None or resource_capacity is not None:
        raise ValueError("调整资源时，必须填写资源对应的 task_id 或资源名称。")

    event_id = ""
    if event_type:
        preset = EVENT_PRESETS.get(event_type)
        if preset is None:
            raise ValueError("请选择有效的特殊事件类型。")
        if impact_days == 0:
            impact_days = int(preset["default_impact_days"])
        event_id = _append_event_log(
            store,
            event_type=event_type,
            related_task=related_task,
            impact_days=impact_days,
            note=event_note,
        )
        _append_adjustment_plan(store, event_id, preset, impact_days)
        changed_tasks = _delay_wbs_tasks(
            store,
            related_task=related_task,
            impact_days=impact_days,
            event_label=str(preset["label"]),
        )

    if not any([changed_start, changed_parameters, event_id, task_adjusted, changed_resources]):
        raise ValueError("请至少填写一个调整项。")

    parts = []
    if changed_start:
        parts.append(f"开工日期已调整为 {changed_start}")
    if changed_parameters:
        parts.append(f"已调整 {changed_parameters} 个项目参数")
    if event_id:
        parts.append(f"已记录特殊事件并调整 {changed_tasks} 个任务")
    elif task_adjusted:
        parts.append(f"已调整 {changed_tasks} 个任务")
    if changed_resources:
        parts.append(f"已调整 {changed_resources} 条资源配置")
    return AdjustmentResult(
        message="；".join(parts) + "。请点击“重新计算”生成新的计划。",
        changed_tasks=changed_tasks,
        new_start_date=changed_start or current_project_start_date(store),
    )


def _upsert_project_start_date(store: ExcelBlackboardStore, start_date: date) -> None:
    _upsert_project_parameter(
        store,
        "P-002",
        start_date.isoformat(),
        "date",
        "开工日期由用户在 Web 调整页面修改。",
    )


def _upsert_project_parameter(
    store: ExcelBlackboardStore,
    parameter_id: str,
    value: str,
    unit: str,
    note: str,
) -> None:
    rows = store.read_rows("project_parameters")
    now = _now()
    updated = False
    for row in rows:
        if str(row.get("parameter_id") or "") == parameter_id:
            row.update(
                {
                    "value": value,
                    "unit": unit,
                    "source": "web_adjustment",
                    "extraction_status": "manual_override",
                    "confidence": "1.00",
                    "confirmed_by": "web_user",
                    "updated_at": now,
                    "created_by": row.get("created_by") or "web_user",
                    "note": note,
                }
            )
            updated = True
            break
    if not updated:
        rows.append(
            {
                "parameter_id": parameter_id,
                "value": value,
                "unit": unit,
                "source": "web_adjustment",
                "evidence_id": "",
                "extraction_status": "manual_override",
                "confidence": "1.00",
                "confirmed_by": "web_user",
                "updated_at": now,
                "created_by": "web_user",
                "note": note,
            }
        )
    store.replace_rows("project_parameters", rows)


def _append_event_log(
    store: ExcelBlackboardStore,
    *,
    event_type: str,
    related_task: str,
    impact_days: int,
    note: str,
) -> str:
    existing = store.read_rows("event_log")
    event_id = f"WEB-EVT-{len(existing) + 1:04d}"
    preset = EVENT_PRESETS[event_type]
    store.append_row(
        "event_log",
        {
            "event_id": event_id,
            "event_type": preset["label"],
            "related_task": related_task or "ALL",
            "impact_days": impact_days,
            "priority": "high" if impact_days >= 3 else "medium",
            "status": "applied",
            "created_at": _now(),
            "created_by": "web_user",
            "source": "web_adjustment",
            "confidence": "1.00",
            "note": note or str(preset["measure"]),
        },
    )
    return event_id


def _append_adjustment_plan(
    store: ExcelBlackboardStore,
    event_id: str,
    preset: dict[str, Any],
    impact_days: int,
) -> None:
    existing = store.read_rows("adjustment_plan")
    store.append_row(
        "adjustment_plan",
        {
            "plan_id": f"WEB-ADJ-{len(existing) + 1:04d}",
            "event_id": event_id,
            "measure": preset["measure"],
            "recovered_days": 0,
            "cost_level": "low",
            "risk_level": preset["risk_level"],
            "score": max(1, 100 - impact_days * 5),
            "selected_flag": "yes",
            "source": "web_adjustment",
            "confidence": "1.00",
            "note": f"按用户输入影响 {impact_days} 天执行确定性重排。",
            "created_by": "web_user",
            "created_at": _now(),
        },
    )


def _delay_wbs_tasks(
    store: ExcelBlackboardStore,
    *,
    related_task: str,
    impact_days: int,
    event_label: str,
) -> int:
    if impact_days <= 0:
        return 0
    rows = store.read_rows("wbs_tasks_final")
    targets = _target_task_ids(rows, related_task)
    changed = 0
    for row in rows:
        task_id = str(row.get("task_id") or "").strip()
        if task_id not in targets:
            continue
        try:
            duration = int(float(row.get("duration_days") or 0))
        except (TypeError, ValueError):
            continue
        row["duration_days"] = max(1, duration + impact_days)
        existing_note = str(row.get("note") or "").strip()
        addition = f"{event_label}影响，工期增加{impact_days}天。"
        row["note"] = f"{existing_note} {addition}".strip()
        row["source"] = "web_adjustment"
        row["owner_agent"] = "web_user"
        row["confidence"] = row.get("confidence") or "1.00"
        changed += 1
    if changed:
        store.replace_rows("wbs_tasks_final", rows)
    return changed


def _target_task_ids(rows: list[dict[str, Any]], related_task: str) -> set[str]:
    related_task = related_task.strip()
    if not related_task:
        return {str(row.get("task_id") or "").strip() for row in rows if row.get("task_id")}
    lowered = related_task.lower()
    matches = set()
    for row in rows:
        task_id = str(row.get("task_id") or "").strip()
        task_name = str(row.get("task_name") or "").strip().lower()
        phase = str(row.get("phase") or "").strip().lower()
        if related_task == task_id or lowered in task_name or lowered in phase:
            matches.add(task_id)
    if not matches:
        raise ValueError("没有找到匹配的受影响任务。可留空表示影响全部任务，或输入 task_id。")
    return matches


def _adjust_task_fields(
    store: ExcelBlackboardStore,
    *,
    task_id: str,
    duration_delta: int,
    duration_set: int,
    predecessor_ids: str,
    relation_type: str,
    lag_days: int,
) -> int:
    rows = store.read_rows("wbs_tasks_final")
    changed = 0
    valid_relation = {"", "FS", "SS"}
    if relation_type not in valid_relation:
        raise ValueError("前置关系类型只能填写 FS 或 SS。")
    for row in rows:
        if str(row.get("task_id") or "").strip() != task_id:
            continue
        if duration_set:
            row["duration_days"] = duration_set
        elif duration_delta:
            current = int(float(row.get("duration_days") or 0))
            row["duration_days"] = max(1, current + duration_delta)
        if predecessor_ids:
            row["predecessor_ids"] = predecessor_ids
        if relation_type:
            row["relation_type"] = relation_type
        if lag_days:
            row["lag_days"] = lag_days
        row["source"] = "web_adjustment"
        row["owner_agent"] = "web_user"
        row["confidence"] = row.get("confidence") or "1.00"
        existing_note = str(row.get("note") or "").strip()
        row["note"] = f"{existing_note} 用户调整任务参数。".strip()
        changed = 1
        break
    if not changed:
        raise ValueError(f"没有找到 task_id={task_id} 的任务。")
    store.replace_rows("wbs_tasks_final", rows)
    return changed


def _adjust_resource_fields(
    store: ExcelBlackboardStore,
    *,
    task_id: str,
    resource_name: str,
    demand: float | None,
    capacity: float | None,
) -> int:
    rows = store.read_rows("resource_plan_final")
    changed = 0
    for row in rows:
        row_task = str(row.get("task_id") or "").strip()
        row_resource = str(row.get("resource_name") or "").strip()
        if task_id and row_task != task_id:
            continue
        if resource_name and row_resource != resource_name:
            continue
        if demand is not None:
            row["demand"] = demand
        if capacity is not None:
            row["capacity"] = capacity
        try:
            row["conflict_flag"] = float(row.get("demand") or 0) > float(row.get("capacity") or 0)
        except (TypeError, ValueError):
            row["conflict_flag"] = False
        row["source"] = "web_adjustment"
        row["owner_agent"] = "web_user"
        row["confidence"] = row.get("confidence") or "1.00"
        changed += 1
    if not changed:
        raise ValueError("没有找到匹配的资源配置。")
    store.replace_rows("resource_plan_final", rows)
    return changed


def _parse_int(value: Any, *, field: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"{field}必须是数字。") from exc


def _parse_positive_int(value: Any, *, field: str) -> int:
    number = _parse_int(value, field=field)
    if number <= 0:
        raise ValueError(f"{field}必须大于 0。")
    return number


def _parse_non_negative_int(value: Any, *, field: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        number = int(float(text))
    except ValueError as exc:
        raise ValueError(f"{field}必须是数字。") from exc
    if number < 0:
        raise ValueError(f"{field}不能小于 0。")
    return number


def _parse_optional_float(value: Any, *, field: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"{field}必须是数字。") from exc
    if number < 0:
        raise ValueError(f"{field}不能小于 0。")
    return number


def parse_date(value: str) -> date | None:
    match = re.search(r"([0-9]{4})[-年/.]([0-9]{1,2})[-月/.]([0-9]{1,2})", value)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
