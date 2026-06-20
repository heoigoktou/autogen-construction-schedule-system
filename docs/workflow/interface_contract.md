# Interface Contract

This project uses an Excel workbook as the shared blackboard for all formal
agent data exchange. It does not use SQL, SQLite, or an external database.

Default workbook:

```text
data/blackboard/demo_blackboard.xlsx
```

The production real-case workflow writes to:

```text
data/blackboard/real_case_blackboard.xlsx
```

Required worksheets:

- `parameter_checklist`
- `project_parameters`
- `wbs_tasks`
- `wbs_tasks_final`
- `resource_plan`
- `resource_plan_final`
- `resource_load_daily`
- `resource_resolution`
- `schedule_initial`
- `cpm_analysis`
- `network_edges`
- `milestone_check`
- `constraint_check`
- `event_log`
- `agent_message_log`
- `adjustment_plan`
- `debug_records`
- `test_case_results`
- `test_results`

Field definitions are centralized in `src/blackboard/sheet_schema.py`.

## Message Shape

```json
{
  "message_id": "MSG-20260609-0001",
  "timestamp": "2026-06-09T10:00:00+08:00",
  "sender": "dynamic_responder_agent",
  "receiver": "resource_allocator_agent",
  "mode": "direct",
  "event_type": "resource.shortage.detected",
  "priority": "high",
  "related_sheet": "event_log",
  "related_id": "EVT-0001",
  "payload": {
    "summary": "Resource shortage detected; evaluate schedule impact.",
    "required_response": "resource_adjustment_options"
  },
  "status": "pending"
}
```

## Agent Result Shape

```json
{
  "agent": "wbs_planner_agent",
  "status": "success",
  "summary": "WBS rows were generated and validated.",
  "written_sheets": ["wbs_tasks_final"],
  "messages": ["MSG-20260609-0008"],
  "needs_human_confirmation": false,
  "warnings": []
}
```

## Production Rules

- `wbs_tasks_final`, `resource_plan_final`, `event_log`, and `adjustment_plan`
  must include evidence fields such as `source`, `confidence`, or `note`.
- The real-case entrypoint must not fabricate default checklists, fixed WBS
  rows, sample events, or preset adjustment plans.
- Final AgentChat output must start with `FINAL_SCHEDULE_READY` and include one
  JSON object.
- Runtime validation checks required fields, duplicate task ids, missing
  predecessors, CPM cycles, invalid dates, resource references, and evidence
  fields before writing final tables.
