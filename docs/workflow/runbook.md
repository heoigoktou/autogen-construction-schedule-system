# Runbook

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Configure The Model

Copy the environment template and fill in your own credentials:

```bash
cp .env.example .env
```

The real-case workflow requires a live model endpoint. It fails fast when the
model package, API key, model name, or readable source documents are missing.
It does not fall back to mock WBS rows or sample events.

## Run The Real-Case Workflow

Place source documents in `data/input_docs/`, then run:

```bash
python src/main_real_case_workflow.py
```

Supported source files include `.txt`, `.md`, `.csv`, `.xlsx`, `.xlsm`,
`.docx`, `.doc`, `.pdf`, `.dxf`, and `.dwg`.

Main outputs are written under `outputs/real_case/` and are ignored by Git.

## Run Demo Checks

```bash
python src/main_generate_demo.py
python src/main_event_demo.py
python src/main_initial_schedule.py
```

These scripts inspect the configured demo blackboard and route existing records.
They are useful for local checks, but generated workbooks and logs are not meant
to be committed.

## Troubleshooting

| Problem | Action |
|---|---|
| Missing dependencies | Run `python -m pip install -e ".[dev]"`. |
| Model configuration fails | Check `.env`, `config/model.yaml`, and the selected provider endpoint. |
| `.doc` parsing fails | Install Microsoft Word or LibreOffice for conversion. |
| Excel write fails | Close open workbook files and rerun. |
| Validation fails | Inspect `debug_records` and `agent_message_log` in the blackboard. |
