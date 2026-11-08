from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional

from .engine import apply_command as engine_apply_command
from .llm_ladder import UNIVERSAL_STUB, run as ladder_run
from .loaders import load_canon, load_templates
from .models import Command, Event, Outcome, Primitive, TurnResult
from .parser import parse as parse_command
from .projection import (
    IntegrityError,
    ProjectionLookup,
    apply as projection_apply,
    build_initial,
)
from .prompt_builder import BuildError, PromptBuilderConfig, build as build_prompt
from .templates_renderer import build_render_snapshot, render as render_template
from .validator import Validator


MAX_INPUT_LENGTH = 500


@dataclass(frozen=True)
class SessionConfig:
    mode: str = "no-llm"            # no-llm | llm | broken-llm
    canon_path: str = "canon.yaml"
    templates_path: str = "templates.yaml"
    fuzzy_threshold: int = 85


class Session:
    """
    Composition root. Единственное место, где встречаются все компоненты
    и задан порядок их вызовов (12 шагов хода, см. specs/session.md).
    """

    def __init__(
        self,
        *,
        config,
        canon,
        templates,
        parse,
        apply_command,
        build,
        client,
        validator,
        render,
        ladder,
        snapshot_builder,
        state=None,
        events=None,
        turn=0,
        game_finished=False,
    ):
        self._config = config
        self._canon = canon
        self._templates = templates
        self._parse = parse
        self._apply_command = apply_command
        self._build = build
        self._client = client
        self._validator = validator
        self._render = render
        self._ladder = ladder
        self._snapshot_builder = snapshot_builder
        self._builder_config = PromptBuilderConfig()

        self._state = state if state is not None else build_initial(canon)
        self._lookup = ProjectionLookup(self._state, canon)
        self._events: List[Event] = list(events) if events else []
        self._turn_events: List[Event] = [e for e in self._events if e.kind == "turn"]
        self._turn = turn
        self._game_finished = game_finished
        self._aborted = False
        self._abort_reason: Optional[str] = None
        self._diagnostics: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Публичный контракт
    # ------------------------------------------------------------------

    def step(self, raw: str) -> TurnResult:
        if self._aborted:
            return TurnResult(
                text="",
                game_finished=self._game_finished,
                aborted=True,
                abort_reason=self._abort_reason,
            )

        self._turn += 1
        turn = self._turn

        # Шаг 1: валидация ввода. Пусто / слишком длинно → UNKNOWN.
        invalid_input = (raw is None) or (not raw.strip()) or (len(raw) > MAX_INPUT_LENGTH)

        if invalid_input:
            command = Command(verb="UNKNOWN", args={}, resolved={}, raw=raw or "")
            outcome = Outcome(result="fail", reason_code="unknown", applied=(), response_text=None)
        else:
            # Шаг 2: парсер. Падение → CRITICAL, трактуем как UNKNOWN.
            try:
                command = self._parse(raw, self._canon, self._lookup, self._config.fuzzy_threshold)
            except Exception:
                command = Command(verb="UNKNOWN", args={}, resolved={}, raw=raw)

            # Шаг 3: движок. Падение → CRITICAL, трактуем как UNKNOWN.
            try:
                outcome = self._apply_command(command, self._lookup, self._canon)
            except Exception:
                command = Command(verb="UNKNOWN", args={}, resolved={}, raw=raw)
                outcome = Outcome(result="fail", reason_code="unknown", applied=(), response_text=None)

        # Шаг 4: применить applied к проекции (атомарно).
        # Ошибка целостности → аварийная остановка сессии (D-47).
        try:
            new_state = projection_apply(self._state, list(outcome.applied))
            new_state = replace(new_state, turn=turn)
        except IntegrityError:
            self._aborted = True
            self._abort_reason = "integrity_error"
            return TurnResult(text="", game_finished=False, aborted=True, abort_reason="integrity_error")

        self._state = new_state
        self._lookup = ProjectionLookup(self._state, self._canon)

        # Снимок предыдущих ходов для блока RECENT — до записи текущего хода.
        recent_events = list(self._turn_events)

        # Шаг 5: запись turn-события (до вызова LLM — гарантия порядка).
        turn_event = Event(kind="turn", turn=turn, raw=raw, command=command, outcome=outcome)
        self._events.append(turn_event)
        self._turn_events.append(turn_event)

        # Шаг 6: проверка win_condition (до нарратора — D-48).
        if _win_condition_met(self._lookup, self._canon):
            self._game_finished = True

        # Шаги 7–9: наррация.
        if command.verb == "UNKNOWN":
            # Шаг 7: UNKNOWN рендерится шаблоном напрямую, лестница не запускается.
            try:
                snapshot = self._snapshot_builder(command, outcome, self._lookup, self._canon)
                final_text = self._render("UNKNOWN", "unknown", outcome, snapshot, turn, self._templates)
                rung = None
            except Exception:
                final_text = UNIVERSAL_STUB
                rung = 4
            self._diagnostics.append({
                "turn": turn,
                "rung": rung,
                "skip_reason": "unknown_command",
                "fallback_reason": None,
                "final_text": final_text,
                "calls": [],
            })
        else:
            # Шаги 8–9: сборщик промпта + лестница.
            result = self._ladder(
                command, outcome, self._lookup, self._canon, recent_events, turn,
                self._builder_config, self._build, self._client, self._validator,
                self._render, self._templates,
            )
            final_text = result.final_text
            rung = result.rung
            self._diagnostics.append(result.diagnostics)

        # Шаг 11: запись presentation-события.
        presentation_event = Event(kind="presentation", turn=turn, final_text=final_text, rung=rung)
        self._events.append(presentation_event)

        # Шаг 12: возврат результата.
        return TurnResult(
            text=final_text,
            game_finished=self._game_finished,
            aborted=False,
            abort_reason=None,
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for event in self._events:
                f.write(json.dumps(_event_to_dict(event), ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: str, config: SessionConfig) -> "Session":
        canon = load_canon(config.canon_path)
        templates = load_templates(
            config.templates_path,
            set(canon.reason_codes.keys()),
            set(canon.verbs.keys()),
        )

        events: List[Event] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(_event_from_dict(json.loads(line)))

        # Replay: применяем applied напрямую, не вызывая движок и парсер (D-21).
        state = build_initial(canon)
        turn_events = sorted((e for e in events if e.kind == "turn"), key=lambda e: e.turn)
        for event in turn_events:
            state = projection_apply(state, list(event.outcome.applied))
        max_turn = turn_events[-1].turn if turn_events else 0
        state = replace(state, turn=max_turn)
        
        lookup = ProjectionLookup(state, canon)
        # game_finished пересчитывается по проекции, а не берётся из лога.
        game_finished = _win_condition_met(lookup, canon)

        deps = _build_dependencies(config, canon, templates)
        return cls(
            config=config, canon=canon, templates=templates, state=state,
            events=events, turn=max_turn, game_finished=game_finished, **deps,
        )

    def history(self) -> List[str]:
        return [e.final_text for e in self._events if e.kind == "presentation"]

    @classmethod
    def start(cls, config: SessionConfig) -> "Session":
        canon = load_canon(config.canon_path)
        templates = load_templates(
            config.templates_path,
            set(canon.reason_codes.keys()),
            set(canon.verbs.keys()),
        )
        deps = _build_dependencies(config, canon, templates)
        return cls(
            config=config, canon=canon, templates=templates,
            state=build_initial(canon), events=[], turn=0,
            game_finished=False, **deps,
        )


def start(config: SessionConfig) -> Session:
    return Session.start(config)


# ----------------------------------------------------------------------
# Вспомогательные функции
# ----------------------------------------------------------------------


def _win_condition_met(lookup: ProjectionLookup, canon) -> bool:
    wc = canon.win_condition
    if wc is None:
        return False
    if wc.type == "flag":
        return lookup.flag(wc.flag) == wc.equals
    return False


def _build_dependencies(config, canon, templates):
    # Режим определяет клиента (D-69).
    # no-llm: клиент = None → лестница сразу ступень 3.
    # broken-llm: клиент = None, инжектируется извне тестом.
    # llm: реальный Ollama-клиент (ленивая инициализация, см. llm_client.py).
    if config.mode == "llm":
        from .llm_client import OllamaClient
        client = OllamaClient()
    else:
        client = None
    return dict(
        parse=parse_command,
        apply_command=engine_apply_command,
        build=build_prompt,
        client=client,
        validator=Validator(canon),
        render=render_template,
        ladder=ladder_run,
        snapshot_builder=build_render_snapshot,
    )


# --- Сериализация событий для save / load -----------------------------


def _primitive_to_dict(p: Primitive) -> dict:
    return {
        "type": p.type, "flag": p.flag, "value": p.value,
        "item": p.item, "count": p.count, "to": p.to,
    }


def _primitive_from_dict(d: dict) -> Primitive:
    return Primitive(
        type=d["type"], flag=d.get("flag"), value=d.get("value"),
        item=d.get("item"), count=d.get("count"), to=d.get("to"),
    )


def _outcome_to_dict(o: Outcome) -> dict:
    return {
        "result": o.result,
        "reason_code": o.reason_code,
        "applied": [_primitive_to_dict(p) for p in o.applied],
        "response_text": o.response_text,
        "seed": o.seed,
    }


def _outcome_from_dict(d: dict) -> Outcome:
    return Outcome(
        result=d["result"],
        reason_code=d["reason_code"],
        applied=tuple(_primitive_from_dict(p) for p in d.get("applied", [])),
        response_text=d.get("response_text"),
        seed=d.get("seed"),
    )


def _command_to_dict(c: Command) -> dict:
    return {"verb": c.verb, "args": dict(c.args), "resolved": dict(c.resolved), "raw": c.raw}


def _command_from_dict(d: dict) -> Command:
    return Command(
        verb=d["verb"], args=d.get("args", {}),
        resolved=d.get("resolved", {}), raw=d.get("raw", ""),
    )


def _event_to_dict(e: Event) -> dict:
    d = {"kind": e.kind, "turn": e.turn}
    if e.kind == "turn":
        # final_text намеренно не попадает в turn-запись (D-20).
        d["raw"] = e.raw
        d["command"] = _command_to_dict(e.command) if e.command is not None else None
        d["outcome"] = _outcome_to_dict(e.outcome) if e.outcome is not None else None
    else:
        d["final_text"] = e.final_text
        d["rung"] = e.rung
    return d


def _event_from_dict(d: dict) -> Event:
    if d["kind"] == "turn":
        command = _command_from_dict(d["command"]) if d.get("command") else None
        outcome = _outcome_from_dict(d["outcome"]) if d.get("outcome") else None
        return Event(kind="turn", turn=d["turn"], raw=d.get("raw"), command=command, outcome=outcome)
    return Event(kind="presentation", turn=d["turn"], final_text=d.get("final_text"), rung=d.get("rung"))