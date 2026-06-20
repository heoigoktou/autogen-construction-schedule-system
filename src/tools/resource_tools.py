"""Resource post-processing tools for AgentChat-produced resource rows."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def has_resource_conflict(demand: float, limit: float) -> bool:
    """Return whether resource demand exceeds the configured limit."""

    return demand > limit


def build_resource_load(resource_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate existing resource rows into load-chart data.

    This function does not infer resources from WBS tasks. It only summarizes
    rows already produced from source documents or AgentChat model inference.
    """

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    related: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in resource_rows:
        key = (
            str(row.get("period") or ""),
            str(row.get("resource_type") or ""),
            str(row.get("resource_name") or ""),
        )
        if not all(key):
            continue
        if key not in grouped:
            grouped[key] = {
                "date_or_period": key[0],
                "resource_type": key[1],
                "resource_name": key[2],
                "demand": 0.0,
                "capacity": float(row.get("capacity") or 0),
                "load_rate": 0.0,
                "conflict_flag": False,
                "related_tasks": "",
            }
        grouped[key]["demand"] += float(row.get("demand") or 0)
        grouped[key]["capacity"] = max(grouped[key]["capacity"], float(row.get("capacity") or 0))
        related[key].append(str(row.get("task_id") or ""))

    output = []
    for key, row in grouped.items():
        capacity = float(row["capacity"])
        demand = float(row["demand"])
        row["demand"] = round(demand, 2)
        row["load_rate"] = round(demand / capacity, 2) if capacity else 0
        row["conflict_flag"] = demand > capacity if capacity else False
        row["related_tasks"] = ",".join(item for item in related[key] if item)
        output.append(row)
    return sorted(
        output,
        key=lambda item: (
            item["date_or_period"],
            item["resource_type"],
            item["resource_name"],
        ),
    )


def build_resource_resolution(load_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build conflict records from existing load rows without inventing measures."""

    rows: list[dict[str, Any]] = []
    for index, conflict in enumerate(
        [row for row in load_rows if row.get("conflict_flag") is True],
        start=1,
    ):
        rows.append(
            {
                "conflict_id": f"RES-{index:04d}",
                "resource_name": conflict["resource_name"],
                "period": conflict["date_or_period"],
                "conflict_reason": (
                    f"{conflict['resource_name']} 需求 {conflict['demand']} "
                    f"超过容量 {conflict['capacity']}"
                ),
                "before_demand": conflict["demand"],
                "capacity": conflict["capacity"],
                "measure": "待 AgentChat 方案仲裁或人工确认",
                "after_demand": conflict["capacity"],
                "recovered_days": 0,
                "cost_level": "待确认",
                "owner_agent": "resource_allocator_agent",
            }
        )
    return rows
