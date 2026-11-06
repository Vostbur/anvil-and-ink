from dataclasses import dataclass
from typing import Any, Mapping

@dataclass(frozen=True)
class Event:
    event_id: int
    turn: int
    ts: str
    type: str           # "turn" | "presentation"
    payload: dict[str, Any]

@dataclass(frozen=True)
class GameState:
    turn: int
    location: str
    inventory: tuple[str, ...]
    flags: Mapping[str, Any]
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