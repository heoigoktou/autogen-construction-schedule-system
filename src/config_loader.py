"""Small config helpers for workflow scripts."""

from __future__ import annotations

from pathlib import Path


def load_paths_config(project_root: Path) -> dict[str, Path]:
    """Load config/paths.yaml and resolve values from project root.

    PyYAML is declared in pyproject, but this fallback keeps demos runnable in
    bare Python environments used during early handoff.
    """

    path = project_root / "config" / "paths.yaml"
    try:
        import yaml  # type: ignore[import-not-found]

        with path.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file)["paths"]
        return {key: project_root / value for key, value in raw.items()}
    except ModuleNotFoundError:
        return _load_simple_paths_yaml(path, project_root)


def _load_simple_paths_yaml(path: Path, project_root: Path) -> dict[str, Path]:
    """Parse the simple key/value paths.yaml used by this project."""

    result: dict[str, Path] = {}
    in_paths = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.strip() == "paths:":
            in_paths = True
            continue
        if not in_paths or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = project_root / value.strip().strip('"').strip("'")
    return result
