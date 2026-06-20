"""Small model client used by the real-case document extraction flow."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class ModelClientError(RuntimeError):
    """Raised when a live model call fails."""


class ModelClient:
    """Minimal OpenAI/OpenAI-compatible HTTP client using the standard library."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.provider = str(settings.get("provider") or "mock").lower()
        self.model = str(settings.get("model") or "")
        self.base_url = str(settings.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = str(settings.get("api_key") or "")
        self.api_style = str(settings.get("api_style") or "responses").lower()
        self.timeout_seconds = int(settings.get("timeout_seconds") or 60)
        self.max_retries = int(settings.get("max_retries") or 2)
        self.temperature = settings.get("temperature")
        self.disable_thinking = bool(
            settings.get("disable_thinking") or _is_kimi_model(self.model)
        )

    def generate_json(
        self,
        *,
        instructions: str,
        prompt: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a JSON object from the configured model endpoint."""

        if not self.api_key:
            raise ModelClientError("未配置 OPENAI_API_KEY")
        if not self.model:
            raise ModelClientError("未配置 OPENAI_MODEL 或 config/model.yaml 中的模型名称")

        if self.api_style == "chat_completions" or self.provider == "openai_compatible":
            text = self._chat_completions(instructions=instructions, prompt=prompt, schema=schema)
        else:
            text = self._responses(instructions=instructions, prompt=prompt, schema=schema)
        return _parse_json_object(text)

    def _responses(
        self,
        *,
        instructions: str,
        prompt: str,
        schema: dict[str, Any] | None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
        }
        if self.temperature is not None and not _is_kimi_model(self.model):
            payload["temperature"] = float(self.temperature)
        if schema:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "parameter_extraction",
                    "schema": schema,
                    "strict": False,
                }
            }
        response = self._post_json(f"{self.base_url}/responses", payload)
        return _extract_responses_text(response)

    def _chat_completions(
        self,
        *,
        instructions: str,
        prompt: str,
        schema: dict[str, Any] | None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
        }
        if self.temperature is not None and not _is_kimi_model(self.model):
            payload["temperature"] = float(self.temperature)
        if self.disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        if schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "parameter_extraction",
                    "schema": schema,
                    "strict": False,
                },
            }
        response = self._post_json(f"{self.base_url}/chat/completions", payload)
        try:
            return str(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError("模型响应中未找到 choices[0].message.content") from exc

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:  # nosec B310 - URL is user-configured API endpoint
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error_text = exc.read().decode("utf-8", errors="replace")
                last_error = ModelClientError(f"模型接口 HTTP {exc.code}: {error_text[:600]}")
                if 400 <= exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(1.5 * (attempt + 1))
        raise ModelClientError(str(last_error or "模型接口调用失败"))


def _extract_responses_text(response: dict[str, Any]) -> str:
    if response.get("output_text"):
        return str(response["output_text"])
    texts: list[str] = []
    for item in response.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(str(content["text"]))
    if texts:
        return "\n".join(texts)
    raise ModelClientError("模型响应中未找到文本输出")


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ModelClientError("模型输出不是 JSON object")
    return parsed


def _is_kimi_model(model: str) -> bool:
    return model.strip().lower().startswith("kimi-")
