"""Run the real-case end-to-end workflow.

Flow:
1. read source documents from data/input_docs
2. extract parameter_checklist with rules and optional live model
3. write project_parameters through DataParserAgent
4. generate WBS/resource/schedule/constraint/adjustment outputs
"""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Any

from agentchat_runtime.workflow import AgentChatWorkflow
from blackboard.excel_store import ExcelBlackboardStore
from tools.case_context import archive_case_state, ensure_case_directories, resolve_case_context
from tools.document_tools import read_source_documents
from tools.env_tools import build_model_settings
from tools.schedule_tools import (
    export_rows_to_xlsx,
    write_network_diagram,
    write_summary_asset,
)
from tools.visualization_tools import DEFAULT_OUTPUT_DIRNAME, generate_schedule_visualizations

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the real-case workflow."""

    parser = argparse.ArgumentParser(description="Run the real-case multi-agent workflow.")
    parser.add_argument(
        "--input-dir",
        default="data/input_docs",
        help="可选：覆盖默认资料目录。默认使用 data/input_docs。",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="跳过运行前自动归档。",
    )
    parser.add_argument(
        "--install-oda-if-missing",
        action="store_true",
        help="遇到 DWG 且未安装 ODA File Converter 时，尝试通过 winget 自动安装。",
    )
    parser.add_argument(
        "--dwg-timeout-seconds",
        type=int,
        default=120,
        help="DWG 转换超时时间，默认 120 秒。",
    )
    parser.add_argument(
        "--skip-visualizations",
        action="store_true",
        help="Skip post-run Gantt/CPM/resource visualization generation.",
    )
    return parser.parse_args()


def setup_logging(runtime_log: Path) -> None:
    """Configure runtime logging for the real-case workflow."""

    runtime_log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[logging.FileHandler(runtime_log, encoding="utf-8"), logging.StreamHandler()],
    )
    for noisy_logger in ("autogen_core", "autogen_core.events", "openai", "httpx"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def main() -> None:
    args = parse_args()
    context = resolve_case_context(
        PROJECT_ROOT,
        input_dir=args.input_dir,
    )
    ensure_case_directories(context)
    archive_result = None if args.no_archive else archive_case_state(context)

    setup_logging(context.runtime_log)
    model_settings = build_model_settings(PROJECT_ROOT)

    store = ExcelBlackboardStore(context.blackboard_path)
    store.initialize()

    source_docs_dir = context.input_docs_dir
    documents = read_source_documents(
        source_docs_dir,
        dwg_conversion_dir=str(context.tmp_dir / "dwg_conversion"),
        install_oda_if_missing=args.install_oda_if_missing,
        dwg_timeout_seconds=args.dwg_timeout_seconds,
    )
    agentchat_result = AgentChatWorkflow(
        store=store,
        model_settings=model_settings,
        documents=documents,
    ).run().to_dict()

    wbs_rows = store.read_rows("wbs_tasks_final")
    resource_rows = store.read_rows("resource_plan_final")
    schedule_rows = store.read_rows("schedule_initial")
    cpm_rows = store.read_rows("cpm_analysis")
    edge_rows = store.read_rows("network_edges")
    milestone_rows = store.read_rows("milestone_check")
    start_date = _project_start_date(store.read_rows("project_parameters"))

    real_case_dir = context.schedule_dir
    export_rows_to_xlsx(
        store.read_rows("parameter_checklist"),
        real_case_dir / "参数检查清单_自动抽取.xlsx",
    )
    export_rows_to_xlsx(
        store.read_rows("project_parameters"),
        real_case_dir / "项目参数_自动解析.xlsx",
    )
    for sheet_name in (
        "document_sections",
        "document_tables",
        "extracted_facts",
        "parameter_audit",
        "assumption_register",
        "quality_gates",
    ):
        export_rows_to_xlsx(
            store.read_rows(sheet_name),
            real_case_dir / f"{sheet_name}.xlsx",
            sheet_name,
        )
    export_rows_to_xlsx(wbs_rows, real_case_dir / "WBS工序分解表.xlsx", "wbs_tasks_final")
    export_rows_to_xlsx(resource_rows, real_case_dir / "资源需求表.xlsx", "resource_plan_final")
    export_rows_to_xlsx(schedule_rows, real_case_dir / "初始施工进度计划.xlsx", "schedule_initial")
    export_rows_to_xlsx(cpm_rows, real_case_dir / "关键线路分析表.xlsx", "cpm_analysis")
    export_rows_to_xlsx(edge_rows, real_case_dir / "网络计划关系表.xlsx", "network_edges")
    export_rows_to_xlsx(
        milestone_rows,
        real_case_dir / "节点工期符合性检查表.xlsx",
        "milestone_check",
    )
    export_rows_to_xlsx(
        store.read_rows("constraint_check"),
        real_case_dir / "约束校核结果.xlsx",
        "constraint_check",
    )
    export_rows_to_xlsx(
        store.read_rows("adjustment_plan"),
        real_case_dir / "动态调整候选方案.xlsx",
        "adjustment_plan",
    )
    write_network_diagram(real_case_dir / "network_diagram.md", wbs_rows, edge_rows, cpm_rows)
    write_summary_asset(
        context.report_assets_dir / "real_case_initial_schedule_summary.md",
        schedule_rows,
        cpm_rows,
        milestone_rows,
    )
    visualization_result = None
    if not args.skip_visualizations:
        visualization_result = generate_schedule_visualizations(
            store,
            context.outputs_root / DEFAULT_OUTPUT_DIRNAME,
            title="Real Case AgentChat Schedule",
        )
    _write_transcript(
        context.demo_transcripts_dir / "real_case_workflow.md",
        {
            "case_id": context.case_id,
            "source_docs_dir": source_docs_dir,
            "blackboard": context.blackboard_path,
            "model_provider": model_settings.get("provider"),
            "model_enabled": True,
            "start_date": start_date.isoformat(),
            "archive_result": archive_result,
            "documents": [
                {"name": document.name, "chars": len(document.text), "warning": document.warning}
                for document in documents
            ],
            "agentchat_result": agentchat_result,
            "output_dir": real_case_dir,
            "visualization_result": visualization_result.to_dict()
            if visualization_result is not None
            else None,
        },
    )

    print(f"real case workflow completed: {real_case_dir}")
    if visualization_result is not None:
        print(f"visualizations completed: {visualization_result.output_dir}")


def _project_start_date(project_parameters: list[dict[str, Any]]) -> date:
    for row in project_parameters:
        if (
            str(row.get("parameter_id") or "") == "P-002"
            or str(row.get("name") or "") == "开工日期"
        ):
            parsed = _parse_date(str(row.get("value") or ""))
            if parsed:
                return parsed
            raise RuntimeError(f"开工日期 P-002 非法：{row.get('value')}")
    raise RuntimeError("缺少真实开工日期 P-002，禁止使用默认开工日期。")


def _parse_date(value: str) -> date | None:
    match = __import__("re").search(r"([0-9]{4})[-年/.]([0-9]{1,2})[-月/.]([0-9]{1,2})", value)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _write_transcript(path: Path, details: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Real Case Workflow Transcript",
        "",
        f"- Case ID: `{details['case_id']}`",
        f"- Source documents: `{details['source_docs_dir']}`",
        f"- Blackboard: `{details['blackboard']}`",
        f"- Model provider: `{details['model_provider']}`",
        f"- Live model enabled: `{details['model_enabled']}`",
        f"- Schedule start date: `{details['start_date']}`",
        f"- Output dir: `{details['output_dir']}`",
        f"- Visualizations: `{details['visualization_result']}`",
        f"- Archive: `{details['archive_result']}`",
        "",
        "## Agent Results",
        "",
    ]
    lines.extend(["## Documents", "", f"```text\n{details['documents']}\n```", ""])
    for key in ("agentchat_result",):
        lines.extend([f"### {key}", "", f"```text\n{details[key]}\n```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


if __name__ == "__main__":
    main()
