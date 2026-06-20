# AutoGen Schedule System

A multi-agent construction schedule planning system built with AutoGen
AgentChat. The project coordinates document parsing, parameter extraction, WBS
generation, resource allocation, constraint checking, dynamic event response,
and plan arbitration. Excel workbooks are used as the formal shared blackboard.

This repository is prepared for public release: real source documents, run
outputs, logs, blackboard workbooks, archives, and local credentials are ignored
by default.

## Capabilities

- Reads project documents from `data/input_docs/`.
- Supports `.txt`, `.md`, `.csv`, `.xlsx`, `.xlsm`, `.docx`, `.doc`, `.pdf`,
  `.dxf`, and `.dwg`.
- Generates project parameters, parameter checklists, WBS rows, resource
  demands, initial schedules, CPM analysis, network edges, milestone checks,
  constraint checks, dynamic events, and adjustment plans.
- Uses Excel blackboard workbooks as the formal data exchange layer.
- Fails fast when a live model endpoint, credentials, or source documents are
  missing. It does not fall back to fabricated production results.

## Architecture

```text
source documents
  -> document readers and deterministic extractors
  -> AutoGen SelectorGroupChat
  -> agent draft tables in memory
  -> local validation and repair loop
  -> Excel public blackboard
  -> schedule workbooks, reports, visualizations
```

The real-case workflow starts from `src/main_real_case_workflow.py`. It reads
source documents, builds source context, and creates an AutoGen
`SelectorGroupChat` team. Specialist agents do not write final Excel workbooks
directly. They call tools that write draft rows into in-memory draft tables.
Only after the final JSON passes local schema, predecessor, CPM, resource, and
evidence validation does the runtime persist results into the Excel blackboard
and output directories.

## Communication Model

The system has two communication layers:

| Layer | Purpose | Key Module |
|---|---|---|
| AgentChat collaboration | Production real-case workflow. AutoGen `SelectorGroupChat` selects which agent speaks or uses tools next. | `src/agentchat_runtime/workflow.py` |
| Lightweight message routing | Local demos, communication logs, and status checks. Supports direct, broadcast, arbitration, and log-only messages. | `src/communication/router.py` |

### AgentChat Collaboration

- Each agent has explicit tool permissions and can only write its assigned draft
  tables.
- `write_blackboard_table` updates in-memory draft tables during the run and
  returns `excel_written=false`.
- `constraint_checker_agent` checks draft issues but does not write business
  result tables.
- `coordinator_agent` reads all draft tables, triggers validation, coordinates
  repairs, and emits the final `FINAL_SCHEDULE_READY` JSON.
- If validation fails, the runtime asks the responsible agent to repair the
  draft. The coordinator may repair drafts within a configured write limit.
- After validation succeeds, `write_agentchat_output` persists official rows to
  the Excel blackboard and exports deliverables.

### Lightweight Router

The lightweight router is used for demos and traceability. It does not replace
the production AgentChat workflow.

- `direct`: point-to-point request with request/response logging.
- `broadcast`: event-topic delivery to subscribed agents.
- `arbitration`: coordinator-led conflict resolution.
- `log_only`: status record without agent execution.

Messages use the `AgentMessage` schema: `message_id`, `sender`, `receiver`,
`mode`, `event_type`, `priority`, `payload`, `status`, `related_sheet`,
`related_id`, and `timestamp`. Routed messages are written to
`agent_message_log` and can be exported to `outputs/demo/communication_log.xlsx`.

Event subscriptions are configured in `config/events.yaml`. Examples:

- `source.documents.parsed` notifies coordinator, parser, WBS, and resource
  agents.
- `wbs.generated` notifies resource, constraint, and coordinator agents.
- `resource.conflict.detected` notifies resource, constraint, and arbitration
  agents.
- `dynamic.event.received` notifies dynamic response, resource, and constraint
  agents.
- `adjustment.plan.proposed` notifies arbitration, constraint, and coordinator
  agents.

## Agent Responsibilities

| Agent | Responsibility | Main Data |
|---|---|---|
| `coordinator_agent` | Orchestrates the team, consolidates drafts, handles conflicts, runs final validation, and publishes the final JSON. | Reads all drafts; may repair target drafts; persists final output |
| `data_parser_agent` | Reads source context, extracts project parameters, and maintains the parameter checklist. | Writes `parameter_checklist`, `project_parameters` |
| `wbs_planner_agent` | Builds WBS tasks, durations, predecessor relationships, and evidence notes from source documents and parameters. | Writes `wbs_tasks_final` |
| `resource_allocator_agent` | Creates labor, equipment, and material demand rows from WBS tasks and flags capacity conflicts. | Writes `resource_plan_final` |
| `constraint_checker_agent` | Checks schema, predecessor links, CPM feasibility, resource references, evidence fields, and blocking issues. | Reads drafts; reports validation issues |
| `dynamic_responder_agent` | Extracts dynamic events from supply risks, seasonal conditions, site constraints, and schedule risks. | Writes `event_log` |
| `plan_arbiter_agent` | Generates, scores, ranks, and selects adjustment plans from event and constraint evidence. | Writes `adjustment_plan` |

These boundaries are enforced through system prompts, tool allocation, and write
permissions in `src/agentchat_runtime/workflow.py`.

## Public Blackboard

The demo blackboard path is:

```text
data/blackboard/demo_blackboard.xlsx
```

The real-case workflow writes to:

```text
data/blackboard/real_case_blackboard.xlsx
```

Core worksheets include:

- `parameter_checklist`
- `project_parameters`
- `wbs_tasks_final`
- `resource_plan_final`
- `schedule_initial`
- `cpm_analysis`
- `network_edges`
- `milestone_check`
- `constraint_check`
- `event_log`
- `adjustment_plan`
- `agent_message_log`
- `debug_records`
- `quality_gates`

To add or change fields, update `src/blackboard/sheet_schema.py` first, then
update readers, writers, validators, and tests.

## Project Layout

```text
config/                 # Path, model, agent, and event configuration
data/
  blackboard/           # Local Excel blackboards; only .gitkeep is committed
  input_docs/           # User project documents; only .gitkeep is committed
  templates/            # Commit-safe parameter checklist template
docs/workflow/          # Interface contract, runbook, and IO examples
outputs/                # Local run artifacts; only .gitkeep is committed
src/                    # Source code, agents, tools, and tests
```

## Installation

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Environment Variables

Copy the template and fill in your own credentials:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `MODEL_PROVIDER` | Provider type, default `openai_compatible` |
| `MOONSHOT_API_KEY` | Moonshot/Kimi key; preferred when set |
| `OPENAI_API_KEY` | OpenAI or OpenAI-compatible API key |
| `OPENAI_MODEL` | Model name, default `kimi-k2.6` |
| `OPENAI_BASE_URL` | OpenAI-compatible `/v1` endpoint |
| `MODEL_API_STYLE` | `chat_completions` or `responses` |
| `KIMI_DISABLE_THINKING` | Disable Kimi thinking output |
| `OPENAI_TIMEOUT_SECONDS` | Optional timeout override |
| `OPENAI_MAX_RETRIES` | Optional retry override |

Never commit `.env`, API keys, provider dashboard screenshots, or logs that may
contain secrets. Rotate any key that has appeared in local files before a public
push.

## Defaults

`config/model.yaml` defaults:

| Key | Default |
|---|---|
| `provider` | `openai_compatible` |
| `name` | `kimi-k2.6` |
| `api_style` | `chat_completions` |
| `base_url` | `https://api.moonshot.cn/v1` |
| `timeout_seconds` | `600` |
| `max_retries` | `4` |
| `disable_thinking` | `true` |
| `mock_mode` | `false` |

`config/paths.yaml` defaults:

| Key | Path |
|---|---|
| `blackboard` | `data/blackboard/demo_blackboard.xlsx` |
| `parameter_template` | `data/templates/parameter_checklist_template.xlsx` |
| `source_docs_dir` | `data/input_docs` |
| `outputs_dir` | `outputs/demo` |
| `communication_log` | `outputs/demo/communication_log.xlsx` |
| `runtime_log` | `outputs/demo/runtime.log` |
| `demo_transcripts_dir` | `outputs/demo/demo_transcripts` |
| `docs_dir` | `docs/workflow` |
| `schedule_dir` | `outputs/demo/schedule` |
| `report_assets_dir` | `outputs/demo/report_assets` |

## Customization

- Change providers by editing `.env` and, if needed, `config/model.yaml`.
- Add your own source files under `data/input_docs/`; do not commit real project
  documents.
- Change run output locations in `config/paths.yaml`.
- Extend the blackboard contract in `src/blackboard/sheet_schema.py`.
- Extend agent behavior in `src/agents/` or `src/agentchat_runtime/workflow.py`.

## Usage

Run the real-case workflow:

```bash
python src/main_real_case_workflow.py
```

It reads `data/input_docs/`, writes `data/blackboard/real_case_blackboard.xlsx`,
and exports artifacts under `outputs/real_case/`.

Run local demo checks:

```bash
python src/main_generate_demo.py
python src/main_event_demo.py
python src/main_initial_schedule.py
python src/visualize_schedule.py
```

Generated workbooks, Markdown summaries, images, and logs are local artifacts
and are ignored by Git.

## Tests

```bash
python -m pytest
```


## License

MIT License. See `LICENSE`.
