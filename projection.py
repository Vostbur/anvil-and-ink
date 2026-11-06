from dataclasses import replace
from types import MappingProxyType
from typing import Any

from anvil_and_ink.models import Canon, Event, GameState

class ProjectionError(Exception):
    """Ошибка целостности лога событий. Состояние после неё не восстанавливается."""

def project_initial(canon: Canon) -> GameState:
    """Состояние на момент начала сессии: turn=0, стартовая локация, флаги из канона."""
    flags = dict(canon.initial_state.get("flags", {}))
    return GameState(
        turn=0,
        location=canon.meta["start_location"],
        inventory=(),
        flags=MappingProxyType(flags),
        won=False,
        game_over=False,
    )

def apply_effect(state: GameState, effect: dict[str, Any]) -> GameState:
    """Применяет один эффект. Не меняет turn — это делает apply_event."""
    etype = effect["type"]

    if etype == "move":
        if effect["from"] != state.location:
            raise ProjectionError(
                f"move.from={effect['from']!r} != current location {state.location!r}"
            )
        return replace(state, location=effect["to"])

    if etype == "set_flag":
        new_flags = dict(state.flags)
        new_flags[effect["flag"]] = effect["value"]
        return replace(state, flags=MappingProxyType(new_flags))

    if etype == "add_to_inventory":
        item = effect["item"]
        if item in state.inventory:
            raise ProjectionError(f"item {item!r} already in inventory")
        return replace(state, inventory=tuple(sorted(state.inventory + (item,))))

    if etype == "remove_from_inventory":
        item = effect["item"]
        if item not in state.inventory:
            raise ProjectionError(f"item {item!r} not in inventory")
        return replace(
            state,
            inventory=tuple(i for i in state.inventory if i != item),
        )

    if etype == "win":
        if state.won:
            raise ProjectionError("win applied twice")
        return replace(state, won=True)

    if etype == "game_over":
        if state.game_over:
            raise ProjectionError("game_over applied twice")
        return replace(state, game_over=True)

    raise ProjectionError(f"unknown effect type {etype!r}")

def apply_event(state: GameState, event: Event) -> GameState:
    """Применяет одно событие лога к состоянию."""
    if event.type == "presentation":
        # Презентация — не state-changing, но должна принадлежать уже наступившему ходу.
        if event.turn > state.turn:
            raise ProjectionError(
                f"presentation turn {event.turn} is in the future (state turn {state.turn})"
            )
        return state

    if event.type != "turn":
        raise ProjectionError(f"unknown event type {event.type!r}")

    if event.turn != state.turn + 1:
        raise ProjectionError(
            f"turn mismatch: event turn={event.turn}, expected {state.turn + 1}"
        )

    outcome = event.payload["outcome"]
    new_state = state
    if outcome["applied"]:
        for effect in outcome["effects"]:
            new_state = apply_effect(new_state, effect)
    return replace(new_state, turn=event.turn)

def project(events: list[Event], canon: Canon) -> GameState:
    """Свёртка событий в состояние. Детерминирована: те же events+canon → то же state."""
    state = project_initial(canon)
    for event in events:
        state = apply_event(state, event)
    return state