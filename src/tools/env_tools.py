"""Environment and model configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def load_dotenv(project_root: Path) -> dict[str, str]:
    """Load a simple `.env` file into `os.environ` without overriding set values."""

    env_path = project_root / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def load_model_yaml(project_root: Path) -> dict[str, Any]:
    """Load `config/model.yaml` with a tiny fallback parser."""

    path = project_root / "config" / "model.yaml"
    if not path.exists():
        return {}

    try:
        import yaml  # type: ignore[import-not-found]

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        model = raw.get("model") or {}
        return dict(model)
    except ModuleNotFoundError:
        return _load_simple_model_yaml(path)


def build_model_settings(project_root: Path) -> dict[str, Any]:
    """Combine `.env` and YAML settings for optional model-backed extraction."""

    load_dotenv(project_root)
    yaml_model = load_model_yaml(project_root)
    provider = os.environ.get("MODEL_PROVIDER") or str(yaml_model.get("provider") or "mock")
    model_name = os.environ.get("OPENAI_MODEL") or str(yaml_model.get("name") or "")
    api_style = os.environ.get("MODEL_API_STYLE") or str(
        yaml_model.get("api_style") or "responses"
    )
    base_url = os.environ.get("OPENAI_BASE_URL") or str(
        yaml_model.get("base_url") or "https://api.openai.com/v1"
    )
    api_key = os.environ.get("MOONSHOT_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    disable_thinking = _bool_env(
        "KIMI_DISABLE_THINKING",
        yaml_model.get("disable_thinking"),
        _is_kimi_model(model_name),
    )
    temperature = _optional_float_env("OPENAI_TEMPERATURE", yaml_model.get("temperature"))
    timeout_seconds = _int_env("OPENAI_TIMEOUT_SECONDS", yaml_model.get("timeout_seconds"), 60)
    max_retries = _int_env("OPENAI_MAX_RETRIES", yaml_model.get("max_retries"), 2)
    mock_mode = _bool_env("MODEL_MOCK_MODE", yaml_model.get("mock_mode"), provider == "mock")

    return {
        "provider": provider.strip().lower(),
        "model": model_name.strip(),
        "api_style": api_style.strip().lower(),
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "temperature": temperature,
        "timeout_seconds": timeout_seconds,
        "max_retries": max_retries,
        "mock_mode": mock_mode,
        "disable_thinking": disable_thinking,
    }


def model_is_enabled(settings: dict[str, Any]) -> bool:
    """Return whether settings are sufficient for a live model call."""

    provider = str(settings.get("provider") or "mock").lower()
    model = str(settings.get("model") or "")
    api_key = str(settings.get("api_key") or "")
    if provider in {"mock", "none"} or settings.get("mock_mode") is True:
        return False
    return bool(model and api_key)


def _load_simple_model_yaml(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    in_model = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "model:":
            in_model = True
            continue
        if not in_model or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"').strip("'")
        if value.lower() == "true":
            parsed: Any = True
        elif value.lower() == "false":
            parsed = False
        else:
            parsed = value
        result[key.strip()] = parsed
    return result


def _bool_env(name: str, yaml_value: Any, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        raw = yaml_value
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _optional_float_env(name: str, yaml_value: Any) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        raw = yaml_value
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in {"", "none", "null"}:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int_env(name: str, yaml_value: Any, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        raw = yaml_value
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_env(name: str, yaml_value: Any, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        raw = yaml_value
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _is_kimi_model(model_name: str) -> bool:
    return model_name.strip().lower().startswith("kimi-")
