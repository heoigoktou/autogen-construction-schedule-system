# Validation Agent IO Examples

Production validation and dynamic response are driven by AutoGen AgentChat. The
demo router scripts only route existing rows and do not create sample events or
preset adjustment plans.

## Constraint Checker Agent

- Agent: `constraint_checker_agent`
- Runtime role: inspect AgentChat draft rows, validate schema, predecessor
  references, CPM feasibility, and resource references.
- Output: validation feedback to the team. Final persisted checks are produced
  after `write_agentchat_output` accepts the final JSON.

```json
{
  "agent": "constraint_checker_agent",
  "status": "success",
  "summary": "Draft rows passed schema and scheduling validation.",
  "written_sheets": ["constraint_check"],
  "messages": ["MSG-20260610-XXXX"],
  "needs_human_confirmation": false,
  "warnings": []
}
```

## Dynamic Responder Agent

- Agent: `dynamic_responder_agent`
- Runtime role: write `event_log` rows only when source context supports the
  event or risk, or when the model explicitly marks an inference.
- Required evidence: `source`, `confidence`, and `note`.

```json
{
  "agent": "dynamic_responder_agent",
  "status": "success",
  "summary": "A schedule risk event was identified from source evidence.",
  "written_sheets": ["event_log"],
  "messages": ["MSG-20260610-XXXX"],
  "needs_human_confirmation": true,
  "warnings": ["High-priority event requires coordinator review."]
}
```

## Plan Arbiter Agent

- Agent: `plan_arbiter_agent`
- Runtime role: produce `adjustment_plan` rows only from existing event and
  constraint evidence.
- If candidate plans are present, exactly one row must have
  `selected_flag=true`.

```json
{
  "agent": "plan_arbiter_agent",
  "status": "success",
  "summary": "One adjustment plan was recommended from event and constraint evidence.",
  "written_sheets": ["adjustment_plan"],
  "messages": ["MSG-20260610-XXXX"],
  "needs_human_confirmation": true,
  "warnings": ["Recommended plan needs final human approval."]
}
```
