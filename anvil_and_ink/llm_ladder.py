from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .prompt_builder import BuildError
from .templates_renderer import build_render_snapshot


# Ступень 4: универсальная заглушка. В норме недостижима.
UNIVERSAL_STUB = "Вы делаете это. Ничего примечательного не происходит."

# Заметка ретрая после отказа валидатора (D-26, D-68).
_RETRY_NOTE = "Твой предыдущий ответ был отклонён: {message}. Не повторяй эту ошибку."


@dataclass(frozen=True)
class LadderResult:
    final_text: str
    rung: Optional[int]              # 1, 2, 3, 4; None для UNKNOWN
    skip_reason: Optional[str]       # unknown_command | config_error
    fallback_reason: Optional[str]   # build_error | timeout_twice | validation_failed_twice | mixed
    diagnostics: Dict[str, Any]


def run(
    command,
    outcome,
    projection,
    canon,
    recent_events,
    turn,
    config,
    builder,
    client,
    validator,
    renderer,
    templates,
) -> LadderResult:
    """
    Лестница деградации. Максимум 2 вызова LLM на ход.

    Возвращает корректный текст при любом исходе: что бы ни случилось
    с моделью, игрок получит ответ.
    """

    calls: List[Dict[str, Any]] = []

    # Спецслучай UNKNOWN: лестница не запускается, клиент 0 раз.
    if command.verb == "UNKNOWN":
        try:
            snapshot = build_render_snapshot(command, outcome, projection, canon)
            text = renderer("UNKNOWN", "unknown", outcome, snapshot, turn, templates)
            rung = None
        except Exception:
            text = UNIVERSAL_STUB
            rung = 4
        return _result(text, rung, "unknown_command", None, calls)

    # Режим no-llm: клиент null → сразу ступень 3 (шаблон). См. D-69.
    if client is None:
        text, rung = _fallback(command, outcome, projection, canon, turn, renderer, templates)
        return _result(text, rung, None, None, calls)
    
    # --- Ступень 1: основной вызов ---
    try:
        prompt, manifest = builder(
            command, outcome, projection, canon, recent_events, turn, config
        )
    except BuildError:
        text, rung = _fallback(command, outcome, projection, canon, turn, renderer, templates)
        return _result(text, rung, None, "build_error", calls)

    first = client(prompt, config)
    first_record = _call_record(1, prompt, first)
    calls.append(first_record)

    first_failure = None   # "timeout" | "validation"
    first_reason = None

    if not first.get("ok"):
        # config_error не ретраится (D-27). rate_limit уже сведён клиентом
        # к timeout_or_network, поэтому всё кроме config_error — повторимо.
        if first.get("error_class") == "config_error":
            text, rung = _fallback(command, outcome, projection, canon, turn, renderer, templates)
            return _result(text, rung, "config_error", None, calls)
        first_failure = "timeout"
    else:
        verdict = validator.validate(first.get("text", ""), manifest)
        first_record["validator_result"] = "ok" if verdict.ok else "reject"
        if verdict.ok:
            return _result(first["text"], 1, None, None, calls)
        first_failure = "validation"
        first_reason = verdict.reason

    # --- Ступень 2: повтор (последний вызов LLM) ---
    retry_note = None
    if first_failure == "validation":
        retry_note = _RETRY_NOTE.format(message=first_reason.message)

    try:
        prompt2, manifest2 = builder(
            command, outcome, projection, canon, recent_events, turn, config,
            retry_note=retry_note,
        )
    except BuildError:
        text, rung = _fallback(command, outcome, projection, canon, turn, renderer, templates)
        return _result(text, rung, None, "build_error", calls)

    second = client(prompt2, config)
    second_record = _call_record(2, prompt2, second)
    calls.append(second_record)

    if not second.get("ok"):
        if second.get("error_class") == "config_error":
            text, rung = _fallback(command, outcome, projection, canon, turn, renderer, templates)
            return _result(text, rung, "config_error", None, calls)
        fallback_reason = "timeout_twice" if first_failure == "timeout" else "mixed"
        text, rung = _fallback(command, outcome, projection, canon, turn, renderer, templates)
        return _result(text, rung, None, fallback_reason, calls)

    verdict2 = validator.validate(second.get("text", ""), manifest2)
    second_record["validator_result"] = "ok" if verdict2.ok else "reject"
    if verdict2.ok:
        return _result(second["text"], 2, None, None, calls)

    fallback_reason = "mixed" if first_failure == "timeout" else "validation_failed_twice"
    text, rung = _fallback(command, outcome, projection, canon, turn, renderer, templates)
    return _result(text, rung, None, fallback_reason, calls)


def _fallback(command, outcome, projection, canon, turn, renderer, templates):
    """Ступень 3 (шаблон). Если рендерер упал — ступень 4 (заглушка)."""

    try:
        snapshot = build_render_snapshot(command, outcome, projection, canon)
        text = renderer(command.verb, outcome.reason_code, outcome, snapshot, turn, templates)
        return text, 3
    except Exception:
        return UNIVERSAL_STUB, 4


def _call_record(attempt, prompt, client_result):
    return {
        "attempt": attempt,
        "prompt": prompt,
        "response": client_result.get("text"),
        "latency_ms": client_result.get("latency_ms"),
        "tokens": client_result.get("tokens"),
        "error_class": client_result.get("error_class"),
        "validator_result": None,
    }


def _result(text, rung, skip_reason, fallback_reason, calls):
    diagnostics = {
        "rung": rung,
        "skip_reason": skip_reason,
        "fallback_reason": fallback_reason,
        "final_text": text,
        "calls": calls,
    }
    return LadderResult(
        final_text=text,
        rung=rung,
        skip_reason=skip_reason,
        fallback_reason=fallback_reason,
        diagnostics=diagnostics,
    )