"""Сквозные контракты: ARCHITECTURE.md §2."""

from dataclasses import dataclass, field
from typing import Any, Mapping


# ---------- Канон ----------

@dataclass(frozen=True)
class Canon:
    meta: dict[str, Any]
    initial_state: dict[str, Any]
    flags: dict[str, dict[str, Any]]
    locations: dict[str, dict[str, Any]]
    npcs: dict[str, dict[str, Any]]
    items: dict[str, dict[str, Any]]
    use_targets: dict[str, dict[str, Any]]
    verbs: dict[str, dict[str, Any]]
    directions: dict[str, dict[str, Any]]
    reason_codes: set[str]
    win_condition: list[dict[str, Any]]


# ---------- Шаблоны ----------

@dataclass(frozen=True)
class Templates:
    canon_guarantee: set[tuple[str, str]]
    generic: dict[str, list[str]]
    specific: dict[str, dict[str, list[str]]]


# ---------- Событие (журнал) ----------

@dataclass(frozen=True)
class Event:
    event_id: int
    turn: int
    ts: str
    type: str                                     # "turn" | "presentation"
    payload: dict[str, Any]


# ---------- Состояние ----------

@dataclass(frozen=True)
class GameState:
    turn: int
    location: str
    inventory: tuple[str, ...]                    # D-60
    flags: Mapping[str, Any]                      # D-60: MappingProxyType
    won: bool
    game_over: bool

    def flag(self, name: str) -> Any:
        return self.flags.get(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "location": self.location,
            "inventory": list(self.inventory),
            "flags": dict(self.flags),
            "won": self.won,
            "game_over": self.game_over,
        }


# ---------- Команда ----------

@dataclass(frozen=True)
class Command:
    raw: str
    verb: str | None
    args: Mapping[str, str | None]
    parse_errors: tuple[str, ...] = ()            # D-62
    confidence: float = 0.0

    @property
    def recognized(self) -> bool:
        return self.verb is not None and not self.parse_errors


# ---------- Результат движка ----------

@dataclass(frozen=True)
class Outcome:
    applied: bool
    reason_code: str
    effects: tuple[dict[str, Any], ...] = ()
    details: Mapping[str, Any] | None = None


# ---------- Результат рендера ----------

@dataclass(frozen=True)
class RenderedText:
    text: str
    source: str                                   # "specific" | "generic"
    template: str
    warnings: tuple[str, ...] = ()