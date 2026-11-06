import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anvil_and_ink.models import Canon, Event, GameState
from anvil_and_ink.projection import ProjectionError, project


class SessionLog:
    """Append-only JSONL. Сейв = этот файл + канон."""

    def __init__(self, path: Path):
        self.path = path
        self._next_id = 0
        self._last_turn = 0
        if path.exists():
            for event in self.read_all():
                self._next_id = max(self._next_id, event.event_id + 1)
                self._last_turn = max(self._last_turn, event.turn)

    def read_all(self) -> list[Event]:
        if not self.path.exists():
            return []
        events: list[Event] = []
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ProjectionError(f"{self.path}:{lineno}: {e}")
                try:
                    events.append(Event(**data))
                except TypeError as e:
                    raise ProjectionError(f"{self.path}:{lineno}: malformed event ({e})")
        return events

    def _append(self, event: Event) -> None:
        self._next_id = max(self._next_id, event.event_id + 1)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def append_turn(self, turn: int, command: dict, outcome: dict) -> Event:
        if turn != self._last_turn + 1:
            raise ValueError(f"turn {turn} != expected {self._last_turn + 1}")
        event = Event(
            event_id=self._next_id,
            turn=turn,
            ts=datetime.now(timezone.utc).isoformat(),
            type="turn",
            payload={"command": command, "outcome": outcome},
        )
        self._append(event)
        self._last_turn = turn
        return event

    def append_presentation(self, turn: int, final_text: str, source: str, **extra) -> Event:
        if turn != self._last_turn:
            raise ValueError(
                f"presentation turn {turn} != current turn {self._last_turn}"
            )
        event = Event(
            event_id=self._next_id,
            turn=turn,
            ts=datetime.now(timezone.utc).isoformat(),
            type="presentation",
            payload={"final_text": final_text, "source": source, **extra},
        )
        self._append(event)
        return event


def load_state(path: Path, canon: Canon) -> tuple[GameState, list[Event]]:
    events = SessionLog(path).read_all()
    return project(events, canon), events