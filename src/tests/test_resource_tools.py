from tools.resource_tools import build_resource_load


def _resource(task_id: str, demand: int = 10, capacity: int = 10) -> dict[str, object]:
    return {
        "task_id": task_id,
        "resource_type": "labor",
        "resource_name": "structure crew",
        "demand": demand,
        "unit": "worker",
        "capacity": capacity,
        "period": "standard flow",
    }


def _schedule(task_id: str, start: str, finish: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_name": task_id,
        "phase": "structure",
        "planned_start": start,
        "planned_finish": finish,
        "duration_days": 1,
        "predecessor_ids": "",
        "total_float": 0,
        "is_critical": False,
        "resource_summary": "",
        "status": "planned",
    }


def test_resource_load_uses_schedule_dates_for_sequential_flow_tasks() -> None:
    rows = build_resource_load(
        [_resource("TASK-001"), _resource("TASK-002")],
        [
            _schedule("TASK-001", "2026-01-01", "2026-01-10"),
            _schedule("TASK-002", "2026-01-11", "2026-01-20"),
        ],
    )

    assert len(rows) == 1
    assert rows[0]["date_or_period"] == "2026-01"
    assert rows[0]["load_rate"] == 1
    assert rows[0]["conflict_flag"] is False


def test_resource_load_marks_real_overlaps_as_conflicts() -> None:
    rows = build_resource_load(
        [_resource("TASK-001"), _resource("TASK-002")],
        [
            _schedule("TASK-001", "2026-01-01", "2026-01-31"),
            _schedule("TASK-002", "2026-01-01", "2026-01-31"),
        ],
    )

    assert len(rows) == 1
    assert rows[0]["demand"] == 20
    assert rows[0]["load_rate"] == 2
    assert rows[0]["conflict_flag"] is True
