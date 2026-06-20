# Schedule Generation

## Inputs

- `wbs_tasks_final`
- `resource_plan_final`
- `project_parameters`

## Outputs

- `schedule_initial`
- `cpm_analysis`
- `network_edges`
- `milestone_check`
- Excel exports under `outputs/demo/schedule/`

## Validation Notes

The schedule workflow validates predecessor references and CPM feasibility
before exporting the schedule workbooks. Generated files are local run artifacts
and are ignored by Git.
