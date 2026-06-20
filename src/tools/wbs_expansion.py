"""Parameter-driven WBS expansion helpers."""

from __future__ import annotations

import re
from typing import Any

from tools.parameter_tools import now_iso

TARGET_MIN_TASKS = 40
FATAL_MIN_TASKS = 20


def maybe_expand_segmented_wbs(
    wbs_rows: list[dict[str, Any]],
    project_parameters: list[dict[str, Any]],
    assumption_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Return a segmented WBS when high-rise basement output is too coarse."""

    above_floors = _parameter_int(project_parameters, "P-012")
    basement_floors = _parameter_int(project_parameters, "P-013")
    if (above_floors or 0) < 10 or (basement_floors or 0) < 1:
        return wbs_rows, False
    if len(wbs_rows) >= FATAL_MIN_TASKS:
        return wbs_rows, False

    total_days = _parameter_int(project_parameters, "P-001") or _sum_duration(wbs_rows) or 600
    rows = _build_segmented_rows(
        above_floors=above_floors or 10,
        basement_floors=basement_floors or 1,
        total_days=max(total_days, TARGET_MIN_TASKS),
    )
    if assumption_rows is not None:
        assumption_rows.append(
            {
                "assumption_id": f"ASM-{len(assumption_rows) + 1:04d}",
                "target_type": "wbs_tasks_final",
                "target_id": "AUTO_SEGMENTED_WBS",
                "assumption": (
                    "WBS auto-expanded because agent output was below the relaxed "
                    f"{FATAL_MIN_TASKS}-task floor for a high-rise basement project."
                ),
                "basis": f"above_floors={above_floors}, basement_floors={basement_floors}, total_days={total_days}",
                "risk_level": "medium",
                "status": "active",
                "created_by": "wbs_expansion",
                "created_at": now_iso(),
            }
        )
    return rows, True


def _build_segmented_rows(
    *,
    above_floors: int,
    basement_floors: int,
    total_days: int,
) -> list[dict[str, Any]]:
    specs: list[tuple[str, str, str]] = [
        ("Preparation", "site", "Site handover and survey control"),
        ("Preparation", "site", "Temporary facilities and access roads"),
        ("Preparation", "site", "Temporary utilities and safety setup"),
        ("Earthwork", "basement", "Dewatering system installation"),
        ("Earthwork", "basement", "Earth retaining and support works"),
        ("Earthwork", "basement", "Bulk excavation"),
        ("Earthwork", "basement", "Foundation pit trimming and inspection"),
        ("Foundation", "basement", "Cushion concrete and waterproof base"),
        ("Foundation", "basement", "Raft reinforcement and formwork"),
        ("Foundation", "basement", "Raft concrete placement"),
    ]
    for basement in range(basement_floors, 0, -1):
        area = f"B{basement}"
        specs.extend(
            [
                ("Basement structure", area, f"{area} vertical structure"),
                ("Basement structure", area, f"{area} slab reinforcement and formwork"),
                ("Basement structure", area, f"{area} slab concrete"),
            ]
        )
    floor_bands = _floor_bands(above_floors, band_size=5)
    for band in floor_bands:
        specs.extend(
            [
                ("Main structure", band, f"Main structure vertical members {band}"),
                ("Main structure", band, f"Main structure slab works {band}"),
                ("Main structure", band, f"Concrete curing and turnover {band}"),
            ]
        )
    for band in floor_bands:
        specs.append(("Secondary structure", band, f"Masonry and secondary structure {band}"))
    specs.extend(
        [
            ("Roof", "roof", "Roof structure and waterproofing"),
            ("Roof", "roof", "Roof insulation and protection layer"),
        ]
    )
    for band in floor_bands:
        specs.append(("MEP rough-in", band, f"MEP rough-in {band}"))
    for band in floor_bands:
        specs.append(("Finishes", band, f"Interior finishes {band}"))
    specs.extend(
        [
            ("Facade", "facade", "External wall insulation and facade"),
            ("MEP commissioning", "whole building", "Electrical system installation and testing"),
            ("MEP commissioning", "whole building", "Plumbing and drainage testing"),
            ("MEP commissioning", "whole building", "Fire protection and HVAC commissioning"),
            ("Outdoor works", "site", "Outdoor utilities and roads"),
            ("Acceptance", "whole building", "Cleaning and pre-acceptance rectification"),
            ("Acceptance", "whole building", "Completion acceptance and handover"),
        ]
    )

    durations = _spread_duration(total_days, len(specs))
    rows: list[dict[str, Any]] = []
    for index, ((phase, area, name), duration) in enumerate(zip(specs, durations), start=1):
        task_id = f"WBS-{index:03d}"
        predecessor = f"WBS-{index - 1:03d}" if index > 1 else ""
        rows.append(
            {
                "task_id": task_id,
                "wbs_code": f"1.{index:03d}",
                "phase": phase,
                "section": area,
                "floor_or_area": area,
                "task_name": name,
                "work_package": phase,
                "quantity": "",
                "unit": "",
                "duration_days": duration,
                "predecessor_ids": predecessor,
                "relation_type": "FS",
                "lag_days": 0,
                "source": "parameter-driven segmented WBS fallback",
                "confidence": "0.70",
                "note": "Auto-expanded from extracted floor/basement/duration parameters.",
                "owner_agent": "wbs_planner_agent",
            }
        )
    return rows


def _floor_bands(above_floors: int, *, band_size: int) -> list[str]:
    bands = []
    start = 1
    while start <= above_floors:
        end = min(start + band_size - 1, above_floors)
        bands.append(f"F{start}-F{end}" if start != end else f"F{start}")
        start = end + 1
    return bands


def _spread_duration(total_days: int, count: int) -> list[int]:
    base = max(total_days // max(count, 1), 1)
    durations = [base for _ in range(count)]
    remaining = max(total_days - base * count, 0)
    index = 0
    while remaining > 0 and durations:
        durations[index % len(durations)] += 1
        index += 1
        remaining -= 1
    return durations


def _parameter_int(project_parameters: list[dict[str, Any]], parameter_id: str) -> int | None:
    for row in project_parameters:
        if str(row.get("parameter_id") or "") != parameter_id:
            continue
        match = re.search(r"\d+(?:\.\d+)?", str(row.get("value") or row.get("note") or ""))
        if match:
            return int(round(float(match.group(0))))
    return None


def _sum_duration(wbs_rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in wbs_rows:
        try:
            total += int(row.get("duration_days") or row.get("duration_estimate") or 0)
        except (TypeError, ValueError):
            continue
    return total
