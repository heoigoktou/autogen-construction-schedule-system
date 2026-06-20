"""Event topic constants and default subscribers."""

from __future__ import annotations

PARAMETER_CHECK_REQUESTED = "parameter.check.requested"
SOURCE_DOCUMENTS_PARSED = "source.documents.parsed"
PARAMETER_MISSING_DETECTED = "parameter.missing.detected"
WBS_GENERATED = "wbs.generated"
RESOURCE_CONFLICT_DETECTED = "resource.conflict.detected"
CONSTRAINT_VIOLATION_DETECTED = "constraint.violation.detected"
DYNAMIC_EVENT_RECEIVED = "dynamic.event.received"
ADJUSTMENT_PLAN_PROPOSED = "adjustment.plan.proposed"
ARBITRATION_COMPLETED = "arbitration.completed"

DEFAULT_SUBSCRIBERS: dict[str, list[str]] = {
    SOURCE_DOCUMENTS_PARSED: [
        "coordinator_agent",
        "data_parser_agent",
        "wbs_planner_agent",
        "resource_allocator_agent",
    ],
    PARAMETER_CHECK_REQUESTED: ["data_parser_agent", "coordinator_agent"],
    PARAMETER_MISSING_DETECTED: [
        "data_parser_agent",
        "wbs_planner_agent",
        "constraint_checker_agent",
    ],
    WBS_GENERATED: [
        "resource_allocator_agent",
        "constraint_checker_agent",
        "coordinator_agent",
    ],
    RESOURCE_CONFLICT_DETECTED: [
        "resource_allocator_agent",
        "constraint_checker_agent",
        "plan_arbiter_agent",
    ],
    CONSTRAINT_VIOLATION_DETECTED: ["coordinator_agent"],
    DYNAMIC_EVENT_RECEIVED: [
        "dynamic_responder_agent",
        "resource_allocator_agent",
        "constraint_checker_agent",
    ],
    ADJUSTMENT_PLAN_PROPOSED: [
        "plan_arbiter_agent",
        "constraint_checker_agent",
        "coordinator_agent",
    ],
    ARBITRATION_COMPLETED: [
        "coordinator_agent",
        "data_parser_agent",
        "wbs_planner_agent",
        "resource_allocator_agent",
        "constraint_checker_agent",
        "dynamic_responder_agent",
        "plan_arbiter_agent",
    ],
}


def subscribers_for(event_type: str) -> list[str]:
    """Return default subscribers for an event."""

    return DEFAULT_SUBSCRIBERS.get(event_type, [])
