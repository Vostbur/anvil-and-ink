import json
import pytest
from pathlib import Path

from anvil_and_ink.models import Event
from anvil_and_ink.projection import (
    ProjectionError, project_initial, apply_effect, apply_event, project,
)
from anvil_and_ink.session_log import SessionLog, load_state
from anvil_and_ink.loaders.canon import load_canon

DATA = Path(__file__).parent / "data"

@pytest.fixture
def canon():
    return load_canon(DATA / "canon.yaml")

def _turn(turn: int, effects, applied=True, command=None) -> Event:
    return Event(
        event_id=turn, turn=turn, ts="2024-01-01T00:00:00+00:00", type="turn",
        payload={
            "command": command or {"verb": "GO", "raw": "go north"},
            "outcome": {
                "applied": applied,
                "reason_code": "ok" if applied else "denied",
                "effects": effects,
            },
        },
    )

# ---------- PROJ-1 … PROJ-8: базовое проецирование ----------

def test_PROJ1_initial_state(canon):
    state = project_initial(canon)
    assert state.turn == 0
    assert state.location == canon.meta["start_location"]
    assert state.inventory == ()
    assert state.won is False and state.game_over is False
    # Все флаги из канона присутствуют с начальными значениями
    for flag_id, value in canon.initial_state["flags"].items():
        assert state.flag(flag_id) == value

def test_PROJ2_move_ok(canon):
    state = project_initial(canon)
    exits = canon.locations[state.location].get("exits", [])
    assert exits, "canon should have at least one exit from start"
    target = exits[0]["to"]
    new_state = apply_effect(state, {"type": "move", "from": state.location, "to": target})
    assert new_state.location == target
    assert new_state.turn == state.turn  # эффекты не бампают turn

def test_PROJ3_move_from_mismatch(canon):
    state = project_initial(canon)
    with pytest.raises(ProjectionError, match="move.from"):
        apply_effect(state, {"type": "move", "from": "wrong", "to": "anywhere"})

def test_PROJ4_set_flag(canon):
    state = project_initial(canon)
    flag = next(iter(canon.flags))
    new_state = apply_effect(state, {"type": "set_flag", "flag": flag, "value": not canon.initial_state["flags"][flag]})
    assert new_state.flags[flag] != canon.initial_state["flags"][flag]

def test_PROJ5_add_item(canon):
    state = project_initial(canon)
    item = next(iter(canon.items))
    new_state = apply_effect(state, {"type": "add_to_inventory", "item": item})
    assert item in new_state.inventory

def test_PROJ6_add_duplicate(canon):
    state = project_initial(canon)
    item = next(iter(canon.items))
    once = apply_effect(state, {"type": "add_to_inventory", "item": item})
    with pytest.raises(ProjectionError, match="already in inventory"):
        apply_effect(once, {"type": "add_to_inventory", "item": item})

def test_PROJ7_remove_missing(canon):
    state = project_initial(canon)
    with pytest.raises(ProjectionError, match="not in inventory"):
        apply_effect(state, {"type": "remove_from_inventory", "item": "no_such"})

def test_PROJ8_unknown_effect(canon):
    state = project_initial(canon)
    with pytest.raises(ProjectionError, match="unknown effect"):
        apply_effect(state, {"type": "teleport"})

# ---------- PROJ-9 … PROJ-13: события turn / presentation ----------

def test_PROJ9_turn_applied_bumps_turn(canon):
    state = project_initial(canon)
    flag = next(iter(canon.flags))
    ev = _turn(1, [{"type": "set_flag", "flag": flag, "value": True}])
    new_state = apply_event(state, ev)
    assert new_state.turn == 1
    assert new_state.flags[flag] is True

def test_PROJ10_turn_denied_bumps_turn_only(canon):
    state = project_initial(canon)
    ev = _turn(1, [], applied=False)
    new_state = apply_event(state, ev)
    assert new_state.turn == 1
    # всё остальное идентично
    assert new_state.location == state.location
    assert new_state.inventory == state.inventory
    assert dict(new_state.flags) == dict(state.flags)

def test_PROJ11_turn_out_of_order(canon):
    state = project_initial(canon)
    with pytest.raises(ProjectionError, match="turn mismatch"):
        apply_event(state, _turn(2, []))

def test_PROJ12_presentation_is_noop(canon):
    state = project_initial(canon)
    after_turn = apply_event(state, _turn(1, []))
    presentation = Event(
        event_id=2, turn=1, ts="2024-01-01T00:00:01+00:00", type="presentation",
        payload={"final_text": "Вы идёте.", "source": "llm"},
    )
    after_presentation = apply_event(after_turn, presentation)
    assert after_presentation == after_turn

def test_PROJ13_presentation_in_future_is_corrupt(canon):
    state = project_initial(canon)
    presentation = Event(
        event_id=1, turn=1, ts="...", type="presentation",
        payload={"final_text": "x", "source": "llm"},
    )
    with pytest.raises(ProjectionError, match="future"):
        apply_event(state, presentation)

# ---------- PROJ-14 … PROJ-16: атомарность, win, game_over ----------

def test_PROJ14_win_and_game_over(canon):
    state = project_initial(canon)
    won = apply_effect(state, {"type": "win"})
    assert won.won
    with pytest.raises(ProjectionError, match="win applied twice"):
        apply_effect(won, {"type": "win"})
    over = apply_effect(won, {"type": "game_over"})
    assert over.game_over

def test_PROJ15_effect_atomicity(canon):
    """Если один из эффектов хода падает, состояние не должно измениться частично."""
    state = project_initial(canon)
    flag = next(iter(canon.flags))
    bad = _turn(1, [
        {"type": "set_flag", "flag": flag, "value": True},
        {"type": "move", "from": "wrong", "to": "anywhere"},
    ])
    with pytest.raises(ProjectionError, match="move.from"):
        apply_event(state, bad)
    assert state == project_initial(canon)  # исходное состояние не тронуто

def test_PROJ16_project_sequence(canon):
    flag = next(iter(canon.flags))
    events = [
        _turn(1, [{"type": "set_flag", "flag": flag, "value": True}]),
        _turn(2, []),
        _turn(3, [{"type": "win"}]),
    ]
    final = project(events, canon)
    assert final.turn == 3
    assert final.flags[flag] is True
    assert final.won is True

# ---------- PROJ-17 … PROJ-19: детерминизм и порядок ----------

def test_PROJ17_determinism(canon):
    events = [_turn(1, []), _turn(2, [])]
    assert project(events, canon) == project(events, canon)

def test_PROJ18_empty_log(canon):
    state = project([], canon)
    assert state == project_initial(canon)

def test_PROJ19_project_does_not_mutate_events(canon):
    events = [_turn(1, [])]
    snapshot = list(events)
    project(events, canon)
    assert events == snapshot

# ---------- PROJ-20 … PROJ-22: session log / save-load ----------

def test_PROJ20_roundtrip(canon, tmp_path):
    path = tmp_path / "session.jsonl"
    log = SessionLog(path)
    log.append_turn(1, {"verb": "GO"}, {"applied": False, "reason_code": "ok", "effects": []})
    log.append_presentation(1, "Вы стоите.", source="llm")

    state, events = load_state(path, canon)
    assert state.turn == 1
    assert len(events) == 2
    assert events[0].type == "turn"
    assert events[1].type == "presentation"
    assert events[1].payload["final_text"] == "Вы стоите."

def test_PROJ21_corrupt_line(canon, tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ProjectionError, match=":1:"):
        load_state(path, canon)

def test_PROJ22_append_turn_enforces_monotonicity(canon, tmp_path):
    path = tmp_path / "session.jsonl"
    log = SessionLog(path)
    log.append_turn(1, {}, {"applied": False, "reason_code": "ok", "effects": []})
    with pytest.raises(ValueError, match="expected 2"):
        log.append_turn(3, {}, {"applied": False, "reason_code": "ok", "effects": []})