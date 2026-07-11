"""Truthful Tool Agent audit trail for Web job runs.

This module records what the Web workflow actually did. It does not alter the
AgentChat runtime, blackboard tables, or deterministic scheduling logic.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
CN_TZ = timezone(timedelta(hours=8))
AUDIT_FILENAME = "tool_agent_audit.json"


def record_audit_event(
    context: Any,
    *,
    agent: str,
    action: str,
    status: str = "completed",
    summary: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Append one truthful Tool Agent event to JSON audit and runtime.log."""

    event = {
        "time": now_iso(),
        "agent": agent,
        "action": action,
        "status": status,
        "summary": summary,
        "details": safe_details(details or {}),
    }
    try:
        audit_path = audit_file_path(context.outputs_root)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        events = read_audit_events(audit_path)
        events.append(event)
        audit_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
        append_runtime_line(context.runtime_log, event)
    except Exception:
        LOGGER.exception("Tool Agent audit event could not be recorded.")


def record_preprocess_audit(context: Any, package: dict[str, Any]) -> None:
    """Record preprocessing and parameter-readiness checks."""

    summary = package.get("summary") or {}
    record_audit_event(
        context,
        agent="DataParserAgent",
        action="preprocess_documents",
        summary="完成上传资料预处理，并刷新标准化输入包。",
        details={
            "readiness_score": summary.get("readiness_score"),
            "missing_required_count": summary.get("missing_required_count"),
            "recognized_count": summary.get("recognized_count"),
            "source_file_count": summary.get("source_file_count"),
        },
    )
    record_audit_event(
        context,
        agent="ParameterCheckAgent",
        action="check_required_parameters",
        summary="根据标准化输入包检查必需参数完整性。",
        details={
            "readiness_score": summary.get("readiness_score"),
            "missing_required_count": summary.get("missing_required_count"),
            "status": "ready" if int(summary.get("missing_required_count") or 0) == 0 else "needs_supplement",
        },
    )


def record_table_snapshot_audit(
    context: Any,
    *,
    phase: str,
    table_counts: dict[str, Any],
) -> None:
    """Record current blackboard table counts for traceability."""

    record_audit_event(
        context,
        agent="BlackboardAgent",
        action=f"snapshot_{phase}",
        summary="记录公共黑板关键表数量，作为后续排程和复核依据。",
        details=table_counts,
    )


def record_model_workflow_audit(
    context: Any,
    *,
    run_mode: str,
    status: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record the real AgentChat stage without claiming more than happened."""

    record_audit_event(
        context,
        agent="AgentChatCoordinator",
        action=f"run_{run_mode}_workflow",
        status=status,
        summary=summary,
        details=details or {},
    )


def record_recalculation_audit(
    context: Any,
    *,
    counts: dict[str, Any],
    scenario: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    run_mode: str,
) -> None:
    """Record deterministic scheduling, CPM, resource, scenario, and QA steps."""

    record_audit_event(
        context,
        agent="ScheduleAgent",
        action="build_initial_schedule",
        summary="基于当前 WBS、资源计划和项目参数重新生成施工进度。",
        details={
            "run_mode": run_mode,
            "schedule_initial": counts.get("schedule_initial"),
            "network_edges": counts.get("network_edges"),
        },
    )
    record_audit_event(
        context,
        agent="CPMAgent",
        action="calculate_critical_path",
        summary="计算 CPM、关键线路和网络关系。",
        details={
            "cpm_analysis": counts.get("cpm_analysis"),
            "network_edges": counts.get("network_edges"),
        },
    )
    record_audit_event(
        context,
        agent="ResourceAllocatorAgent",
        action="calculate_resource_load",
        summary="计算资源负荷、冲突点和资源平衡建议。",
        details={
            "resource_load_daily": counts.get("resource_load_daily"),
            "resource_resolution": counts.get("resource_resolution"),
        },
    )
    record_audit_event(
        context,
        agent="MilestoneAgent",
        action="check_milestones_and_constraints",
        summary="检查里程碑目标和施工约束。",
        details={
            "milestone_check": counts.get("milestone_check"),
            "constraint_check": counts.get("constraint_check"),
        },
    )
    if scenario:
        record_audit_event(
            context,
            agent="ScenarioAgent",
            action="save_scenario_snapshot",
            summary="保存本次方案快照，可用于基准/扰动方案动态对比。",
            details={
                "scenario_id": scenario.get("id"),
                "scenario_name": scenario.get("name"),
                "scenario_kind": scenario.get("kind"),
            },
        )
    record_audit_event(
        context,
        agent="VisualizationAgent",
        action="export_visual_artifacts",
        summary="导出甘特图、CPM 图、资源负荷图谱和交互式看板数据。",
        details={
            "visualization_result": counts.get("visualization_result"),
        },
    )
    if quality:
        record_audit_event(
            context,
            agent="QualityReviewAgent",
            action="assess_result_quality",
            status=str(quality.get("level") or "completed"),
            summary=str(quality.get("summary") or "完成基础质量复核。"),
            details={
                "issues": quality.get("issues") or [],
                "fallback_detected": quality.get("fallback_detected"),
                "restored_tables": quality.get("restored_tables") or {},
                "table_counts": quality.get("table_counts") or {},
            },
        )


def record_restore_audit(context: Any, restored_tables: dict[str, int]) -> None:
    """Record fallback table restoration when it actually occurred."""

    if not restored_tables:
        return
    record_audit_event(
        context,
        agent="FallbackReviewAgent",
        action="restore_preserved_tables",
        status="needs_review",
        summary="检测到兜底结果后，恢复人工/标准化输入中的关键 WBS 和资源表，并标记为待复核。",
        details={"restored_tables": restored_tables},
    )


def record_job_end_audit(
    context: Any,
    *,
    status: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record final job state or failure/cancel state."""

    record_audit_event(
        context,
        agent="QualityReviewAgent",
        action="finalize_web_job",
        status=status,
        summary=summary,
        details=details or {},
    )


def audit_file_path(outputs_root: Path) -> Path:
    return outputs_root / "report_assets" / AUDIT_FILENAME


def read_audit_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def append_runtime_line(runtime_log: Path, event: dict[str, Any]) -> None:
    runtime_log.parent.mkdir(parents=True, exist_ok=True)
    details = event.get("details") or {}
    compact_details = ", ".join(
        f"{key}={value}"
        for key, value in details.items()
        if should_show_in_runtime_log(value)
    )
    suffix = f" | {compact_details}" if compact_details else ""
    line = (
        f"{event['time']} [ToolAgent] {event['agent']} "
        f"{event['action']} {event['status']}: {event['summary']}{suffix}\n"
    )
    with runtime_log.open("a", encoding="utf-8") as handle:
        handle.write(line)


def should_show_in_runtime_log(value: Any) -> bool:
    if isinstance(value, (list, dict)):
        return False
    if value in {None, ""}:
        return False
    return True


def safe_details(details: dict[str, Any]) -> dict[str, Any]:
    """Make details JSON-friendly and avoid huge blobs in logs."""

    clean: dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
        elif isinstance(value, dict):
            clean[key] = {
                str(inner_key): safe_scalar(inner_value) for inner_key, inner_value in value.items()
            }
        elif isinstance(value, list):
            clean[key] = [safe_scalar(item) for item in value[:20]]
        else:
            clean[key] = str(value)
    return clean


def safe_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")
