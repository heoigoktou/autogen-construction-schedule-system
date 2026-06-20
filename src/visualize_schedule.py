"""Generate visual outputs from an AgentChat schedule run."""

from __future__ import annotations

import argparse
from pathlib import Path

from blackboard.excel_store import ExcelBlackboardStore
from tools.case_context import ensure_case_directories, resolve_case_context
from tools.visualization_tools import (
    DEFAULT_OUTPUT_DIRNAME,
    build_demo_visualization_rows,
    generate_schedule_visualizations,
    generate_schedule_visualizations_from_rows,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate Gantt, CPM, and resource load visualizations from "
            "AgentChat schedule outputs."
        )
    )
    parser.add_argument(
        "--blackboard",
        default=None,
        help="Optional blackboard workbook path. Defaults to data/blackboard/real_case_blackboard.xlsx.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional visualization output directory. Defaults to outputs/real_case/visualizations.",
    )
    parser.add_argument(
        "--input-dir",
        default="data/input_docs",
        help="Input document directory used only to resolve the standard real-case context.",
    )
    parser.add_argument(
        "--title",
        default="Real Case AgentChat Schedule",
        help="Title prefix used on generated charts.",
    )
    parser.add_argument(
        "--demo-data",
        action="store_true",
        help="Use fake built-in schedule data instead of reading the current Excel blackboard.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    context = resolve_case_context(PROJECT_ROOT, input_dir=args.input_dir)
    ensure_case_directories(context)

    blackboard_path = Path(args.blackboard) if args.blackboard else context.blackboard_path
    if not blackboard_path.is_absolute():
        blackboard_path = PROJECT_ROOT / blackboard_path
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else context.outputs_root / DEFAULT_OUTPUT_DIRNAME
    )
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    store = ExcelBlackboardStore(blackboard_path)
    if args.demo_data:
        result = generate_schedule_visualizations_from_rows(
            build_demo_visualization_rows(),
            output_dir,
            title=args.title,
        )
    else:
        store.initialize()
        result = generate_schedule_visualizations(store, output_dir, title=args.title)

    print(f"visualizations completed: {result.output_dir}")
    for name, artifact in result.artifacts.items():
        print(f"- {name}: {artifact}")
    for warning in result.warnings:
        print(f"warning: {warning}")


if __name__ == "__main__":
    main()
