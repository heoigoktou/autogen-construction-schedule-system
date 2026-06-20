from __future__ import annotations


def minimal_parameter_checklist() -> list[dict[str, object]]:
    rows = [
        ("P-001", "project_boundary", "total_duration", "30 days", "data_parser_agent"),
        ("P-002", "project_boundary", "start_date", "2026-03-01", "data_parser_agent"),
        ("P-003", "project_boundary", "finish_date", "2026-03-30", "data_parser_agent"),
        ("P-011", "project_scale", "building_area", "3000 m2", "data_parser_agent"),
        ("P-012", "project_scale", "aboveground_floors", "3 floors", "wbs_planner_agent"),
        ("P-013", "project_scale", "basement_floors", "0 floors", "wbs_planner_agent"),
        ("P-015", "technical_boundary", "structure_type", "frame", "wbs_planner_agent"),
    ]
    return [
        {
            "parameter_id": parameter_id,
            "category": category,
            "name": name,
            "required": "yes",
            "source": "pytest source document",
            "status": "source_exact",
            "extraction_status": "source_exact",
            "owner_agent": owner_agent,
            "value": value,
            "note": value,
            "created_by": "pytest",
            "created_at": "2026-01-01T00:00:00+08:00",
        }
        for parameter_id, category, name, value, owner_agent in rows
    ]


def minimal_wbs_rows() -> list[dict[str, object]]:
    return [
        {
            "task_id": "TASK-0001",
            "wbs_code": "01.01.001",
            "phase": "preparation",
            "section": "site",
            "floor_or_area": "site",
            "task_name": "site preparation",
            "work_package": "preparation",
            "quantity": 1,
            "unit": "item",
            "duration_days": 3,
            "predecessor_ids": "",
            "relation_type": "FS",
            "lag_days": 0,
            "source": "pytest source document",
            "confidence": "0.90",
            "note": "source identifies preparation work",
            "owner_agent": "wbs_planner_agent",
        },
        {
            "task_id": "TASK-0002",
            "wbs_code": "02.01.001",
            "phase": "foundation",
            "section": "foundation",
            "floor_or_area": "foundation",
            "task_name": "foundation work",
            "work_package": "foundation",
            "quantity": 1,
            "unit": "item",
            "duration_days": 5,
            "predecessor_ids": "TASK-0001",
            "relation_type": "FS",
            "lag_days": 0,
            "source": "model_inference+source_context",
            "confidence": "0.80",
            "note": "inferred from construction sequence",
            "owner_agent": "wbs_planner_agent",
        },
        {
            "task_id": "TASK-0003",
            "wbs_code": "03.01.001",
            "phase": "structure",
            "section": "superstructure",
            "floor_or_area": "1F",
            "task_name": "concrete structure work",
            "work_package": "structure",
            "quantity": 1,
            "unit": "item",
            "duration_days": 7,
            "predecessor_ids": "TASK-0002",
            "relation_type": "FS",
            "lag_days": 0,
            "source": "model_inference+source_context",
            "confidence": "0.80",
            "note": "inferred from construction sequence",
            "owner_agent": "wbs_planner_agent",
        },
    ]


def minimal_resource_rows() -> list[dict[str, object]]:
    return [
        {
            "task_id": "TASK-0001",
            "resource_type": "labor",
            "resource_name": "general crew",
            "demand": 10,
            "unit": "worker",
            "capacity": 20,
            "period": "2026-W09",
            "conflict_flag": False,
            "source": "pytest source document",
            "confidence": "0.90",
            "note": "source identifies general crew resource",
            "owner_agent": "resource_allocator_agent",
        },
        {
            "task_id": "TASK-0002",
            "resource_type": "machine",
            "resource_name": "excavator",
            "demand": 3,
            "unit": "unit",
            "capacity": 2,
            "period": "2026-W10",
            "conflict_flag": True,
            "source": "model_inference+source_context",
            "confidence": "0.80",
            "note": "capacity conflict inferred from demand and capacity",
            "owner_agent": "resource_allocator_agent",
        },
    ]


def minimal_event_rows() -> list[dict[str, object]]:
    return [
        {
            "event_id": "EVT-0001",
            "event_type": "resource.capacity_risk",
            "related_task": "TASK-0002",
            "impact_days": 2,
            "priority": "high",
            "status": "generated",
            "created_at": "2026-01-01T00:00:00+08:00",
            "created_by": "dynamic_responder_agent",
            "source": "model_inference+source_context",
            "confidence": "0.75",
            "note": "risk event inferred from resource capacity conflict",
        }
    ]


def minimal_adjustment_rows() -> list[dict[str, object]]:
    return [
        {
            "plan_id": "PLAN-0001",
            "event_id": "EVT-0001",
            "measure": "stagger foundation resources",
            "recovered_days": 2,
            "cost_level": "medium",
            "risk_level": "low",
            "score": 85,
            "selected_flag": True,
            "source": "model_inference+source_context",
            "confidence": "0.75",
            "note": "staggering is inferred from resource conflict",
            "created_by": "plan_arbiter_agent",
            "created_at": "2026-01-01T00:00:00+08:00",
        }
    ]
