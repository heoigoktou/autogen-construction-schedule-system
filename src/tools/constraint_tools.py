"""Constraint checking tools for programmer C Agents."""

from __future__ import annotations

from typing import Any

from tools.parameter_tools import now_iso

PASS = "通过"
WARNING = "警告"
FAIL = "未通过"


def normalize_check_result(result: str) -> str:
    """Normalize constraint result text."""

    allowed = {PASS, WARNING, FAIL}
    return result if result in allowed else WARNING


def check_constraints(
    wbs_rows: list[dict[str, Any]], resource_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Check schedule, process, resource, and site constraints."""

    created_at = now_iso()
    checks: list[dict[str, Any]] = []
    total_duration = sum(
        int(row.get("duration_estimate") or row.get("duration_days") or 0) for row in wbs_rows
    )
    checks.append(
        {
            "check_id": "CHK-0001",
            "target_type": "schedule",
            "target_id": "agentchat_wbs_tasks_final",
            "rule_id": "contract.duration.720d",
            "result": WARNING if total_duration > 720 else PASS,
            "severity": "medium" if total_duration > 720 else "low",
            "suggestion": (
                f"AgentChat WBS 持续时间直接求和为 {total_duration} 天，"
                "应以 schedule_initial/cpm_analysis 的 CPM 结果作为正式总工期依据"
            ),
            "created_by": "constraint_checker_agent",
            "created_at": created_at,
        }
    )

    has_curing_task = any("养护" in str(row.get("task_name") or "") for row in wbs_rows)
    checks.append(
        {
            "check_id": "CHK-0002",
            "target_type": "process",
            "target_id": "concrete_curing",
            "rule_id": "process.technical.interval",
            "result": PASS if has_curing_task else FAIL,
            "severity": "low" if has_curing_task else "high",
            "suggestion": (
                "已包含混凝土养护及拆模工序" if has_curing_task else "需补充混凝土养护技术间歇"
            ),
            "created_by": "constraint_checker_agent",
            "created_at": created_at,
        }
    )

    conflicts = [row for row in resource_rows if row.get("conflict_flag") is True]
    checks.append(
        {
            "check_id": "CHK-0003",
            "target_type": "resource",
            "target_id": "resource_plan",
            "rule_id": "resource.limit.not_exceeded",
            "result": FAIL if conflicts else PASS,
            "severity": "high" if conflicts else "low",
            "suggestion": (
                f"发现 {len(conflicts)} 项资源超限，建议资源平滑或追加临时资源"
                if conflicts
                else "AgentChat 资源需求未超过已记录容量"
            ),
            "created_by": "constraint_checker_agent",
            "created_at": created_at,
        }
    )

    narrow_site_tasks = [
        row for row in wbs_rows if str(row.get("phase") or "") in {"基坑支护及土方", "室外工程"}
    ]
    checks.append(
        {
            "check_id": "CHK-0004",
            "target_type": "site",
            "target_id": "urban_village_constraints",
            "rule_id": "site.narrow_and_resident_control",
            "result": WARNING if narrow_site_tasks else PASS,
            "severity": "medium" if narrow_site_tasks else "low",
            "suggestion": "基坑土方和室外工程需校核城中村场地狭小、扰民和夜间运输限制",
            "created_by": "constraint_checker_agent",
            "created_at": created_at,
        }
    )
    return checks
