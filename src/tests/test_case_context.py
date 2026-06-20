from pathlib import Path

import pytest

from tools.case_context import (
    archive_case_state,
    ensure_case_directories,
    resolve_case_context,
)


def test_case_context_uses_workspace_blackboard_and_outputs(tmp_path: Path) -> None:
    context = resolve_case_context(tmp_path)

    assert context.case_id == "real_case"
    assert context.blackboard_path == tmp_path / "data" / "blackboard" / "real_case_blackboard.xlsx"
    assert context.input_docs_dir == tmp_path / "data" / "input_docs"
    assert context.schedule_dir == tmp_path / "outputs" / "real_case" / "schedule"


def test_input_dir_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_case_context(tmp_path, input_dir="../bad")


def test_archive_case_state_copies_existing_blackboard_and_outputs(tmp_path: Path) -> None:
    context = resolve_case_context(tmp_path)
    ensure_case_directories(context)
    context.blackboard_path.write_text("blackboard", encoding="utf-8")
    (context.schedule_dir / "result.txt").write_text("schedule", encoding="utf-8")

    result = archive_case_state(context)

    assert result.archived_paths
    archived_blackboard = (
        context.case_archive_dir / result.run_id / "blackboard" / "real_case_blackboard.xlsx"
    )
    assert archived_blackboard.exists()
    assert (context.outputs_archive_dir / result.run_id / "schedule" / "result.txt").exists()
    assert (context.case_archive_dir / result.run_id / "manifest.json").exists()
