from __future__ import annotations

from pathlib import Path

from tools.visualization_tools import (
    build_demo_visualization_rows,
    generate_schedule_visualizations_from_rows,
)


def test_generate_schedule_visualizations(tmp_path: Path) -> None:
    result = generate_schedule_visualizations_from_rows(
        build_demo_visualization_rows(),
        tmp_path / "visualizations",
        title="Pytest",
    )

    expected = {
        "gantt_chart",
        "cpm_float_chart",
        "cpm_network",
        "cpm_network_mermaid",
        "resource_load_heatmap",
        "resource_load_bars",
        "report",
        "manifest",
    }
    assert expected.issubset(result.artifacts)
    assert not result.warnings
    for artifact in result.artifacts.values():
        assert artifact.exists()
        assert artifact.stat().st_size > 0
