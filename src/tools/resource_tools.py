"""Resource post-processing tools for AgentChat-produced resource rows."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any


def has_resource_conflict(demand: float, limit: float) -> bool:
    """Return whether resource demand exceeds the configured limit."""

    return demand > limit


def build_resource_load(
    resource_rows: list[dict[str, Any]],
    schedule_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate existing resource rows into load-chart data.

    This function does not infer resources from WBS tasks. It only summarizes
    rows already produced from source documents or AgentChat model inference.
    """

    if schedule_rows:
        scheduled_rows = _build_scheduled_resource_load(resource_rows, schedule_rows)
        if scheduled_rows:
            return scheduled_rows

    return _build_period_resource_load(resource_rows)


def _build_period_resource_load(resource_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _build_scheduled_resource_load(
    resource_rows: list[dict[str, Any]],
    schedule_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    schedule_by_task = {
        str(row.get("task_id") or "").strip(): row
        for row in schedule_rows
        if row.get("task_id")
    }
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    related: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    for row in resource_rows:
        task_id = str(row.get("task_id") or "").strip()
        resource_type = str(row.get("resource_type") or "").strip()
        resource_name = str(row.get("resource_name") or "").strip()
        if not task_id or not resource_type or not resource_name:
            continue
        schedule = schedule_by_task.get(task_id)
        if not schedule:
            continue
        start = _parse_date(schedule.get("planned_start"))
        finish = _parse_date(schedule.get("planned_finish"))
        if not start or not finish:
            continue
        if finish < start:
            start, finish = finish, start

        demand = _to_float(row.get("demand"))
        capacity = _to_float(row.get("capacity"))
        if demand <= 0:
            continue

        for bucket_start, bucket_end, label in _month_buckets(start, finish):
            overlap_start = max(start, bucket_start)
            overlap_end = min(finish, bucket_end)
            if overlap_end < overlap_start:
                continue
            key = (label, resource_type, resource_name)
            if key not in grouped:
                grouped[key] = {
                    "date_or_period": label,
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "demand": 0.0,
                    "capacity": capacity,
                    "load_rate": 0.0,
                    "conflict_flag": False,
                    "related_tasks": "",
                    "_intervals": [],
                }
            grouped[key]["_intervals"].append((overlap_start, overlap_end, demand))
            grouped[key]["capacity"] = max(float(grouped[key]["capacity"]), capacity)
            related[key].add(task_id)

    output = []
    for key, row in grouped.items():
        capacity = float(row["capacity"])
        demand = _peak_demand(row.pop("_intervals", []))
        row["demand"] = round(demand, 2)
        row["capacity"] = round(capacity, 2)
        row["load_rate"] = round(demand / capacity, 2) if capacity else 0
        row["conflict_flag"] = demand > capacity if capacity else False
        row["related_tasks"] = ",".join(sorted(related[key]))
        output.append(row)
    return sorted(
        output,
        key=lambda item: (
            item["date_or_period"],
            item["resource_type"],
            item["resource_name"],
        ),
    )


def _peak_demand(intervals: list[tuple[date, date, float]]) -> float:
    events: dict[date, float] = defaultdict(float)
    for start, finish, demand in intervals:
        events[start] += demand
        events[finish + timedelta(days=1)] -= demand
    current = 0.0
    peak = 0.0
    for day in sorted(events):
        current += events[day]
        peak = max(peak, current)
    return peak


def _month_buckets(start: date, finish: date) -> list[tuple[date, date, str]]:
    buckets = []
    year = start.year
    month = start.month
    while (year, month) <= (finish.year, finish.month):
        last_day = calendar.monthrange(year, month)[1]
        bucket_start = date(year, month, 1)
        bucket_end = date(year, month, last_day)
        buckets.append((bucket_start, bucket_end, f"{year:04d}-{month:02d}"))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return buckets


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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
