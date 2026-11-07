from types import MappingProxyType

import pytest

from anvil_and_ink.models import (
    Canon, Command, Event, GameState, Outcome, RenderedText,
)
from anvil_and_ink.prompt_builder import build_manifest, build_prompt


# ---------- Фикстуры ----------

@pytest.fixture
def canon() -> Canon:
    return Canon(
        meta={
            "schema_version": 1,
            "start_location": "loc_cell",
            "tone": "мрачное фэнтези",
        },
        initial_state={"flags": {"door_unlocked": False, "escaped": False}},
        flags={
            "door_unlocked": {"type": "bool", "description": "дверь открыта"},
            "escaped": {"type": "bool", "description": "побег"},
        },
        locations={
            "loc_cell": {
                "id": "loc_cell", "name": "Камера",
                "scene": "Тесная камера с земляным полом.",
            },
            "loc_corridor": {
                "id": "loc_corridor", "name": "Коридор",
                "scene": "Тёмный коридор.",
            },
        },
        npcs={},
        items={
            "item_key": {"id": "item_key", "name": "Ключ"},
            "item_sword": {"id": "item_sword", "name": "Меч"},
        },
        use_targets={},
        verbs={},
        directions={"north": {"id": "north", "name": "Север"}},
        reason_codes={"ok", "unknown", "denied", "not_applicable"},
        win_condition=[],
    )


def _state(
    location="loc_cell",
    inventory=(),
    flags=None,
    won=False,
    game_over=False,
    turn=0,
) -> GameState:
    return GameState(
        turn=turn,
        location=location,
        inventory=tuple(sorted(inventory)),
        flags=MappingProxyType(flags or {"door_unlocked": False, "escaped": False}),
        won=won,
        game_over=game_over,
    )


def _cmd(raw="go north", verb="GO", **args) -> Command:
    return Command(
        raw=raw, verb=verb, args=MappingProxyType(dict(args)), parse_errors=(),
    )


def _out(code="ok", applied=True) -> Outcome:
    return Outcome(applied=applied, reason_code=code, effects=())


def _rendered(text="Вы идёте на север.") -> RenderedText:
    return RenderedText(text=text, source="specific", template=text)


# ---------- Manifest ----------

def test_PB1_manifest_noop(canon):
    s = _state()
    m = build_manifest(
        outcome=_out(), command=_cmd(verb="LOOK"),
        before=s, after=s, rendered=_rendered(), turn=1,
    )
    assert m.location_before == m.location_after == "loc_cell"
    assert m.inventory_added == () and m.inventory_removed == ()
    assert dict(m.flags_changed) == {}
    assert m.won is False and m.game_over is False
    assert m.applied is True and m.reason_code == "ok"
    assert m.rendered_source == "specific"
    assert m.turn == 1


def test_PB2_manifest_move(canon):
    before = _state(location="loc_cell")
    after = _state(location="loc_corridor")
    m = build_manifest(
        outcome=_out(), command=_cmd(),
        before=before, after=after, rendered=_rendered(), turn=1,
    )
    assert m.location_before == "loc_cell"
    assert m.location_after == "loc_corridor"


def test_PB3_manifest_inventory(canon):
    before = _state(inventory=())
    after = _state(inventory=("item_key",))
    m = build_manifest(
        outcome=_out(), command=_cmd(verb="TAKE", item="item_key"),
        before=before, after=after, rendered=_rendered(), turn=1,
    )
    assert m.inventory_added == ("item_key",)
    assert m.inventory_removed == ()


def test_PB4_manifest_inventory_removed(canon):
    before = _state(inventory=("item_key", "item_sword"))
    after = _state(inventory=("item_key",))
    m = build_manifest(
        outcome=_out(), command=_cmd(),
        before=before, after=after, rendered=_rendered(), turn=1,
    )
    assert m.inventory_removed == ("item_sword",)
    assert m.inventory_added == ()


def test_PB5_manifest_flag_change(canon):
    before = _state(flags={"door_unlocked": False, "escaped": False})
    after = _state(flags={"door_unlocked": True, "escaped": False})
    m = build_manifest(
        outcome=_out(), command=_cmd(),
        before=before, after=after, rendered=_rendered(), turn=1,
    )
    assert dict(m.flags_changed) == {"door_unlocked": (False, True)}
    # MappingProxyType — иммутабельность
    with pytest.raises(TypeError):
        m.flags_changed["x"] = (None, None)  # type: ignore[index]


def test_PB6_manifest_win_only_on_transition(canon):
    # не было победы → стала
    m1 = build_manifest(
        outcome=_out(), command=_cmd(),
        before=_state(won=False), after=_state(won=True),
        rendered=_rendered(), turn=1,
    )
    assert m1.won is True

    # уже была победа → не transition
    m2 = build_manifest(
        outcome=_out(), command=_cmd(),
        before=_state(won=True), after=_state(won=True),
        rendered=_rendered(), turn=2,
    )
    assert m2.won is False


def test_PB7_manifest_game_over(canon):
    m = build_manifest(
        outcome=_out(), command=_cmd(),
        before=_state(game_over=False), after=_state(game_over=True),
        rendered=_rendered(), turn=1,
    )
    assert m.game_over is True


def test_PB8_manifest_applied_false(canon):
    m = build_manifest(
        outcome=_out("denied", applied=False), command=_cmd(),
        before=_state(), after=_state(), rendered=_rendered(), turn=1,
    )
    assert m.applied is False
    assert m.reason_code == "denied"


# ---------- Prompt: системная часть ----------

def test_PB9_system_contains_tone(canon):
    p = build_prompt(
        canon=canon,
        state_before=_state(), state_after=_state(),
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=1,
    )
    assert "мрачное фэнтези" in p.system
    assert "рассказчик" in p.system


def test_PB10_system_no_tone(canon):
    canon_no_tone = Canon(**{**canon.__dict__, "meta": {"schema_version": 1, "start_location": "loc_cell"}})
    p = build_prompt(
        canon=canon_no_tone,
        state_before=_state(), state_after=_state(),
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=1,
    )
    assert "Тон:" not in p.system


# ---------- Prompt: пользовательская часть ----------

def test_PB11_user_contains_scene(canon):
    p = build_prompt(
        canon=canon,
        state_before=_state(), state_after=_state(),
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=1,
    )
    assert "Тесная камера с земляным полом." in p.user
    assert "Локация: Камера" in p.user


def test_PB12_user_contains_inventory(canon):
    p = build_prompt(
        canon=canon,
        state_before=_state(), state_after=_state(inventory=("item_key",)),
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=1,
    )
    assert "Инвентарь: Ключ" in p.user


def test_PB13_user_contains_draft(canon):
    draft = "Вы берёте Ключ."
    p = build_prompt(
        canon=canon,
        state_before=_state(), state_after=_state(),
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(text=draft), turn=1,
    )
    assert draft in p.user
    assert "[Черновик ответа]" in p.user


def test_PB14_user_contains_manifest(canon):
    before = _state(flags={"door_unlocked": False, "escaped": False})
    after = _state(flags={"door_unlocked": True, "escaped": False})
    p = build_prompt(
        canon=canon,
        state_before=before, state_after=after,
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=1,
    )
    assert "door_unlocked: False → True" in p.user
    assert "[Манифест изменения состояния]" in p.user


def test_PB15_user_contains_command_raw(canon):
    cmd = _cmd(raw="использовать ключ на двери", verb="USE",
               item="item_key", target="x")
    p = build_prompt(
        canon=canon,
        state_before=_state(), state_after=_state(),
        events=[], command=cmd, outcome=_out(),
        rendered=_rendered(), turn=1,
    )
    assert "> использовать ключ на двери" in p.user


# ---------- История ----------

def _turn_event(turn: int, raw: str) -> Event:
    return Event(
        event_id=turn * 2, turn=turn, ts="2024-01-01T00:00:00+00:00",
        type="turn",
        payload={
            "command": {"raw": raw, "verb": "GO", "args": {}, "parse_errors": []},
            "outcome": {"applied": True, "reason_code": "ok", "effects": []},
        },
    )


def _presentation_event(turn: int, final_text: str) -> Event:
    return Event(
        event_id=turn * 2 + 1, turn=turn, ts="2024-01-01T00:00:01+00:00",
        type="presentation",
        payload={"final_text": final_text, "source": "llm"},
    )


def test_PB16_history_empty(canon):
    p = build_prompt(
        canon=canon,
        state_before=_state(turn=1), state_after=_state(turn=2),
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=2,
    )
    assert "[Предыдущие ходы]" not in p.user
    assert p.history_turns_used == 0


def test_PB17_history_collected(canon):
    events = [
        _turn_event(1, "go north"),
        _presentation_event(1, "Вы идёте на север."),
    ]
    p = build_prompt(
        canon=canon,
        state_before=_state(turn=1), state_after=_state(turn=2),
        events=events, command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=2,
    )
    assert "[Предыдущие ходы]" in p.user
    assert "> go north" in p.user
    assert "Вы идёте на север." in p.user
    assert p.history_turns_used == 1


def test_PB18_history_skips_incomplete(canon):
    # turn 1 завершён, turn 2 — без presentation (упавший LLM)
    events = [
        _turn_event(1, "go north"),
        _presentation_event(1, "Вы идёте на север."),
        _turn_event(2, "go south"),
    ]
    p = build_prompt(
        canon=canon,
        state_before=_state(turn=2), state_after=_state(turn=3),
        events=events, command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=3,
    )
    assert "go north" in p.user
    assert "go south" not in p.user
    assert p.history_turns_used == 1


def test_PB19_history_limit(canon):
    events = []
    for t in range(1, 8):
        events.append(_turn_event(t, f"go {t}"))
        events.append(_presentation_event(t, f"ответ {t}"))
    p = build_prompt(
        canon=canon,
        state_before=_state(turn=7), state_after=_state(turn=8),
        events=events, command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=8, history_n=3,
    )
    assert p.history_turns_used == 3
    assert "go 5" in p.user
    assert "go 6" in p.user
    assert "go 7" in p.user
    assert "go 4" not in p.user


def test_PB20_history_chronological(canon):
    events = [
        _turn_event(1, "first"),
        _presentation_event(1, "resp 1"),
        _turn_event(2, "second"),
        _presentation_event(2, "resp 2"),
    ]
    p = build_prompt(
        canon=canon,
        state_before=_state(turn=2), state_after=_state(turn=3),
        events=events, command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=3,
    )
    assert p.user.index("first") < p.user.index("second")


# ---------- Инварианты ----------

def test_PB21_determinism(canon):
    args = dict(
        canon=canon,
        state_before=_state(), state_after=_state(),
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=1,
    )
    assert build_prompt(**args) == build_prompt(**args)


def test_PB22_manifest_is_immutable(canon):
    m = build_manifest(
        outcome=_out(), command=_cmd(),
        before=_state(), after=_state(inventory=("item_key",)),
        rendered=_rendered(), turn=1,
    )
    # поля tuple / MappingProxyType
    assert isinstance(m.inventory_added, tuple)
    assert isinstance(m.flags_changed, MappingProxyType)


def test_PB23_prompt_does_not_mutate_inputs(canon):
    before = _state(flags={"door_unlocked": False, "escaped": False})
    after = _state(flags={"door_unlocked": True, "escaped": False})
    before_snapshot = before.to_dict()
    after_snapshot = after.to_dict()
    build_prompt(
        canon=canon,
        state_before=before, state_after=after,
        events=[], command=_cmd(), outcome=_out(),
        rendered=_rendered(), turn=1,
    )
    assert before.to_dict() == before_snapshot
    assert after.to_dict() == after_snapshot


def test_PB24_manifest_to_dict_roundtrip(canon):
    m = build_manifest(
        outcome=_out(), command=_cmd(verb="TAKE", item="item_key"),
        before=_state(),
        after=_state(inventory=("item_key",),
                     flags={"door_unlocked": True, "escaped": False}),
        rendered=_rendered(), turn=1,
    )
    d = m.to_dict()
    assert d["inventory_added"] == ["item_key"]
    assert d["flags_changed"] == {"door_unlocked": [False, True]}
    assert d["turn"] == 1
    assert d["rendered_source"] == "specific"


def test_PB25_not_applicable_in_manifest(canon):
    m = build_manifest(
        outcome=_out("not_applicable", applied=False), command=_cmd(),
        before=_state(), after=_state(), rendered=_rendered(), turn=1,
    )
    assert m.applied is False
    assert m.reason_code == "not_applicable"
    p = build_prompt(
        canon=canon,
        state_before=_state(), state_after=_state(),
        events=[], command=_cmd(),
        outcome=_out("not_applicable", applied=False),
        rendered=_rendered(), turn=1,
    )
    assert "команда не выполнена" in p.user
    assert "not_applicable" in p.user