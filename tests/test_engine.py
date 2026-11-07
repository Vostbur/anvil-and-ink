import pytest
import yaml
from dataclasses import replace
from pathlib import Path

from anvil_and_ink.engine import Engine, check_win_condition
from anvil_and_ink.loaders.canon import load_canon
from anvil_and_ink.models import Event, Outcome
from anvil_and_ink.parser import parse
from anvil_and_ink.projection import apply_effect, apply_event, project_initial


# ---------- Золотой путь (E-A-1) ----------

GOLDEN_CANON_YAML = """
meta:
  schema_version: 1
  start_location: loc_cell
initial_state:
  flags: {door_unlocked: false, escaped: false}
flags:
  door_unlocked: {type: bool, description: "Дверь открыта"}
  escaped: {type: bool, description: "Побег совершён"}
locations:
  - id: loc_cell
    name: Камера
    entities: [item_key]
    exits:
      - {to: loc_corridor, direction: north}
  - id: loc_corridor
    name: Коридор
    exits:
      - {to: loc_cell, direction: south}
      - to: loc_escape
        direction: north
        condition: [{type: flag, flag: door_unlocked, equals: true}]
        effects: [{type: set_flag, flag: escaped, value: true}]
  - id: loc_escape
    name: Свобода
    exits: []
npcs: []
items:
  - {id: item_key, name: Ключ, aliases: [ключ, key]}
use_targets:
  - id: ut_door
    name: Дверь
    aliases: [дверь, door]
    exit: {location: loc_corridor, direction: north}
    use:
      - target: item_key
        effects: [{type: set_flag, flag: door_unlocked, value: true}]
verbs:
  - {id: LOOK, aliases: [look, осмотреться]}
  - {id: GO,   aliases: [go, идти], args: [direction]}
  - {id: TAKE, aliases: [take, взять], args: [item]}
  - {id: USE,  aliases: [use, использовать], args: [item, target]}
  - {id: SAY,  aliases: [say, сказать], args: [topic]}
directions:
  - {id: north, name: Север, aliases: [north, север]}
  - {id: south, name: Юг, aliases: [south, юг]}
reason_codes: [ok, unknown, denied, not_applicable]
win_condition:
  - {type: flag, flag: escaped, equals: true}
"""


@pytest.fixture
def golden_canon(tmp_path):
    p = tmp_path / "canon.yaml"
    p.write_text(GOLDEN_CANON_YAML, encoding="utf-8")
    return load_canon(p)


@pytest.fixture
def engine(golden_canon):
    return Engine(golden_canon)


def _execute_turn(state, canon, engine, raw: str) -> tuple:
    """Полный ход без LLM: parse → answer → (win?) → event → apply."""
    cmd = parse(raw, canon)
    outcome = engine.answer(state, cmd)

    # Проверка win_condition — как это будет делать session
    if outcome.applied and not state.won:
        predicted = state
        for eff in outcome.effects:
            predicted = apply_effect(predicted, eff)
        if check_win_condition(predicted, canon):
            outcome = replace(
                outcome,
                effects=outcome.effects + ({"type": "win"},),
            )

    event = Event(
        event_id=state.turn,
        turn=state.turn + 1,
        ts="2024-01-01T00:00:00+00:00",
        type="turn",
        payload={
            "command": {"raw": cmd.raw, "verb": cmd.verb,
                        "args": dict(cmd.args),
                        "parse_errors": list(cmd.parse_errors)},
            "outcome": {"applied": outcome.applied,
                        "reason_code": outcome.reason_code,
                        "effects": list(outcome.effects)},
        },
    )
    return apply_event(state, event), outcome


def test_E_A_1_golden_path(golden_canon, engine):
    """E-A-1: take key → go north → use key on door → go north → win."""
    state = project_initial(golden_canon)

    state, out = _execute_turn(state, golden_canon, engine, "take key")
    assert out.applied and out.reason_code == "ok"
    assert state.inventory == ("item_key",)
    assert state.location == "loc_cell"

    state, out = _execute_turn(state, golden_canon, engine, "go north")
    assert out.applied and out.reason_code == "ok"
    assert state.location == "loc_corridor"

    # Дверь закрыта — попытка пройти дальше должна быть denied
    probe_state, out = _execute_turn(state, golden_canon, engine, "go north")
    assert out.applied is False and out.reason_code == "denied"
    assert probe_state.location == "loc_corridor"  # не сдвинулись

    state, out = _execute_turn(state, golden_canon, engine, "use key on door")
    assert out.applied and out.reason_code == "ok"
    assert state.flags["door_unlocked"] is True
    assert state.won is False

    state, out = _execute_turn(state, golden_canon, engine, "go north")
    assert out.applied and out.reason_code == "ok"
    assert state.location == "loc_escape"
    assert state.flags["escaped"] is True
    assert state.won is True
    assert state.game_over is False
    assert state.turn == 4


# ---------- GO ----------

def test_GO_unknown_direction(golden_canon, engine):
    state = project_initial(golden_canon)
    out = engine.answer(state, parse("go sideways", golden_canon))
    # parse не смог разрешить направление → not recognized → unknown
    assert out.reason_code == "unknown"
    assert out.applied is False


def test_GO_no_exit(golden_canon, engine):
    state = project_initial(golden_canon)
    out = engine.answer(state, parse("go south", golden_canon))
    # в камере нет южного выхода
    assert out.reason_code == "unknown"
    assert out.applied is False


def test_GO_condition_denied(golden_canon, engine):
    state = project_initial(golden_canon)
    state, _ = _execute_turn(state, golden_canon, engine, "go north")
    out = engine.answer(state, parse("go north", golden_canon))
    assert out.reason_code == "denied"
    assert out.applied is False


# ---------- TAKE ----------

def test_TAKE_twice_denied(golden_canon, engine):
    state = project_initial(golden_canon)
    state, _ = _execute_turn(state, golden_canon, engine, "take key")
    out = engine.answer(state, parse("take key", golden_canon))
    assert out.reason_code == "denied"
    assert out.applied is False


def test_TAKE_wrong_location_unknown(golden_canon, engine):
    state = project_initial(golden_canon)
    state, _ = _execute_turn(state, golden_canon, engine, "go north")
    out = engine.answer(state, parse("take key", golden_canon))
    assert out.reason_code == "unknown"
    assert out.applied is False


# ---------- USE ----------

def test_USE_not_in_inventory_unknown(golden_canon, engine):
    state = project_initial(golden_canon)
    state, _ = _execute_turn(state, golden_canon, engine, "go north")
    # Ключ ещё не взят
    out = engine.answer(state, parse("use key on door", golden_canon))
    assert out.reason_code == "unknown"
    assert out.applied is False


def test_USE_target_out_of_scope_unknown(golden_canon, engine):
    state = project_initial(golden_canon)
    state, _ = _execute_turn(state, golden_canon, engine, "take key")
    # Дверь в loc_corridor, мы в loc_cell
    out = engine.answer(state, parse("use key on door", golden_canon))
    assert out.reason_code == "unknown"
    assert out.applied is False


def test_USE_no_matching_rule_not_applicable(golden_canon, engine, tmp_path):
    # Правим канон: правило требует не тот предмет
    data = yaml.safe_load(GOLDEN_CANON_YAML)
    data["use_targets"][0]["use"][0]["target"] = "item_other"
    data["items"].append({"id": "item_other", "name": "Другое",
                          "aliases": ["другое"]})
    data["locations"][0]["entities"].append("item_other")
    p = tmp_path / "alt.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    alt_canon = load_canon(p)
    alt_engine = Engine(alt_canon)

    state = project_initial(alt_canon)
    state, _ = _execute_turn(state, alt_canon, alt_engine, "take key")
    state, _ = _execute_turn(state, alt_canon, alt_engine, "go north")
    out = alt_engine.answer(state, parse("use key on door", alt_canon))
    assert out.reason_code == "not_applicable"
    assert out.applied is False


# ---------- LOOK / SAY / TALK ----------

def test_LOOK_always_ok(golden_canon, engine):
    state = project_initial(golden_canon)
    out = engine.answer(state, parse("look", golden_canon))
    assert out.applied and out.reason_code == "ok"
    assert out.effects == ()


def test_LOOK_unknown_item(golden_canon, engine):
    state = project_initial(golden_canon)
    out = engine.answer(state, parse("look at sword", golden_canon))
    assert out.reason_code == "unknown"
    assert out.applied is False


def test_SAY_always_ok(golden_canon, engine):
    state = project_initial(golden_canon)
    out = engine.answer(state, parse("say hello", golden_canon))
    assert out.applied and out.reason_code == "ok"


# ---------- Инварианты ----------

def test_engine_requires_reason_codes(tmp_path):
    data = yaml.safe_load(GOLDEN_CANON_YAML)
    data["reason_codes"] = ["ok", "unknown"]  # убрали denied/not_applicable
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(Exception) as exc:
        load_canon(p)
    assert "denied" in str(exc.value)


def test_engine_does_not_mutate_state(golden_canon, engine):
    state = project_initial(golden_canon)
    snapshot = (state.turn, state.location, state.inventory, dict(state.flags))
    engine.answer(state, parse("take key", golden_canon))
    engine.answer(state, parse("go north", golden_canon))
    assert (state.turn, state.location, state.inventory, dict(state.flags)) == snapshot


def test_engine_determinism(golden_canon, engine):
    state = project_initial(golden_canon)
    cmd = parse("go north", golden_canon)
    assert engine.answer(state, cmd) == engine.answer(state, cmd)


def test_game_over_blocks_commands(golden_canon, engine):
    state = project_initial(golden_canon)
    state = replace(state, game_over=True)
    out = engine.answer(state, parse("look", golden_canon))
    assert out.applied is False and out.reason_code == "denied"


def test_win_condition_empty_never_wins(golden_canon):
    # Пустой win_condition = никогда не выигрываем
    canon = replace(golden_canon, win_condition=[])
    state = project_initial(canon)
    assert check_win_condition(state, canon) is False