from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Set, Tuple

@dataclass(frozen=True)
class Flag:
    type: str
    description: str

@dataclass(frozen=True)
class Condition:
    type: str
    flag: Optional[str] = None
    equals: Optional[Any] = None
    item: Optional[str] = None
    min: Optional[int] = None

@dataclass(frozen=True)
class Effect:
    type: str
    flag: Optional[str] = None
    value: Optional[Any] = None
    item: Optional[str] = None
    count: Optional[int] = None

@dataclass(frozen=True)
class Exit:
    direction: str
    to: str
    condition: Tuple[Condition, ...] = field(default_factory=tuple)
    effects: Tuple[Effect, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class Location:
    id: str
    name: str
    description: str
    exits: Tuple[Exit, ...] = field(default_factory=tuple)
    entities: Tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class Topic:
    key: str
    response: str
    response_on_fail: Optional[str] = None
    condition: Tuple[Condition, ...] = field(default_factory=tuple)
    effects: Tuple[Effect, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class Npc:
    id: str
    name: str
    topics: Tuple[Topic, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class UseRule:
    target: str
    condition: Tuple[Condition, ...] = field(default_factory=tuple)
    effects: Tuple[Effect, ...] = field(default_factory=tuple)
    response: Optional[str] = None
    response_on_fail: Optional[str] = None

@dataclass(frozen=True)
class Item:
    id: str
    name: str
    takeable: bool
    use: Tuple[UseRule, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class UseTarget:
    id: str
    name: str
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    exit: Optional[Tuple[str, str]] = None

@dataclass(frozen=True)
class Verb:
    id: str
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    args: Tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class Direction:
    id: str
    name: str
    aliases: Tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class Meta:
    schema_version: int
    title: str
    start_location: str
    note: Optional[str] = None

@dataclass(frozen=True)
class InitialState:
    location: str
    stats: Mapping[str, int] = field(default_factory=dict)
    inventory: Mapping[str, int] = field(default_factory=dict)
    flags: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class WinCondition:
    type: str
    flag: Optional[str] = None
    equals: Optional[Any] = None

@dataclass(frozen=True)
class Canon:
    meta: Meta
    initial_state: InitialState
    flags: Mapping[str, Flag]
    locations: Mapping[str, Location]
    npcs: Mapping[str, Npc]
    items: Mapping[str, Item]
    use_targets: Mapping[str, UseTarget]
    verbs: Mapping[str, Verb]
    directions: Mapping[str, Direction]
    reason_codes: Mapping[str, str]
    win_condition: WinCondition

class LoaderError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))

class CanonError(LoaderError): pass
class TemplatesError(LoaderError): pass

@dataclass(frozen=True)
class Templates:
    canon_guarantee: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    generic: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    specific: Mapping[str, Mapping[str, Tuple[str, ...]]] = field(default_factory=dict)

# --- Сквозные контракты (ARCHITECTURE.md §3) ---

@dataclass(frozen=True)
class Command:
    verb: str
    args: Mapping[str, Optional[str]] = field(default_factory=dict)
    resolved: Mapping[str, bool] = field(default_factory=dict)
    raw: str = ""

@dataclass(frozen=True)
class Primitive:
    type: str
    flag: Optional[str] = None
    value: Optional[Any] = None
    item: Optional[str] = None
    count: Optional[int] = None
    to: Optional[str] = None

@dataclass(frozen=True)
class Outcome:
    result: str  # 'ok' or 'fail'
    reason_code: str
    applied: Tuple[Primitive, ...] = field(default_factory=tuple)
    response_text: Optional[str] = None
    seed: Optional[int] = None

@dataclass(frozen=True)
class Manifest:
    allowed_names: Set[str] = field(default_factory=set)
    allowed_numbers: Set[int] = field(default_factory=set)
    mode: str = "describe"
    verb: str = ""
    reason_code: str = ""

@dataclass(frozen=True)
class TurnResult:
    text: str
    game_finished: bool
    aborted: bool
    abort_reason: Optional[str] = None

@dataclass(frozen=True)
class Event:
    kind: str  # 'turn' | 'presentation'
    turn: int
    raw: Optional[str] = None
    command: Optional[Command] = None
    outcome: Optional[Outcome] = None
    final_text: Optional[str] = None
    rung: Optional[int] = None

@dataclass(frozen=True)
class GameState:
    turn: int
    location: str
    inventory: Mapping[str, int]
    stats: Mapping[str, int]
    flags: Mapping[str, Any]
    item_placement: Mapping[str, Mapping[str, int]]
    npc_locations: Mapping[str, str]