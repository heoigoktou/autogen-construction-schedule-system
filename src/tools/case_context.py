"""Workspace-scoped paths and archive helpers for real-case workflows."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class ArchiveResult:
    """Archive operation result."""

    run_id: str
    archived_paths: list[Path]
    warnings: list[str]


@dataclass(frozen=True)
class CaseContext:
    """Resolved paths for the single real planning workflow."""

    case_id: str
    input_docs_dir: Path
    blackboard_path: Path
    case_archive_dir: Path
    tmp_dir: Path
    outputs_root: Path
    schedule_dir: Path
    demo_transcripts_dir: Path
    report_assets_dir: Path
    outputs_archive_dir: Path
    runtime_log: Path


def resolve_case_context(
    project_root: Path,
    *,
    input_dir: str | Path | None = None,
) -> CaseContext:
    """Resolve workspace-level paths for the real workflow."""

    input_docs_dir = Path(input_dir) if input_dir else project_root / "data" / "input_docs"
    if not input_docs_dir.is_absolute():
        input_docs_dir = project_root / input_docs_dir
    input_docs_dir = _resolve_under_workspace(project_root, input_docs_dir)

    outputs_root = project_root / "outputs" / "real_case"
    return CaseContext(
        case_id="real_case",
        input_docs_dir=input_docs_dir,
        blackboard_path=project_root / "data" / "blackboard" / "real_case_blackboard.xlsx",
        case_archive_dir=project_root / "data" / "archive" / "real_case",
        tmp_dir=project_root / "data" / ".tmp" / "real_case",
        outputs_root=outputs_root,
        schedule_dir=outputs_root / "schedule",
        demo_transcripts_dir=outputs_root / "demo_transcripts",
        report_assets_dir=outputs_root / "report_assets",
        outputs_archive_dir=outputs_root / "archive",
        runtime_log=outputs_root / "runtime.log",
    )


def ensure_case_directories(context: CaseContext) -> None:
    """Create the standard directories for the real workflow."""

    for path in (
        context.input_docs_dir,
        context.blackboard_path.parent,
        context.case_archive_dir,
        context.tmp_dir,
        context.schedule_dir,
        context.demo_transcripts_dir,
        context.report_assets_dir,
        context.outputs_archive_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def archive_case_state(context: CaseContext) -> ArchiveResult:
    """Archive the current blackboard and outputs before a new run."""

    run_id = datetime.now(CN_TZ).strftime("run_%Y%m%d_%H%M%S")
    case_archive_run = context.case_archive_dir / run_id
    output_archive_run = context.outputs_archive_dir / run_id
    archived_paths: list[Path] = []
    warnings: list[str] = []

    for source, target_root in (
        (context.blackboard_path, case_archive_run / "blackboard"),
        (context.schedule_dir, output_archive_run / "schedule"),
        (context.demo_transcripts_dir, output_archive_run / "demo_transcripts"),
        (context.report_assets_dir, output_archive_run / "report_assets"),
    ):
        if not source.exists() or not _has_archive_content(source):
            continue
        try:
            archived_paths.append(_copy_to_archive(source, target_root))
        except OSError as exc:
            warnings.append(f"归档失败：{source} -> {target_root}: {exc}")

    if archived_paths or warnings:
        _write_manifest(
            case_archive_run / "manifest.json",
            context=context,
            run_id=run_id,
            archived_paths=archived_paths,
            warnings=warnings,
        )
        _write_manifest(
            output_archive_run / "manifest.json",
            context=context,
            run_id=run_id,
            archived_paths=archived_paths,
            warnings=warnings,
        )
    return ArchiveResult(run_id=run_id, archived_paths=archived_paths, warnings=warnings)


def _has_archive_content(path: Path) -> bool:
    if path.is_file():
        return True
    return any(path.iterdir())


def _copy_to_archive(source: Path, target_root: Path) -> Path:
    target_root.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        target = target_root
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        return target

    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / source.name
    shutil.copy2(source, target)
    return target


def _write_manifest(
    path: Path,
    *,
    context: CaseContext,
    run_id: str,
    archived_paths: list[Path],
    warnings: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "case_id": context.case_id,
        "run_id": run_id,
        "created_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "blackboard": str(context.blackboard_path),
        "outputs_root": str(context.outputs_root),
        "archived_paths": [str(item) for item in archived_paths],
        "warnings": warnings,
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_under_workspace(workspace_root: Path, path: Path) -> Path:
    resolved_root = workspace_root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"路径必须位于工作区内：{resolved_path}") from exc
    return resolved_path
