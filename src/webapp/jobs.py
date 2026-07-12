"""File-system backed Web job management."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from blackboard.excel_store import ExcelBlackboardStore
from tools.case_context import CaseContext, ensure_case_directories, resolve_web_case_context

CN_TZ = timezone(timedelta(hours=8))
ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".xlsx", ".docx", ".pdf"}
DEFAULT_MAX_UPLOAD_MB = 100


@dataclass(frozen=True)
class WebJob:
    """One Web job and its resolved paths."""

    job_id: str
    root: Path
    context: CaseContext
    metadata: dict[str, Any]


def jobs_root(project_root: Path) -> Path:
    return project_root / "data" / "web" / "jobs"


def new_job_id() -> str:
    return datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]


def create_job(project_root: Path, *, original_files: list[dict[str, Any]]) -> WebJob:
    job_id = new_job_id()
    context = resolve_web_case_context(project_root, job_id=job_id)
    ensure_case_directories(context)
    created_at = now_iso()
    metadata = {
        "job_id": job_id,
        "name": default_job_name("", original_files, created_at),
        "status": "queued",
        "created_at": created_at,
        "started_at": "",
        "finished_at": "",
        "uploads": original_files,
        "error_summary": "",
        "artifacts": [],
    }
    write_job_metadata(context, metadata)
    ExcelBlackboardStore(context.blackboard_path).initialize()
    return WebJob(job_id, context.input_docs_dir.parent, context, metadata)


def load_job(project_root: Path, job_id: str) -> WebJob:
    context = resolve_web_case_context(project_root, job_id=job_id)
    path = metadata_path(context)
    if not path.exists():
        raise FileNotFoundError(job_id)
    metadata = json.loads(path.read_text(encoding="utf-8"))
    return WebJob(job_id, context.input_docs_dir.parent, context, metadata)


def list_jobs(project_root: Path) -> list[WebJob]:
    root = jobs_root(project_root)
    if not root.exists():
        return []
    jobs: list[WebJob] = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        try:
            jobs.append(load_job(project_root, path.name))
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            continue
    return jobs


def delete_job(project_root: Path, job_id: str) -> None:
    """Delete one persisted Web job directory after resolving it safely."""

    root = jobs_root(project_root).resolve()
    job_dir = (root / job_id).resolve()
    if root not in job_dir.parents:
        raise ValueError("非法任务路径。")
    if not job_dir.exists():
        raise FileNotFoundError(job_id)
    shutil.rmtree(job_dir)


def cleanup_interrupted_jobs(project_root: Path) -> int:
    """Mark persisted in-flight jobs as cancelled after a service restart.

    Running workers live only in process memory. If the service restarts while a
    job is queued, running, or cancelling, there is no worker left to complete
    the transition. Clearing those states on startup prevents jobs from staying
    stuck forever.
    """

    cleaned = 0
    for job in list_jobs(project_root):
        if job.metadata.get("status") not in {"running", "cancelling"}:
            continue
        update_job_metadata(
            job.context,
            status="cancelled",
            finished_at=job.metadata.get("finished_at") or now_iso(),
            error_summary="服务重启后清理未完成任务；原任务已停止，可重新运行。",
            recovered_from_status=job.metadata.get("status"),
            recovered_at=now_iso(),
        )
        cleaned += 1
    return cleaned


def metadata_path(context: CaseContext) -> Path:
    return context.input_docs_dir.parent / "job.json"


def write_job_metadata(context: CaseContext, metadata: dict[str, Any]) -> None:
    path = metadata_path(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def update_job_metadata(context: CaseContext, **changes: Any) -> dict[str, Any]:
    path = metadata_path(context)
    metadata = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    metadata.update(changes)
    write_job_metadata(context, metadata)
    return metadata


def save_uploads(
    files: list[Any],
    upload_dir: Path,
    *,
    max_upload_bytes: int,
) -> list[dict[str, Any]]:
    """Persist uploaded files with safe names and return manifest rows."""

    upload_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for item in files:
        original_name = Path(getattr(item, "filename", "") or "").name
        if not original_name:
            continue
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的文件类型：{original_name}")
        safe_name = unique_safe_filename(upload_dir, original_name)
        target = upload_dir / safe_name

        size = 0
        with target.open("wb") as output:
            while True:
                chunk = item.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_upload_bytes:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise ValueError(f"上传文件超过大小限制：{original_name}")
                output.write(chunk)
        manifest.append(
            {
                "original_name": original_name,
                "stored_name": safe_name,
                "size": size,
                "extension": suffix,
            }
        )
    if not manifest:
        raise ValueError("请至少上传一个资料文件。")
    return manifest


def unique_safe_filename(directory: Path, filename: str) -> str:
    stem = sanitize_filename(Path(filename).stem) or "upload"
    suffix = Path(filename).suffix.lower()
    candidate = f"{stem}{suffix}"
    index = 1
    while (directory / candidate).exists():
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value, flags=re.UNICODE).strip("._")
    return cleaned[:80]


def max_upload_bytes_from_env(value: str | None) -> int:
    if not value:
        return DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
    text = value.strip().lower()
    multiplier = 1
    if text.endswith("mb"):
        multiplier = 1024 * 1024
        text = text[:-2]
    elif text.endswith("m"):
        multiplier = 1024 * 1024
        text = text[:-1]
    elif text.endswith("kb"):
        multiplier = 1024
        text = text[:-2]
    try:
        return max(1, int(float(text) * multiplier))
    except ValueError:
        return DEFAULT_MAX_UPLOAD_MB * 1024 * 1024


def copy_uploaded_files_to_job(
    project_root: Path,
    uploads: list[Any],
    max_upload_bytes: int,
    *,
    display_name: str | None = None,
) -> WebJob:
    """Create a job and move uploaded files into its upload directory."""

    job_id = new_job_id()
    context = resolve_web_case_context(project_root, job_id=job_id)
    ensure_case_directories(context)
    manifest = save_uploads(uploads, context.input_docs_dir, max_upload_bytes=max_upload_bytes)
    created_at = now_iso()
    metadata = {
        "job_id": job_id,
        "name": default_job_name(display_name or "", manifest, created_at),
        "status": "queued",
        "created_at": created_at,
        "started_at": "",
        "finished_at": "",
        "uploads": manifest,
        "error_summary": "",
        "artifacts": [],
    }
    write_job_metadata(context, metadata)
    ExcelBlackboardStore(context.blackboard_path).initialize()
    return WebJob(job_id, context.input_docs_dir.parent, context, metadata)


def default_job_name(display_name: str, uploads: list[dict[str, Any]], created_at: str) -> str:
    cleaned = " ".join(str(display_name or "").split())
    if cleaned:
        return cleaned[:120]
    first_name = uploads[0].get("original_name") if uploads else ""
    stem = Path(str(first_name or "未命名任务")).stem or "未命名任务"
    if len(uploads) > 1:
        stem = f"{stem}等{len(uploads)}个文件"
    time_label = created_at.replace("T", " ")[:19]
    return f"{stem} {time_label}"[:120]


def remove_job_outputs(context: CaseContext) -> None:
    for path in (context.schedule_dir, context.outputs_root / "visualizations", context.report_assets_dir):
        if path.exists():
            shutil.rmtree(path)


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")
