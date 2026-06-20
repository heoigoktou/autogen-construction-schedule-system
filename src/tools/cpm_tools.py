"""Simplified CPM tools for the initial schedule workflow."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CpmEdge:
    """A precedence relationship between two tasks."""

    predecessor: str
    successor: str
    relation_type: str
    lag_days: int


def parse_predecessors(value: object) -> list[str]:
    """Parse comma-separated predecessor ids."""

    if value is None:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def build_network_edges(wbs_rows: list[dict[str, Any]]) -> list[CpmEdge]:
    """Build edge objects from final WBS rows."""

    task_ids = {str(row["task_id"]) for row in wbs_rows}
    edges: list[CpmEdge] = []
    for row in wbs_rows:
        successor = str(row["task_id"])
        relation_type = str(row.get("relation_type") or "FS").upper()
        lag_days = int(row.get("lag_days") or 0)
        for predecessor in parse_predecessors(row.get("predecessor_ids")):
            if predecessor not in task_ids:
                raise ValueError(f"Unknown predecessor {predecessor!r} for {successor!r}")
            edges.append(
                CpmEdge(
                    predecessor=predecessor,
                    successor=successor,
                    relation_type=relation_type,
                    lag_days=lag_days,
                )
            )
    return edges


def topological_order(task_ids: list[str], edges: list[CpmEdge]) -> list[str]:
    """Return a topological order or raise for cycles."""

    successors: dict[str, list[str]] = defaultdict(list)
    indegree = {task_id: 0 for task_id in task_ids}
    for edge in edges:
        successors[edge.predecessor].append(edge.successor)
        indegree[edge.successor] += 1

    queue = deque(task_id for task_id in task_ids if indegree[task_id] == 0)
    order: list[str] = []
    while queue:
        task_id = queue.popleft()
        order.append(task_id)
        for successor in successors[task_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)

    if len(order) != len(task_ids):
        cyclic = [task_id for task_id, degree in indegree.items() if degree > 0]
        raise ValueError(f"Cycle detected in WBS predecessors: {', '.join(cyclic[:8])}")
    return order


def calculate_cpm(
    wbs_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Calculate ES/EF/LS/LF/float and network edge rows."""

    task_ids = [str(row["task_id"]) for row in wbs_rows]
    durations = {str(row["task_id"]): int(row.get("duration_days") or 1) for row in wbs_rows}
    edges = build_network_edges(wbs_rows)
    order = topological_order(task_ids, edges)

    incoming: dict[str, list[CpmEdge]] = defaultdict(list)
    outgoing: dict[str, list[CpmEdge]] = defaultdict(list)
    for edge in edges:
        incoming[edge.successor].append(edge)
        outgoing[edge.predecessor].append(edge)

    es = {task_id: 0 for task_id in task_ids}
    ef = {task_id: durations[task_id] for task_id in task_ids}
    for task_id in order:
        start = 0
        for edge in incoming[task_id]:
            if edge.relation_type == "SS":
                candidate = es[edge.predecessor] + edge.lag_days
            else:
                candidate = ef[edge.predecessor] + edge.lag_days
            start = max(start, candidate)
        es[task_id] = start
        ef[task_id] = start + durations[task_id]

    project_finish = max(ef.values(), default=0)
    lf = {task_id: project_finish for task_id in task_ids}
    ls = {task_id: project_finish - durations[task_id] for task_id in task_ids}
    for task_id in reversed(order):
        for edge in outgoing[task_id]:
            if edge.relation_type == "SS":
                candidate_ls = ls[edge.successor] - edge.lag_days
                if candidate_ls < ls[task_id]:
                    ls[task_id] = candidate_ls
                    lf[task_id] = candidate_ls + durations[task_id]
            else:
                candidate_lf = ls[edge.successor] - edge.lag_days
                if candidate_lf < lf[task_id]:
                    lf[task_id] = candidate_lf
                    ls[task_id] = candidate_lf - durations[task_id]

    cpm_rows = []
    for task_id in task_ids:
        total_float = max(0, ls[task_id] - es[task_id])
        free_float = _free_float(task_id, outgoing, es, ef)
        cpm_rows.append(
            {
                "task_id": task_id,
                "es": es[task_id],
                "ef": ef[task_id],
                "ls": ls[task_id],
                "lf": lf[task_id],
                "total_float": total_float,
                "free_float": free_float,
                "is_critical": total_float == 0,
                "critical_path_no": "CP-1" if total_float == 0 else "",
            }
        )

    cpm_by_task = {row["task_id"]: row for row in cpm_rows}
    edge_rows = []
    for index, edge in enumerate(edges, 1):
        is_critical = (
            cpm_by_task[edge.predecessor]["is_critical"]
            and cpm_by_task[edge.successor]["is_critical"]
        )
        edge_rows.append(
            {
                "edge_id": f"EDGE-{index:04d}",
                "from_task_id": edge.predecessor,
                "to_task_id": edge.successor,
                "relation_type": edge.relation_type,
                "lag_days": edge.lag_days,
                "is_critical_edge": is_critical,
            }
        )
    return cpm_rows, edge_rows


def _free_float(
    task_id: str,
    outgoing: dict[str, list[CpmEdge]],
    es: dict[str, int],
    ef: dict[str, int],
) -> int:
    if not outgoing.get(task_id):
        return 0
    slacks = []
    for edge in outgoing[task_id]:
        if edge.relation_type == "SS":
            slacks.append(es[edge.successor] - es[edge.predecessor] - edge.lag_days)
        else:
            slacks.append(es[edge.successor] - ef[edge.predecessor] - edge.lag_days)
    return max(0, min(slacks))
