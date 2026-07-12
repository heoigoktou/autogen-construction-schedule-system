from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from blackboard.excel_store import ExcelBlackboardStore
from tests.helpers import minimal_parameter_checklist, minimal_resource_rows, minimal_wbs_rows
from tools.case_context import resolve_web_case_context
from tools.parameter_tools import build_project_parameter_rows
from webapp.auth import make_password_hash, verify_password
from webapp.editors import save_resource_rows, save_wbs_rows
from webapp.jobs import copy_uploaded_files_to_job, save_uploads
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
