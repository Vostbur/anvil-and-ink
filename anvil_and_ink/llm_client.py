from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT = 30.0
DEFAULT_TEMPERATURE = 0.6
DEFAULT_MAX_TOKENS = 200


class OllamaClient:
    """
    Клиент Ollama /api/generate.

    Контракт (см. specs/llm_ladder.md):
        client(prompt, config) -> {ok: True, text, latency_ms, tokens}
                                | {ok: False, error_class, detail, latency_ms, tokens: None}

    Не ретраит, не валидирует, не строит промпт.
    Ленивая инициализация: соединение не открывается до первого вызова.
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.base_url = (base_url or os.environ.get("ANVIL_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("ANVIL_LLM_MODEL") or DEFAULT_MODEL
        self.timeout = timeout if timeout is not None else float(os.environ.get("ANVIL_LLM_TIMEOUT", DEFAULT_TIMEOUT))
        self.temperature = temperature if temperature is not None else DEFAULT_TEMPERATURE
        self.max_tokens = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS

    def __call__(self, prompt: str, config: Any = None) -> Dict[str, Any]:
        # config может нести переопределения (PromptBuilderConfig не имеет этих полей,
        # но тесты/живая сессия могут передать dict или объект с .timeout/.model).
        timeout = self.timeout
        model = self.model
        temperature = self.temperature
        max_tokens = self.max_tokens
        if config is not None:
            timeout = getattr(config, "llm_timeout", timeout)
            model = getattr(config, "llm_model", model)
            temperature = getattr(config, "llm_temperature", temperature)
            max_tokens = getattr(config, "llm_max_tokens", max_tokens)

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        start = time.monotonic()
        try:
            raw = self._post("/api/generate", payload, timeout)
        except urllib.error.HTTPError as e:
            latency = int((time.monotonic() - start) * 1000)
            return self._http_error_result(e, latency)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            latency = int((time.monotonic() - start) * 1000)
            return {
                "ok": False,
                "error_class": "timeout_or_network",
                "detail": _exception_detail(e),
                "latency_ms": latency,
                "tokens": None,
            }

        latency = int((time.monotonic() - start) * 1000)

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            return {
                "ok": False,
                "error_class": "timeout_or_network",
                "detail": f"invalid json: {e}",
                "latency_ms": latency,
                "tokens": None,
            }

        text = data.get("response", "")
        if not isinstance(text, str) or not text.strip():
            return {
                "ok": False,
                "error_class": "empty_response",
                "detail": "response is empty or whitespace",
                "latency_ms": latency,
                "tokens": None,
            }

        # tokens: completion tokens от Ollama (eval_count).
        tokens = data.get("eval_count")
        return {
            "ok": True,
            "text": text,
            "latency_ms": latency,
            "tokens": tokens,
        }

    def _post(self, path: str, payload: Dict[str, Any], timeout: float) -> str:
        url = self.base_url + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    def _http_error_result(self, e: urllib.error.HTTPError, latency: int) -> Dict[str, Any]:
        code = e.code
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            detail = str(e.reason)

        # 429 = rate_limit → трактуется как timeout_or_network (ретраибельно, см. llm_ladder.md).
        # 5xx = серверная ошибка → тоже timeout_or_network.
        # 4xx кроме 429 = config_error (модель не найдена, плохой запрос — неретраибельно).
        if code == 429 or code >= 500:
            error_class = "timeout_or_network"
        elif 400 <= code < 500:
            error_class = "config_error"
        else:
            error_class = "timeout_or_network"

        return {
            "ok": False,
            "error_class": error_class,
            "detail": f"HTTP {code}: {detail}",
            "latency_ms": latency,
            "tokens": None,
        }


def _exception_detail(e: Exception) -> str:
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, socket.timeout):
            return "timeout"
        return f"url error: {reason}"
    if isinstance(e, socket.timeout):
        return "timeout"
    return f"{type(e).__name__}: {e}"