"""FastAPI entrypoint for the internal schedule Web app."""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from blackboard.excel_store import ExcelBlackboardStore
from main_real_case_workflow import PROJECT_ROOT, run_real_case_workflow
from webapp import auth
from webapp.adjustments import EVENT_PRESETS, apply_schedule_adjustment, current_project_start_date
from webapp.editors import RESOURCE_EDIT_FIELDS, WBS_EDIT_FIELDS, save_resource_rows, save_wbs_rows
from webapp.jobs import (
    cleanup_interrupted_jobs,
    copy_uploaded_files_to_job,
    delete_job,
    list_jobs,
    load_job,
    max_upload_bytes_from_env,
    now_iso,
    update_job_metadata,
)
from webapp.preprocess import (
    load_preprocess_package,
    preprocess_job_documents,
    save_preprocess_supplements,
)
from webapp.services import (
    artifact_path,
    build_results_zip,
    list_existing_artifacts,
    recalculate_blackboard_outputs,
)
from webapp.visual_data import (
    VISUAL_EXPORTS,
    build_visual_payload,
    export_visual_json_artifacts,
    read_json,
    visual_export_path,
)

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals["current_user"] = auth.current_user
templates.env.filters["date_only"] = lambda value: date_only(value)
LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleaned = cleanup_interrupted_jobs(PROJECT_ROOT)
    if cleaned:
        LOGGER.warning("Cleaned %s interrupted Web job(s) on startup.", cleaned)
    app.state.executor = ThreadPoolExecutor(max_workers=1)
    app.state.run_lock = threading.Lock()
    app.state.running_jobs = {}
    yield
    app.state.executor.shutdown(wait=False, cancel_futures=False)


app = FastAPI(title="Construction Schedule Web", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Response:
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/jobs", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
    if username == auth.configured_username() and auth.verify_password(
        password,
        auth.configured_password_hash(),
    ):
        return auth.login_response(username, redirect_to="/jobs?welcome=1")
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "用户名或密码错误。"},
        status_code=401,
    )


@app.post("/logout")
def logout() -> Response:
    return auth.logout_response()


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    jobs = list_jobs(PROJECT_ROOT)
    return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs})


@app.get("/jobs/new", response_class=HTMLResponse)
def new_job_page(request: Request) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    return templates.TemplateResponse(
        request,
        "new_job.html",
        {
            "allowed_extensions": ", ".join(sorted({ext.upper() for ext in {'.txt', '.md', '.csv', '.xlsx', '.docx', '.pdf'}})),
            "error": "",
        },
    )


@app.post("/jobs")
def create_job(request: Request, files: list[UploadFile] = File(...), job_name: str = Form("")) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    try:
        job = copy_uploaded_files_to_job(
            PROJECT_ROOT,
            files,
            max_upload_bytes_from_env(os.getenv("WEB_MAX_UPLOAD_SIZE")),
            display_name=job_name,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "new_job.html",
            {
                "allowed_extensions": "TXT, MD, CSV, XLSX, DOCX, PDF",
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(f"/jobs/{job.job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    artifacts = list_existing_artifacts(job.context.outputs_root, job.context.blackboard_path)
    preprocess_package = load_preprocess_package(job.context.outputs_root)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job,
            "artifacts": artifacts,
            "artifact_groups": group_artifacts(artifacts),
            "preprocess": preprocess_package,
        },
    )


@app.get("/jobs/{job_id}/preprocess", response_class=HTMLResponse)
def preprocess_page(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    package = load_preprocess_package(job.context.outputs_root)
    return templates.TemplateResponse(
        request,
        "preprocess.html",
        {"job": job, "preprocess": package},
    )


@app.post("/jobs/{job_id}/preprocess")
def run_preprocess(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    if job.metadata.get("status") in {"running", "cancelling"}:
        update_job_metadata(job.context, error_summary="任务运行中，暂不能重新预处理资料。")
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    try:
        result = preprocess_job_documents(
            job.context.input_docs_dir,
            job.context.outputs_root,
            job.context.blackboard_path,
        )
    except Exception as exc:
        update_job_metadata(job.context, error_summary=f"资料预处理失败：{exc}")
        return RedirectResponse(f"/jobs/{job_id}/preprocess", status_code=303)
    update_job_metadata(
        job.context,
        preprocessed_at=now_iso(),
        preprocess_summary=result.package.get("summary") or {},
        error_summary="",
        artifacts=list_existing_artifacts(job.context.outputs_root, job.context.blackboard_path),
    )
    return RedirectResponse(f"/jobs/{job_id}/preprocess", status_code=303)


@app.post("/jobs/{job_id}/preprocess/supplements")
async def save_preprocess_supplements_route(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    if job.metadata.get("status") in {"running", "cancelling"}:
        update_job_metadata(job.context, error_summary="任务运行中，暂不能保存预处理补充。")
        return RedirectResponse(f"/jobs/{job_id}/preprocess", status_code=303)
    form_data: FormData = await request.form()
    form = dict(form_data.multi_items())
    try:
        saved = save_preprocess_supplements(job.context.blackboard_path, form)
        result = preprocess_job_documents(
            job.context.input_docs_dir,
            job.context.outputs_root,
            job.context.blackboard_path,
        )
    except Exception as exc:
        update_job_metadata(job.context, error_summary=f"保存补充失败：{exc}")
        return RedirectResponse(f"/jobs/{job_id}/preprocess", status_code=303)
    update_job_metadata(
        job.context,
        preprocessed_at=now_iso(),
        preprocess_summary=result.package.get("summary") or {},
        last_preprocess_supplement=saved,
        error_summary="",
        artifacts=list_existing_artifacts(job.context.outputs_root, job.context.blackboard_path),
    )
    return RedirectResponse(f"/jobs/{job_id}/preprocess", status_code=303)


@app.get("/jobs/{job_id}/visualize", response_class=HTMLResponse)
def visualize_job(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    return templates.TemplateResponse(
        request,
        "visualize.html",
        {"job": job},
    )


@app.get("/jobs/{job_id}/visualize/data")
def visualize_data(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    store = ExcelBlackboardStore(job.context.blackboard_path)
    store.initialize()
    baseline = read_json(visual_export_path(job.context.outputs_root, "baseline_visual_data"))
    data = build_visual_payload(store, baseline=baseline)
    return JSONResponse(data)


@app.post("/jobs/{job_id}/run")
def run_job(request: Request, job_id: str, run_mode: str = Form("standard")) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    run_mode = run_mode if run_mode in {"standard", "light"} else "standard"
    if job.metadata.get("status") in {"running", "cancelling"}:
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    if not request.app.state.run_lock.acquire(blocking=False):
        update_job_metadata(
            job.context,
            status="queued",
            error_summary="已有任务正在运行，请稍后再启动。",
        )
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    update_job_metadata(
        job.context,
        status="running",
        run_mode=run_mode,
        preflight_warnings=preflight_warnings(job, run_mode),
        started_at=now_iso(),
        finished_at="",
        error_summary="",
    )
    job.context.runtime_log.parent.mkdir(parents=True, exist_ok=True)
    job.context.runtime_log.write_text("", encoding="utf-8")
    cancel_event = threading.Event()
    request.app.state.running_jobs[job.job_id] = cancel_event
    request.app.state.executor.submit(
        _run_job_worker,
        job.job_id,
        request.app.state.run_lock,
        cancel_event,
        request.app.state.running_jobs,
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    if job.metadata.get("status") not in {"running", "queued", "cancelling"}:
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    cancel_event = request.app.state.running_jobs.get(job_id)
    if cancel_event is not None:
        cancel_event.set()
    else:
        update_job_metadata(
            job.context,
            status="cancelled",
            finished_at=now_iso(),
            cancel_requested_at=now_iso(),
            error_summary="未找到正在运行的后台任务；已清理为已中止状态，可重新运行。",
        )
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    update_job_metadata(
        job.context,
        status="cancelling",
        cancel_requested_at=now_iso(),
        error_summary="正在中止任务，当前模型请求返回后会停止。",
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/delete")
def delete_failed_or_cancelled_job(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    if job.metadata.get("status") not in {"failed", "cancelled"}:
        update_job_metadata(
            job.context,
            error_summary="只能删除失败或已取消的任务。",
        )
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    delete_job(PROJECT_ROOT, job_id)
    return RedirectResponse("/jobs", status_code=303)


@app.get("/jobs/{job_id}/logs", response_class=HTMLResponse)
def job_logs(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    content = tail_file(job.context.runtime_log)
    if request.headers.get("HX-Request"):
        return HTMLResponse(f"<pre class=\"log-box\">{escape_html(content)}</pre>")
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"job": job, "content": content},
    )


@app.get("/jobs/{job_id}/download/{artifact}")
def download_artifact(request: Request, job_id: str, artifact: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    if artifact == "all":
        path = build_results_zip(job.context.outputs_root, job.context.blackboard_path)
    else:
        try:
            path = artifact_path(job.context.outputs_root, artifact, job.context.blackboard_path)
        except KeyError:
            return HTMLResponse("未知下载项。", status_code=404)
    if artifact in VISUAL_EXPORTS and (not path.exists() or not path.is_file()):
        store = ExcelBlackboardStore(job.context.blackboard_path)
        store.initialize()
        export_visual_json_artifacts(store, job.context.outputs_root)
    if not path.exists() or not path.is_file():
        return HTMLResponse("文件不存在。", status_code=404)
    return FileResponse(path, filename=path.name)


@app.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
def edit_job(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    store = ExcelBlackboardStore(job.context.blackboard_path)
    store.initialize()
    return templates.TemplateResponse(
        request,
        "edit.html",
        {
            "job": job,
            "wbs_rows": store.read_rows("wbs_tasks_final"),
            "resource_rows": store.read_rows("resource_plan_final"),
            "wbs_fields": WBS_EDIT_FIELDS,
            "resource_fields": RESOURCE_EDIT_FIELDS,
            "event_presets": EVENT_PRESETS,
            "current_start_date": current_project_start_date(store),
            "errors": [],
            "message": "",
        },
    )


@app.post("/jobs/{job_id}/edit/wbs", response_class=HTMLResponse)
async def save_wbs(request: Request, job_id: str) -> Response:
    return await _save_edit(request, job_id, "wbs")


@app.post("/jobs/{job_id}/edit/resources", response_class=HTMLResponse)
async def save_resources(request: Request, job_id: str) -> Response:
    return await _save_edit(request, job_id, "resources")


@app.post("/jobs/{job_id}/adjust", response_class=HTMLResponse)
async def adjust_job(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    form_data: FormData = await request.form()
    form = dict(form_data.multi_items())
    job = load_job(PROJECT_ROOT, job_id)
    store = ExcelBlackboardStore(job.context.blackboard_path)
    try:
        result = apply_schedule_adjustment(store, form)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "edit.html",
            {
                "job": load_job(PROJECT_ROOT, job_id),
                "wbs_rows": store.read_rows("wbs_tasks_final"),
                "resource_rows": store.read_rows("resource_plan_final"),
                "wbs_fields": WBS_EDIT_FIELDS,
                "resource_fields": RESOURCE_EDIT_FIELDS,
                "event_presets": EVENT_PRESETS,
                "current_start_date": current_project_start_date(store),
                "errors": [{"row": "调整", "field": "adjustment", "message": str(exc)}],
                "message": "",
            },
            status_code=400,
        )
    update_job_metadata(
        job.context,
        last_adjusted_at=now_iso(),
        last_adjustment_summary=result.message,
        error_summary="",
    )
    return templates.TemplateResponse(
        request,
        "edit.html",
        {
            "job": load_job(PROJECT_ROOT, job_id),
            "wbs_rows": store.read_rows("wbs_tasks_final"),
            "resource_rows": store.read_rows("resource_plan_final"),
            "wbs_fields": WBS_EDIT_FIELDS,
            "resource_fields": RESOURCE_EDIT_FIELDS,
            "event_presets": EVENT_PRESETS,
            "current_start_date": result.new_start_date,
            "errors": [],
            "message": result.message,
        },
    )


@app.post("/jobs/{job_id}/recalculate")
def recalculate_job(request: Request, job_id: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    job = load_job(PROJECT_ROOT, job_id)
    store = ExcelBlackboardStore(job.context.blackboard_path)
    try:
        result = recalculate_blackboard_outputs(store, job.context.outputs_root, title=f"Web Job {job_id}")
    except Exception as exc:
        update_job_metadata(job.context, error_summary=str(exc))
        return RedirectResponse(f"/jobs/{job_id}/edit", status_code=303)
    update_job_metadata(
        job.context,
        artifacts=list_existing_artifacts(job.context.outputs_root, job.context.blackboard_path),
        last_recalculated_at=now_iso(),
        last_recalculate_counts=result,
        error_summary="",
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


async def _save_edit(request: Request, job_id: str, table: str) -> Response:
    guard = auth.require_login(request)
    if isinstance(guard, Response):
        return guard
    form_data: FormData = await request.form()
    form = dict(form_data.multi_items())
    job = load_job(PROJECT_ROOT, job_id)
    store = ExcelBlackboardStore(job.context.blackboard_path)
    errors = save_wbs_rows(store, form) if table == "wbs" else save_resource_rows(store, form)
    if errors:
        return templates.TemplateResponse(
            request,
            "edit.html",
            {
                "job": load_job(PROJECT_ROOT, job_id),
                "wbs_rows": store.read_rows("wbs_tasks_final"),
                "resource_rows": store.read_rows("resource_plan_final"),
                "wbs_fields": WBS_EDIT_FIELDS,
                "resource_fields": RESOURCE_EDIT_FIELDS,
                "event_presets": EVENT_PRESETS,
                "current_start_date": current_project_start_date(store),
                "errors": errors,
                "message": "",
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "edit.html",
        {
            "job": load_job(PROJECT_ROOT, job_id),
            "wbs_rows": store.read_rows("wbs_tasks_final"),
            "resource_rows": store.read_rows("resource_plan_final"),
            "wbs_fields": WBS_EDIT_FIELDS,
            "resource_fields": RESOURCE_EDIT_FIELDS,
            "event_presets": EVENT_PRESETS,
            "current_start_date": current_project_start_date(store),
            "errors": [],
            "message": "保存成功。请点击重新计算生成最新排程和图表。",
        },
    )


def _run_job_worker(
    job_id: str,
    lock: threading.Lock,
    cancel_event: threading.Event,
    running_jobs: dict[str, threading.Event],
) -> None:
    try:
        job = load_job(PROJECT_ROOT, job_id)
        preprocess_result = preprocess_job_documents(
            job.context.input_docs_dir,
            job.context.outputs_root,
            job.context.blackboard_path,
        )
        update_job_metadata(
            job.context,
            preprocessed_at=now_iso(),
            preprocess_summary=preprocess_result.package.get("summary") or {},
        )
        if cancel_event.is_set():
            raise RuntimeError("Job cancelled by user.")
        job = load_job(PROJECT_ROOT, job_id)
        result = run_real_case_workflow(
            context=job.context,
            project_root=PROJECT_ROOT,
            archive=False,
            cancel_checker=cancel_event.is_set,
            run_mode=str(job.metadata.get("run_mode") or "standard"),
        )
        if cancel_event.is_set():
            raise RuntimeError("Job cancelled by user.")
        counts = recalculate_blackboard_outputs(
            ExcelBlackboardStore(job.context.blackboard_path),
            job.context.outputs_root,
            title=f"Web Job {job_id}",
        )
        if cancel_event.is_set():
            raise RuntimeError("Job cancelled by user.")
        update_job_metadata(
            job.context,
            status="succeeded",
            finished_at=now_iso(),
            error_summary="",
            artifacts=list_existing_artifacts(job.context.outputs_root, job.context.blackboard_path),
            last_recalculate_counts=counts,
            result_summary={
                "output_dir": str(result["output_dir"]),
                "blackboard": str(result["blackboard"]),
            },
        )
    except Exception as exc:
        cancelled = cancel_event.is_set() or "cancelled" in str(exc).lower()
        LOGGER.exception("Web job %s %s", job_id, "cancelled" if cancelled else "failed")
        try:
            job = load_job(PROJECT_ROOT, job_id)
            update_job_metadata(
                job.context,
                status="cancelled" if cancelled else "failed",
                finished_at=now_iso(),
                error_summary="任务已中止。" if cancelled else friendly_error_summary(exc),
            )
        except Exception:
            LOGGER.exception("Failed to mark Web job %s as failed", job_id)
    finally:
        running_jobs.pop(job_id, None)
        lock.release()


def tail_file(path: Path, max_bytes: int = 200000) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def preflight_warnings(job: Any, run_mode: str) -> list[str]:
    uploads = job.metadata.get("uploads") or []
    total_bytes = sum(int(item.get("size") or 0) for item in uploads)
    warnings: list[str] = []
    if len(uploads) >= 4:
        warnings.append("上传文件较多，AgentChat 请求量可能升高。")
    if total_bytes >= 2 * 1024 * 1024:
        warnings.append("上传资料体积较大，建议优先使用轻量模式或拆分资料。")
    preprocess_summary = job.metadata.get("preprocess_summary") or {}
    if not preprocess_summary:
        warnings.append("尚未执行资料预处理；建议先生成标准化输入包，检查缺失参数后再运行。")
    elif int(preprocess_summary.get("missing_required_count") or 0) > 0:
        warnings.append(
            f"资料预处理发现 {preprocess_summary.get('missing_required_count')} 个必需参数未识别，"
            "建议补充或确认后再正式运行。"
        )
    if run_mode == "standard":
        warnings.append("标准模式会进行更完整的多 Agent 协作，质量更高但更容易触发模型限流。")
    else:
        warnings.append("轻量模式会减少模型上下文、轮数和工具调用，适合演示、快速测试和额度紧张时使用。")
    return warnings


def friendly_error_summary(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "429" in lowered or "rate limit" in lowered or "限流" in message:
        return (
            "模型服务商触发限流（HTTP 429）。请等待额度窗口恢复后重试，"
            "或改用轻量模式、减少上传资料量、避免多人同时运行任务。"
        )
    return message[:1000]


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def date_only(value: Any) -> str:
    if value in {None, ""}:
        return "-"
    text = str(value)
    if text == "-":
        return "-"
    return text.replace("T", " ")[:19] if len(text) >= 19 else text.replace("T", " ")


def group_artifacts(artifacts: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Group downloadable artifacts for the job detail page."""

    group_defs = [
        ("core", "核心结果", {"all", "blackboard", "visual_data", "baseline_visual_data"}),
        ("schedule", "进度与网络", {"wbs", "schedule", "schedule_json", "cpm", "cpm_json", "network", "network_diagram"}),
        (
            "resource",
            "资源与约束",
            {"resources", "resource_load", "resource_load_json", "resource_resolution", "milestones", "milestones_json", "constraints", "adjustments_json"},
        ),
        ("preprocess", "资料预处理", {"preprocess_json", "preprocess_markdown"}),
        ("report", "图表与报告", {"summary", "visual_report", "gantt", "cpm_network", "cpm_float", "resource_heatmap", "resource_bars"}),
    ]
    by_key = {item["key"]: item for item in artifacts}
    grouped = []
    used: set[str] = set()
    for group_id, title, keys in group_defs:
        items = [item for item in artifacts if item["key"] in keys]
        if items:
            used.update(item["key"] for item in items)
            grouped.append({"id": group_id, "title": title, "artifacts": items})
    other_items = [item for item in artifacts if item["key"] not in used and item["key"] in by_key]
    if other_items:
        grouped.append({"id": "other", "title": "其他文件", "artifacts": other_items})
    return grouped
