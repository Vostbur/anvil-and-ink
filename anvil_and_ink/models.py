"""Сквозные контракты. ARCHITECTURE.md §3. Имена полей — как в спеках."""

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


# ============================================================
# КАНОН (immutable, загружается один раз)
# ============================================================

@dataclass(frozen=True)
class Canon:
    meta: dict[str, Any]
    initial_state: dict[str, Any]
    flags: dict[str, dict[str, Any]]
    locations: dict[str, dict[str, Any]]
    npcs: dict[str, dict[str, Any]]
    items: dict[str, dict[str, Any]]
    use_targets: dict[str, dict[str, Any]]
    verbs: dict[str, dict[str, Any]]                       # {GO: {aliases, args}, ...}
    directions: dict[str, dict[str, Any]]
    reason_codes: set[str]                                 # ok, unknown + произвольные
    win_condition: list[dict[str, Any]]


# ============================================================
# ШАБЛОНЫ
# ============================================================

@dataclass(frozen=True)
class Templates:
    canon_guarantee: set[tuple[str, str]]                  # {(verb, "ok"), ...}
    generic: dict[str, list[str]]
    specific: dict[str, dict[str, list[str]]]


# ============================================================
# КОМАНДА (выход парсера)
# ============================================================
# verb всегда есть. Если не распознан — "UNKNOWN".
# args: id сущностей или None. resolved: для каждого аргумента — bool.
# Например, "взять ключ" при отсутствии ключа в каноне:
#   verb="TAKE", args={"item": None}, resolved={"item": False}

@dataclass(frozen=True)
class Command:
    verb: str
    raw: str
    args: Mapping[str, Any] = field(default_factory=dict)
    resolved: Mapping[str, bool] = field(default_factory=dict)

    @property
    def is_unknown(self) -> bool:
        return self.verb == "UNKNOWN"


# ============================================================
# ПРИМИТИВЫ (applied в Outcome — это их список)
# ============================================================
# Канонические (могут быть в каноне, в effect'ах):
#   set_flag(flag, value)
#   add_to_inventory(item, count)
#   remove_from_inventory(item, count)
# Внутренние (только движок генерирует; в каноне не встречаются):
#   add_to_location(loc, item, count)
#   remove_from_location(loc, item, count)
#   change_location(to)
#
# Представление: dict с "type" и полями. Immutable на уровне конвенции.

Primitive = dict[str, Any]


# ============================================================
# OUTCOME (выход движка)
# ============================================================
# result: "ok" | "fail"
# reason_code: из canon.reason_codes
# applied: список примитивов. При fail — [].
#   При ok — непустой, кроме LOOK (пустой).
# response_text: canon-текст (response / response_on_fail) или None.
# seed: null в MVP (зарезервировано для RNG).

@dataclass(frozen=True)
class Outcome:
    result: Literal["ok", "fail"]
    reason_code: str
    applied: tuple[Primitive, ...] = ()
    response_text: str | None = None
    seed: int | None = None

    @property
    def ok(self) -> bool:
        return self.result == "ok"


# ============================================================
# СОСТОЯНИЕ (проекция)
# ============================================================
# inventory, item_placement, stats: словари без нулевых значений.
# npc_locations: {npc_id: loc_id}, в MVP статичен.

@dataclass(frozen=True)
class GameState:
    turn: int
    location: str
    inventory: Mapping[str, int]                            # {item_type_id: count}
    stats: Mapping[str, int]                                # {hp: 10}
    flags: Mapping[str, Any]                                # {flag_id: bool|number}
    item_placement: Mapping[str, Mapping[str, int]]         # {loc_id: {item_type_id: count}}
    npc_locations: Mapping[str, str]                        # {npc_id: loc_id}

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "location": self.location,
            "inventory": dict(self.inventory),
            "stats": dict(self.stats),
            "flags": dict(self.flags),
            "item_placement": {loc: dict(items) for loc, items in self.item_placement.items()},
            "npc_locations": dict(self.npc_locations),
        }


# ============================================================
# СОБЫТИЕ (журнал)
# ============================================================
# kind: "turn" | "presentation"
# Для "turn":   payload = {raw, command, outcome}
# Для "presentation": payload = {final_text, rung, ...}
# Единственное, что нужно проекции — outcome.applied и turn.

@dataclass(frozen=True)
class Event:
    turn: int
    kind: Literal["turn", "presentation"]
    payload: dict[str, Any]


# ============================================================
# МАНИФЕСТ (то, что «видел» нарратор)
# ============================================================
# allowed_names: нормализованные имена (lowercase, ё→е)
# allowed_numbers: содержательные числа из сцены и хода
# mode: "look" | "describe"
# verb, reason_code: для контекста

@dataclass(frozen=True)
class Manifest:
    allowed_names: frozenset[str]
    allowed_numbers: frozenset[int]
    mode: Literal["look", "describe"]
    verb: str
    reason_code: str


# ============================================================
# ПРОМПТ
# ============================================================

@dataclass(frozen=True)
class Prompt:
    text: str
    manifest: Manifest


# ============================================================
# РЕЗУЛЬТАТ ХОДА (возврат session.step)
# ============================================================

@dataclass(frozen=True)
class TurnResult:
    text: str
    game_finished: bool = False
    aborted: bool = False
    abort_reason: str | None = None


# ============================================================
# РЕЗУЛЬТАТ ВАЛИДАТОРА
# ============================================================

@dataclass(frozen=True)
class ValidationReason:
    code: Literal[
        "empty", "too_short", "too_long",
        "mechanics_leak", "unknown_number", "unknown_name",
    ]
    message: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: ValidationReason | None = None


# ============================================================
# РЕЗУЛЬТАТ ЛЕСТНИЦЫ
# ============================================================

@dataclass(frozen=True)
class LadderResult:
    final_text: str
    rung: int | None                                        # 1..4, null при UNKNOWN
    skip_reason: str | None = None                          # "unknown_command" | "config_error"
    fallback_reason: str | None = None                      # "timeout_twice" | "validation_failed_twice" | "mixed" | "build_error"
    diagnostics: dict[str, Any] = field(default_factory=dict)