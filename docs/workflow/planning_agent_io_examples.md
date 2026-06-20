# Planning Agent IO Examples

Production scheduling runs through AutoGen AgentChat. The legacy router agents
remain available for communication and status checks; they must not fabricate
WBS rows, resources, events, or adjustment plans.

## Data Parser Agent

- Agent: `data_parser_agent`
- Runtime role: read source context and deterministic parameter candidates, then
  write `parameter_checklist` and `project_parameters` draft rows through
  `write_blackboard_table`.
- Required evidence: `source`, `confidence`, and `note`.

```json
{
  "agent": "data_parser_agent",
  "status": "success",
  "summary": "Parameter candidates were extracted from source documents.",
  "written_sheets": ["parameter_checklist", "project_parameters"],
  "messages": ["MSG-20260610-XXXX"],
  "needs_human_confirmation": false,
  "warnings": []
}
```

## WBS Planner Agent

- Agent: `wbs_planner_agent`
- Runtime role: generate non-template `wbs_tasks_final` rows from real
  documents, extracted parameters, and model inference.
- Required checks: unique `task_id` and `wbs_code`, valid predecessors, positive
  `duration_days`, and evidence fields.

```json
{
  "agent": "wbs_planner_agent",
  "status": "success",
  "summary": "WBS draft rows were generated from project evidence.",
  "written_sheets": ["wbs_tasks_final"],
  "messages": ["MSG-20260610-XXXX"],
  "needs_human_confirmation": false,
  "warnings": []
}
```

## Resource Allocator Agent

- Agent: `resource_allocator_agent`
- Runtime role: read `wbs_tasks_final`, infer `resource_plan_final` rows, and
  flag conflicts from demand and capacity.
- Required evidence: `source`, `confidence`, `note`, and valid `task_id`
  references.

```json
{
  "agent": "resource_allocator_agent",
  "status": "success",
  "summary": "Resource demand rows were generated and capacity conflicts were flagged.",
  "written_sheets": ["resource_plan_final"],
  "messages": ["MSG-20260610-XXXX"],
  "needs_human_confirmation": true,
  "warnings": ["Resource capacity requires human review."]
}
```
