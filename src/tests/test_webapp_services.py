from __future__ import annotations

import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from blackboard.excel_store import ExcelBlackboardStore
from tests.helpers import (
    minimal_event_rows,
    minimal_parameter_checklist,
    minimal_resource_rows,
    minimal_wbs_rows,
)
from tools.case_context import resolve_web_case_context
from tools.parameter_tools import build_project_parameter_rows
from webapp.auth import make_password_hash, verify_password
from webapp.editors import save_resource_rows, save_wbs_rows
from webapp.jobs import copy_uploaded_files_to_job, load_job, save_uploads
from webapp.preprocess import build_readiness, preprocess_job_documents
from webapp.services import artifact_path, list_existing_artifacts, recalculate_blackboard_outputs


def test_web_case_context_is_job_scoped(tmp_path: Path) -> None:
    first = resolve_web_case_context(tmp_path, job_id="job-a")
    second = resolve_web_case_context(tmp_path, job_id="job-b")

    assert first.blackboard_path != second.blackboard_path
    assert "data/web/jobs/job-a" in first.blackboard_path.as_posix()
    assert first.input_docs_dir.name == "uploads"


def test_upload_extension_and_size_validation(tmp_path: Path) -> None:
    upload = SimpleNamespace(filename="施工资料.txt", file=BytesIO(b"hello"))
    manifest = save_uploads([upload], tmp_path, max_upload_bytes=10)

    assert manifest[0]["original_name"] == "施工资料.txt"
    assert (tmp_path / manifest[0]["stored_name"]).exists()

    bad = SimpleNamespace(filename="drawing.dwg", file=BytesIO(b"x"))
    with pytest.raises(ValueError):
        save_uploads([bad], tmp_path, max_upload_bytes=10)

    too_big = SimpleNamespace(filename="large.txt", file=BytesIO(b"01234567890"))
    with pytest.raises(ValueError):
        save_uploads([too_big], tmp_path, max_upload_bytes=10)


def test_password_hash_verification() -> None:
    password_hash = make_password_hash("secret")

    assert verify_password("secret", password_hash)
    assert not verify_password("wrong", password_hash)


def test_wbs_and_resource_edit_validation(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    store.replace_rows("wbs_tasks_final", minimal_wbs_rows())
    store.replace_rows("resource_plan_final", minimal_resource_rows())

    duplicate_wbs = {
        "task_id__0": "TASK-0001",
        "wbs_code__0": "01",
        "phase__0": "phase",
        "task_name__0": "a",
        "duration_days__0": "1",
        "predecessor_ids__0": "",
        "relation_type__0": "FS",
        "lag_days__0": "0",
        "task_id__1": "TASK-0001",
        "wbs_code__1": "02",
        "phase__1": "phase",
        "task_name__1": "b",
        "duration_days__1": "1",
        "predecessor_ids__1": "",
        "relation_type__1": "FS",
        "lag_days__1": "0",
    }
    errors = save_wbs_rows(store, duplicate_wbs)
    assert any("重复" in item["message"] for item in errors)

    bad_resource = {
        "task_id__0": "MISSING",
        "resource_type__0": "labor",
        "resource_name__0": "crew",
        "demand__0": "1",
        "unit__0": "worker",
        "capacity__0": "2",
        "period__0": "2026-W01",
    }
    errors = save_resource_rows(store, bad_resource)
    assert any("不存在" in item["message"] for item in errors)


def test_recalculate_outputs_and_download_whitelist(tmp_path: Path) -> None:
    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    store.replace_rows("parameter_checklist", minimal_parameter_checklist())
    store.replace_rows("project_parameters", build_project_parameter_rows(minimal_parameter_checklist()))
    store.replace_rows("wbs_tasks_final", minimal_wbs_rows())
    store.replace_rows("resource_plan_final", minimal_resource_rows())

    counts = recalculate_blackboard_outputs(store, tmp_path / "outputs", title="pytest")

    assert counts["schedule_initial"] == 3
    assert (tmp_path / "outputs" / "schedule" / "schedule_initial.xlsx").exists()
    assert artifact_path(tmp_path / "outputs", "schedule", tmp_path / "blackboard.xlsx").name == "schedule_initial.xlsx"
    assert any(item["key"] == "schedule" for item in list_existing_artifacts(tmp_path / "outputs", tmp_path / "blackboard.xlsx"))
    with pytest.raises(KeyError):
        artifact_path(tmp_path / "outputs", "../../secret", tmp_path / "blackboard.xlsx")


def test_create_job_writes_metadata_and_blackboard(tmp_path: Path) -> None:
    upload = SimpleNamespace(filename="case.md", file=BytesIO(b"# case"))
    job = copy_uploaded_files_to_job(tmp_path, [upload], max_upload_bytes=100)

    assert job.metadata["status"] == "queued"
    assert job.context.blackboard_path.exists()
    assert job.context.runtime_log.parent.exists()


def test_create_job_imports_standard_workbook_tables(tmp_path: Path) -> None:
    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)
    for sheet_name, rows in {
        "wbs_tasks_final": minimal_wbs_rows(),
        "resource_plan_final": minimal_resource_rows(),
    }.items():
        sheet = workbook.create_sheet(sheet_name)
        headers = list(rows[0].keys())
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(header) for header in headers])
    payload = BytesIO()
    workbook.save(payload)
    payload.seek(0)

    upload = SimpleNamespace(filename="standard.xlsx", file=payload)
    job = copy_uploaded_files_to_job(tmp_path, [upload], max_upload_bytes=100000)

    store = ExcelBlackboardStore(job.context.blackboard_path)
    assert len(store.read_rows("wbs_tasks_final")) == len(minimal_wbs_rows())
    assert len(store.read_rows("resource_plan_final")) == len(minimal_resource_rows())
    assert job.metadata["standard_table_import"]["imported"]["wbs_tasks_final"] == len(minimal_wbs_rows())


def test_preprocess_merges_existing_parameter_checklist(tmp_path: Path) -> None:
    input_dir = tmp_path / "uploads"
    input_dir.mkdir()
    (input_dir / "case.md").write_text("# case\nNo structured parameters in this document.", encoding="utf-8")
    blackboard_path = tmp_path / "blackboard.xlsx"
    store = ExcelBlackboardStore(blackboard_path)
    store.initialize()
    checklist = minimal_parameter_checklist()
    checklist.append(
        {
            **checklist[0],
            "parameter_id": "P-016",
            "category": "technical_boundary",
            "name": "foundation_type",
            "value": "raft foundation",
            "note": "manual checklist value",
        }
    )
    store.replace_rows("parameter_checklist", checklist)

    result = preprocess_job_documents(input_dir, tmp_path / "outputs", blackboard_path)

    assert result.package["summary"]["recognized_parameter_count"] >= len(checklist)
    recognized_ids = {row["parameter_id"] for row in result.package["recognized_parameters"]}
    missing_ids = {row["parameter_id"] for row in result.package["missing_required_parameters"]}
    assert "P-016" in recognized_ids
    assert "P-016" not in missing_ids
    reloaded = ExcelBlackboardStore(blackboard_path)
    assert len(reloaded.read_rows("parameter_checklist")) >= len(checklist)


def test_readiness_rewards_complete_required_parameters() -> None:
    readiness = build_readiness(
        documents=[SimpleNamespace(text="readable source")],
        recognized_parameters=[{"parameter_id": f"P-{index:03d}"} for index in range(8)],
        missing_required=[],
        resource_candidates=[{"name_hint": f"resource-{index}", "quantity_hint": "1"} for index in range(8)],
        schedule_candidates=[{"type": "milestone"} for _ in range(10)],
    )

    assert readiness["score"] == 95


def test_fallback_restores_preserved_manual_tables(tmp_path: Path) -> None:
    from webapp import app as webapp_app

    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    store.replace_rows("parameter_checklist", minimal_parameter_checklist())
    store.replace_rows("project_parameters", build_project_parameter_rows(minimal_parameter_checklist()))
    store.replace_rows("wbs_tasks_final", minimal_wbs_rows())
    store.replace_rows("resource_plan_final", minimal_resource_rows())
    preserved = webapp_app.snapshot_preserved_tables(store)

    fallback_wbs = [
        {
            **minimal_wbs_rows()[0],
            "task_id": "TASK-FALLBACK",
            "source": "rules_fallback+source_context",
            "owner_agent": "fallback_scheduler",
            "note": "Fallback placeholder.",
        }
    ]
    event = {**minimal_event_rows()[0], "event_type": "runtime_fallback", "created_by": "fallback_scheduler"}
    store.replace_rows("wbs_tasks_final", fallback_wbs)
    store.replace_rows("resource_plan_final", [])
    store.replace_rows("event_log", [event])

    restored = webapp_app.restore_preserved_tables_after_fallback(store, preserved)

    assert restored["wbs_tasks_final"] == len(minimal_wbs_rows())
    assert restored["resource_plan_final"] == len(minimal_resource_rows())
    assert len(store.read_rows("wbs_tasks_final")) == len(minimal_wbs_rows())
    assert len(store.read_rows("resource_plan_final")) == len(minimal_resource_rows())


def test_fallback_without_preserved_tables_is_marked_incomplete(tmp_path: Path) -> None:
    from webapp import app as webapp_app

    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    fallback_wbs = [
        {
            **minimal_wbs_rows()[0],
            "task_id": "TASK-FALLBACK",
            "source": "rules_fallback+source_context",
            "owner_agent": "fallback_scheduler",
            "note": "Fallback placeholder.",
        }
    ]
    event = {**minimal_event_rows()[0], "event_type": "runtime_fallback", "created_by": "fallback_scheduler"}
    store.replace_rows("wbs_tasks_final", fallback_wbs)
    store.replace_rows("event_log", [event])

    quality = webapp_app.assess_result_quality(store, {"schedule_initial": 0}, {}, run_mode="standard")

    assert quality["level"] == "fallback_incomplete"
    assert quality["fallback_detected"] is True


def test_default_run_mode_prefers_recalculate_when_tables_exist(tmp_path: Path) -> None:
    from webapp import app as webapp_app

    assert webapp_app.default_run_mode({"wbs_tasks_final": 1, "resource_plan_final": 1}, {}) == "recalculate"
    assert webapp_app.default_run_mode({"wbs_tasks_final": 0, "resource_plan_final": 0}, {"run_mode": "light"}) == "light"


def test_worker_refreshes_preprocess_package_before_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from webapp import app as webapp_app

    upload = SimpleNamespace(filename="case.md", file=BytesIO(b"# case"))
    job = copy_uploaded_files_to_job(tmp_path, [upload], max_upload_bytes=100)
    calls: list[str] = []

    def fake_preprocess(input_docs_dir: Path, outputs_root: Path, blackboard_path: Path) -> SimpleNamespace:
        calls.append("preprocess")
        return SimpleNamespace(package={"summary": {"readiness_score": 95, "missing_required_count": 0}})

    def fake_workflow(**kwargs: object) -> dict[str, Path]:
        calls.append("workflow")
        context = kwargs["context"]
        return {"output_dir": context.outputs_root, "blackboard": context.blackboard_path}

    monkeypatch.setattr(webapp_app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(webapp_app, "preprocess_job_documents", fake_preprocess)
    monkeypatch.setattr(webapp_app, "run_real_case_workflow", fake_workflow)
    monkeypatch.setattr(webapp_app, "recalculate_blackboard_outputs", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp_app, "list_existing_artifacts", lambda *args, **kwargs: [])

    lock = threading.Lock()
    lock.acquire()
    webapp_app._run_job_worker(job.job_id, lock, threading.Event(), {})

    reloaded = load_job(tmp_path, job.job_id)
    assert calls[:2] == ["preprocess", "workflow"]
    assert reloaded.metadata["preprocess_summary"]["readiness_score"] == 95
    assert reloaded.metadata["status"] == "succeeded"


def test_recalculate_worker_skips_model_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from webapp import app as webapp_app
    from webapp.jobs import update_job_metadata

    upload = SimpleNamespace(filename="case.md", file=BytesIO(b"# case"))
    job = copy_uploaded_files_to_job(tmp_path, [upload], max_upload_bytes=100)
    store = ExcelBlackboardStore(job.context.blackboard_path)
    store.replace_rows("wbs_tasks_final", minimal_wbs_rows())
    store.replace_rows("resource_plan_final", minimal_resource_rows())
    update_job_metadata(job.context, status="running", run_mode="recalculate")
    calls: list[str] = []

    monkeypatch.setattr(webapp_app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        webapp_app,
        "preprocess_job_documents",
        lambda *args, **kwargs: SimpleNamespace(package={"summary": {"readiness_score": 95}}),
    )
    monkeypatch.setattr(
        webapp_app,
        "run_real_case_workflow",
        lambda **kwargs: pytest.fail("model workflow should not run in recalculate mode"),
    )

    def fake_recalculate(*args: object, **kwargs: object) -> dict[str, int]:
        calls.append("recalculate")
        return {"schedule_initial": 3}

    monkeypatch.setattr(webapp_app, "recalculate_blackboard_outputs", fake_recalculate)
    monkeypatch.setattr(webapp_app, "list_existing_artifacts", lambda *args, **kwargs: [])

    lock = threading.Lock()
    lock.acquire()
    webapp_app._run_job_worker(job.job_id, lock, threading.Event(), {})

    reloaded = load_job(tmp_path, job.job_id)
    assert calls == ["recalculate"]
    assert reloaded.metadata["status"] == "succeeded"
    assert reloaded.metadata["result_quality"]["level"] == "recalculated"
