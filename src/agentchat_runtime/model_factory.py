"""Build AutoGen model clients from project model settings."""

from __future__ import annotations

from typing import Any

from agentchat_runtime.exceptions import ModelConfigurationError


def _is_kimi_model(model: str) -> bool:
    return model.strip().lower().startswith("kimi-")


def validate_live_model_settings(settings: dict[str, Any]) -> None:
    """Ensure production AgentChat has a real model endpoint."""

    provider = str(settings.get("provider") or "mock").lower()
    if provider in {"mock", "none"} or settings.get("mock_mode") is True:
        raise ModelConfigurationError(
            "生产 AgentChat 运行禁止 mock 模式，请配置 MODEL_PROVIDER/openai/openai_compatible。"
        )
    if not str(settings.get("model") or "").strip():
        raise ModelConfigurationError("未配置 OPENAI_MODEL，无法启动 AutoGen AgentChat。")
    if not str(settings.get("api_key") or "").strip():
        raise ModelConfigurationError(
            "未配置 MOONSHOT_API_KEY 或 OPENAI_API_KEY，无法启动 AutoGen AgentChat。"
        )


def build_openai_chat_completion_client(
    settings: dict[str, Any],
    *,
    vision: bool = False,
    thinking: str | None = None,
):
    """Create an AutoGen OpenAI-compatible chat completion client."""

    validate_live_model_settings(settings)
    try:
        from autogen_core.models import ModelFamily
        from autogen_ext.models.openai import OpenAIChatCompletionClient
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by dependency tests
        raise ModelConfigurationError(
            "缺少 AutoGen 依赖，请运行：python -m pip install -e \".[dev]\""
        ) from exc

    class NonEmptyAssistantContentClient(OpenAIChatCompletionClient):
        """Normalize empty assistant messages for strict OpenAI-compatible APIs."""

        def _process_create_args(self, *args: Any, **kwargs: Any) -> Any:
            create_params = super()._process_create_args(*args, **kwargs)
            for message in create_params.messages:
                if not isinstance(message, dict):
                    continue
                if message.get("role") != "assistant":
                    continue
                if message.get("tool_calls"):
                    continue
                content = message.get("content")
                if content is None or (isinstance(content, str) and not content.strip()):
                    message["content"] = " "
            return create_params

    model = str(settings["model"])
    timeout_seconds = max(float(settings.get("timeout_seconds") or 300), 300.0)
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": str(settings["api_key"]),
        "base_url": str(settings.get("base_url") or "https://api.openai.com/v1"),
        "timeout": timeout_seconds,
        "max_retries": int(settings.get("max_retries") or 2),
        "model_info": {
            "vision": vision,
            "function_calling": True,
            "json_output": True,
            "structured_output": True,
            "family": ModelFamily.UNKNOWN,
        },
    }
    if settings.get("temperature") is not None and not _is_kimi_model(model):
        kwargs["temperature"] = float(settings["temperature"])
    if _is_kimi_model(model):
        if thinking in {"enabled", "disabled"}:
            kwargs["extra_body"] = {"thinking": {"type": thinking}}
        kwargs["include_name_in_message"] = False
    return NonEmptyAssistantContentClient(**kwargs)
