import dataclasses
from types import MappingProxyType

import pytest

from anvil_and_ink.models import (
    Canon, Command, GameState, Outcome, Templates,
)
from anvil_and_ink.templates_renderer import render, RenderError


@pytest.fixture
def canon() -> Canon:
    return Canon(
        meta={"start_location": "loc_cell"},
        initial_state={"flags": {}},
        flags={},
        locations={
            "loc_cell": {
                "id": "loc_cell", "name": "Камера",
                "scene": "Тесная камера с земляным полом.",
            },
            "loc_hall": {
                "id": "loc_hall", "name": "Зал",
                "scene": "Пустой зал.",
            },
        },
        npcs={
            "npc_guard": {"id": "npc_guard", "name": "Стражник",
                          "aliases": ["стражник"]},
        },
        items={
            "item_sword": {"id": "item_sword", "name": "Меч",
                           "aliases": ["меч"]},
            "item_key": {"id": "item_key", "name": "Ключ",
                         "aliases": ["ключ"]},
        },
        use_targets={
            "ut_door": {"id": "ut_door", "name": "Дверь",
                        "aliases": ["дверь"]},
        },
        verbs={},
        directions={
            "north": {"id": "north", "name": "Север", "aliases": ["север"]},
        },
        reason_codes={"ok", "unknown", "denied", "not_applicable"},
        win_condition=[],
    )


@pytest.fixture
def templates() -> Templates:
    return Templates(
        canon_guarantee={("GO", "unknown"), ("SAY", "denied")},
        generic={
            "ok": ["Вы делаете это.", "Готово."],
            "unknown": ["Вы не понимаете, о чём речь."],
            "denied": ["Так нельзя."],
            "not_applicable": ["Ничего не происходит."],
        },
        specific={
            "TAKE": {
                "ok": ["Вы берёте {item}."],
                "denied": ["Уже у вас."],
                "unknown": ["Здесь нет {item}."],
            },
            "GO": {
                "ok": ["Вы идёте на {direction}."],
                "denied": ["Путь на {direction} закрыт."],
            },
            "LOOK": {
                "ok": [
                    "Вы осматриваетесь. {scene}",
                    "Вокруг всё та же картина. {scene}",
                    "Вы бросаете взгляд вокруг. {scene}",
                ],
            },
            "SAY": {
                "ok": ["Вы говорите: {topic}."],
            },
        },
    )


@pytest.fixture
def state() -> GameState:
    return GameState(
        turn=0, location="loc_cell", inventory=(),
        flags=MappingProxyType({}), won=False, game_over=False,
    )


def _cmd(verb, **args) -> Command:
    return Command(raw="", verb=verb,
                   args=MappingProxyType(dict(args)), parse_errors=())


def _out(code, applied=True) -> Outcome:
    return Outcome(applied=applied, reason_code=code, effects=())


# ---------- TR-1 … TR-4: базовый выбор шаблона ----------

def test_TR1_specific_take_ok(canon, templates, state):
    cmd = _cmd("TAKE", item="item_sword")
    out = _out("ok")
    r = render(out, cmd, state, canon, templates, turn=1)
    assert r.text == "Вы берёте Меч."
    assert r.source == "specific"
    assert "{item}" not in r.text


def test_TR2_generic_fallback_when_specific_missing(canon, templates, state):
    cmd = _cmd("TALK", npc="npc_guard")
    out = _out("ok")
    r = render(out, cmd, state, canon, templates, turn=1)
    assert r.text == "Вы делаете это."
    assert r.source == "generic"


def test_TR3_direction_substitution(canon, templates, state):
    cmd = _cmd("GO", direction="north")
    out = _out("ok")
    r = render(out, cmd, state, canon, templates, turn=1)
    assert r.text == "Вы идёте на Север."


def test_TR4_topic_verbatim(canon, templates, state):
    cmd = _cmd("SAY", topic="привет, мир")
    out = _out("ok")
    r = render(out, cmd, state, canon, templates, turn=1)
    assert r.text == "Вы говорите: привет, мир."


# ---------- TR-5 … TR-7: {scene} ----------

def test_TR5_scene_from_location(canon, templates, state):
    cmd = _cmd("LOOK")
    out = _out("ok")
    r = render(out, cmd, state, canon, templates, turn=1)
    assert "Тесная камера" in r.text
    assert "{scene}" not in r.text


def test_TR6_scene_changes_with_location(canon, templates, state):
    new_state = GameState(
        turn=0, location="loc_hall", inventory=(),
        flags=MappingProxyType({}), won=False, game_over=False,
    )
    cmd = _cmd("LOOK")
    out = _out("ok")
    r = render(out, cmd, new_state, canon, templates, turn=1)
    assert "Пустой зал" in r.text


def test_TR7_scene_missing_in_location(canon, templates, state):
    loc = dict(canon.locations["loc_cell"])
    del loc["scene"]
    canon2 = dataclasses.replace(
        canon,
        locations={"loc_cell": loc, "loc_hall": canon.locations["loc_hall"]},
    )
    cmd = _cmd("LOOK")
    out = _out("ok")
    r = render(out, cmd, state, canon2, templates, turn=1)
    assert r.source == "generic"
    assert any("missing slots" in w for w in r.warnings)


# ---------- TR-8 … TR-10: выбор варианта ----------

def test_TR8_variant_selection_by_turn(canon, templates, state):
    cmd = _cmd("LOOK")
    out = _out("ok")
    r1 = render(out, cmd, state, canon, templates, turn=1)
    r2 = render(out, cmd, state, canon, templates, turn=2)
    r3 = render(out, cmd, state, canon, templates, turn=3)
    r4 = render(out, cmd, state, canon, templates, turn=4)
    assert r1.template != r2.template
    assert r2.template != r3.template
    assert r1.template == r4.template


def test_TR9_same_turn_same_output(canon, templates, state):
    cmd = _cmd("LOOK")
    out = _out("ok")
    a = render(out, cmd, state, canon, templates, turn=5)
    b = render(out, cmd, state, canon, templates, turn=5)
    assert a == b


def test_TR10_determinism_across_instances(canon, templates, state):
    cmd = _cmd("LOOK")
    out = _out("ok")
    results = [render(out, cmd, state, canon, templates, turn=t).text
               for t in range(1, 10)]
    expected = [
        "Вы осматриваетесь. Тесная камера с земляным полом.",
        "Вокруг всё та же картина. Тесная камера с земляным полом.",
        "Вы бросаете взгляд вокруг. Тесная камера с земляным полом.",
        "Вы осматриваетесь. Тесная камера с земляным полом.",
        "Вокруг всё та же картина. Тесная камера с земляным полом.",
        "Вы бросаете взгляд вокруг. Тесная камера с земляным полом.",
        "Вы осматриваетесь. Тесная камера с земляным полом.",
        "Вокруг всё та же картина. Тесная камера с земляным полом.",
        "Вы бросаете взгляд вокруг. Тесная камера с земляным полом.",
    ]
    assert results == expected


# ---------- TR-11 … TR-12: деградация ----------

def test_TR11_missing_slot_in_specific_falls_back(canon, templates, state):
    cmd = _cmd("TAKE")
    out = _out("ok")
    r = render(out, cmd, state, canon, templates, turn=1)
    assert r.source == "generic"
    assert "missing slots" in " ".join(r.warnings)


def test_TR12_missing_slot_in_generic_kept_verbatim(canon, state):
    templates = Templates(
        canon_guarantee=set(),
        generic={"ok": ["Вы делаете {thing}."]},
        specific={},
    )
    cmd = _cmd("LOOK")
    out = _out("ok")
    r = render(out, cmd, state, canon, templates, turn=1)
    assert r.source == "generic"
    assert "{thing}" in r.text
    assert any("missing slots" in w for w in r.warnings)


# ---------- TR-13 … TR-15 ----------

def test_TR13_no_generic_raises(canon, state):
    templates = Templates(canon_guarantee=set(), generic={}, specific={})
    with pytest.raises(RenderError, match="no generic"):
        render(_out("ok"), _cmd("LOOK"), state, canon, templates, turn=1)


def test_TR14_extra_slots_supplied(canon, templates, state):
    templates = Templates(
        canon_guarantee=set(),
        generic={"ok": ["…"]},
        specific={"TALK": {"ok": ["{npc} отвечает: {response}"]}},
    )
    cmd = _cmd("TALK", npc="npc_guard")
    out = _out("ok")
    r = render(
        out, cmd, state, canon, templates, turn=1,
        extra_slots={"response": "Проходи."},
    )
    assert r.text == "Стражник отвечает: Проходи."
    assert r.source == "specific"


def test_TR15_applied_flag_ignored_by_renderer(canon, templates, state):
    cmd = _cmd("TAKE", item="item_sword")
    r_ok = render(_out("ok", applied=True), cmd, state, canon, templates, turn=1)
    r_ok2 = render(_out("ok", applied=True), cmd, state, canon, templates, turn=1)
    assert r_ok.text == r_ok2.text