from __future__ import annotations

import io
import json
import socket
import urllib.error

import pytest

from anvil_and_ink.llm_client import OllamaClient


def _ok_payload(text="Вы идёте на север.", eval_count=12):
    return json.dumps({
        "model": "qwen2.5:7b",
        "response": text,
        "done": True,
        "eval_count": eval_count,
        "prompt_eval_count": 150,
    }).encode("utf-8")


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://localhost:11434/api/generate",
        code=code,
        msg=f"HTTP {code}",
        hdrs={},
        fp=io.BytesIO(body),
    )


@pytest.fixture
def client():
    return OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b", timeout=5.0)


def test_llm_c_1_success(monkeypatch, client):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: FakeResponse(_ok_payload("Вы идёте на север.", eval_count=12)),
    )
    result = client("PROMPT", None)
    assert result["ok"] is True
    assert result["text"] == "Вы идёте на север."
    assert result["tokens"] == 12
    assert "latency_ms" in result


def test_llm_c_2_empty_response(monkeypatch, client):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: FakeResponse(_ok_payload("   ", eval_count=0)),
    )
    result = client("PROMPT", None)
    assert result["ok"] is False
    assert result["error_class"] == "empty_response"


def test_llm_c_3_missing_response_field(monkeypatch, client):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: FakeResponse(json.dumps({"model": "x"}).encode("utf-8")),
    )
    result = client("PROMPT", None)
    assert result["ok"] is False
    assert result["error_class"] == "empty_response"


def test_llm_c_4_timeout(monkeypatch, client):
    def raise_timeout(req, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)
    result = client("PROMPT", None)
    assert result["ok"] is False
    assert result["error_class"] == "timeout_or_network"
    assert "timeout" in result["detail"]


def test_llm_c_5_connection_refused(monkeypatch, client):
    def raise_urlerror(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr("urllib.request.urlopen", raise_urlerror)
    result = client("PROMPT", None)
    assert result["ok"] is False
    assert result["error_class"] == "timeout_or_network"


def test_llm_c_6_http_404_config_error(monkeypatch, client):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(404, b'{"error":"model not found"}')),
    )
    result = client("PROMPT", None)
    assert result["ok"] is False
    assert result["error_class"] == "config_error"
    assert "404" in result["detail"]


def test_llm_c_7_http_429_as_timeout(monkeypatch, client):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(429, b"rate limited")),
    )
    result = client("PROMPT", None)
    assert result["ok"] is False
    assert result["error_class"] == "timeout_or_network"


def test_llm_c_8_http_500_as_timeout(monkeypatch, client):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(500, b"internal")),
    )
    result = client("PROMPT", None)
    assert result["ok"] is False
    assert result["error_class"] == "timeout_or_network"


def test_llm_c_9_invalid_json(monkeypatch, client):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: FakeResponse(b"not json {{{"),
    )
    result = client("PROMPT", None)
    assert result["ok"] is False
    assert result["error_class"] == "timeout_or_network"
    assert "invalid json" in result["detail"]


def test_llm_c_10_no_retry(monkeypatch, client):
    """Клиент не ретраит: urlopen вызывается ровно один раз даже при ошибке."""
    calls = {"n": 0}

    def counting_urlopen(req, timeout=None):
        calls["n"] += 1
        raise socket.timeout("timed out")

    monkeypatch.setattr("urllib.request.urlopen", counting_urlopen)
    client("PROMPT", None)
    assert calls["n"] == 1


def test_llm_c_11_env_overrides(monkeypatch):
    monkeypatch.setenv("ANVIL_LLM_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("ANVIL_LLM_BASE_URL", "http://example.com:11434/")
    monkeypatch.setenv("ANVIL_LLM_TIMEOUT", "3.5")
    c = OllamaClient()
    assert c.model == "qwen2.5:3b"
    assert c.base_url == "http://example.com:11434"  # trailing slash снят
    assert c.timeout == 3.5


def test_llm_c_12_payload_shape(monkeypatch, client):
    """Проверяем, что в тело запроса попадают model, prompt, stream=false, options."""
    captured = {}

    def capture_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(_ok_payload("ок"))

    monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)
    client("ТЕСТОВЫЙ ПРОМПТ", None)

    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["body"]["model"] == "qwen2.5:7b"
    assert captured["body"]["prompt"] == "ТЕСТОВЫЙ ПРОМПТ"
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["temperature"] == 0.6
    assert captured["body"]["options"]["num_predict"] == 200
    assert captured["timeout"] == 5.0