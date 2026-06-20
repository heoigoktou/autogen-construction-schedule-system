from pathlib import Path

from blackboard.excel_store import ExcelBlackboardStore


def test_initialize_and_append_row(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "demo_blackboard.xlsx")
    store.initialize()

    store.append_row(
        "event_log",
        {
            "event_id": "EVT-TEST-0001",
            "event_type": "weather.rainstorm",
            "related_task": "土方开挖",
            "impact_days": 7,
            "priority": "high",
            "status": "待处理",
            "created_at": "2026-06-09T10:00:00+08:00",
            "created_by": "pytest",
            "note": "smoke",
        },
    )

    rows = store.read_rows("event_log")
    assert rows[0]["event_id"] == "EVT-TEST-0001"
    assert store.read_headers("agent_message_log")[0] == "message_id"
