import pytest

from anvil_and_ink.loaders import load_canon, load_templates
from anvil_and_ink.models import Outcome
from anvil_and_ink.templates_renderer import RenderSnapshot, render


CANON_PATH = "canon.yaml"
TEMPLATES_PATH = "templates.yaml"


@pytest.fixture(scope="module")
def canon():
    return load_canon(CANON_PATH)


@pytest.fixture(scope="module")
def templates(canon):
    return load_templates(
        TEMPLATES_PATH,
        set(canon.reason_codes.keys()),
        set(canon.verbs.keys()),
    )


def _name_maps(canon):
    return dict(
        item_names={iid: i.name for iid, i in canon.items.items()},
        npc_names={nid: n.name for nid, n in canon.npcs.items()},
        use_target_names={uid: u.name for uid, u in canon.use_targets.items()},
        direction_names={did: d.name for did, d in canon.directions.items()},
    )


def make_snapshot(
    canon,
    *,
    item_id=None,
    npc_id=None,
    target_id=None,
    direction_id=None,
    topic=None,
):
    return RenderSnapshot(
        item_id=item_id,
        npc_id=npc_id,
        target_id=target_id,
        direction_id=direction_id,
        topic=topic,
        **_name_maps(canon),
    )


def make_look_snapshot(
    canon,
    location_id,
    *,
    npcs_here=(),
    items_here=(),
    use_targets_here=(),
):
    return RenderSnapshot(
        location_description=canon.locations[location_id].description,
        npcs_here=npcs_here,
        items_here=items_here,
        use_targets_here=use_targets_here,
        **_name_maps(canon),
    )


def ok_outcome(reason="ok", response_text=None):
    return Outcome(result="ok", reason_code=reason, applied=(), response_text=response_text)


def fail_outcome(reason, response_text=None):
    return Outcome(result="fail", reason_code=reason, applied=(), response_text=response_text)


# ---------------------------------------------------------------------------
# TR-1..TR-3: specific + слоты + выбор варианта по turn
# ---------------------------------------------------------------------------


def test_tr_1_go_ok_direction_north_turn0(canon, templates):
    snap = make_snapshot(canon, direction_id="north")
    text = render("GO", "ok", ok_outcome(), snap, 0, templates)
    assert text == "Вы идёте на север."


def test_tr_2_go_ok_turn1_other_variant(canon, templates):
    snap = make_snapshot(canon, direction_id="north")
    text = render("GO", "ok", ok_outcome(), snap, 1, templates)
    assert text == "Вы направляетесь на север."


def test_tr_3_take_ok_item_name_from_canon(canon, templates):
    snap = make_snapshot(canon, item_id="item_flask")
    text = render("TAKE", "ok", ok_outcome(), snap, 0, templates)
    assert text == "Вы берёте Фляга воды."


# ---------------------------------------------------------------------------
# TR-4: слот не заполним → переход к generic
# ---------------------------------------------------------------------------


def test_tr_4_take_ok_item_null_falls_to_generic(canon, templates):
    snap = make_snapshot(canon, item_id=None)
    text = render("TAKE", "ok", ok_outcome(), snap, 0, templates)
    assert text in templates.generic["ok"]


# ---------------------------------------------------------------------------
# TR-5..TR-6: canon-текст выше шаблона
# ---------------------------------------------------------------------------


def test_tr_5_talk_ok_response_text_wins(canon, templates):
    snap = make_snapshot(canon, npc_id="npc_trader", topic="код")
    outcome = ok_outcome(response_text="Держи.")
    text = render("TALK", "ok", outcome, snap, 0, templates)
    assert text == "Держи."


def test_tr_6_talk_condition_not_met_response_on_fail(canon, templates):
    snap = make_snapshot(canon, npc_id="npc_trader", topic="код")
    outcome = fail_outcome("condition_not_met", response_text="Принеси.")
    text = render("TALK", "condition_not_met", outcome, snap, 0, templates)
    assert text == "Принеси."


# ---------------------------------------------------------------------------
# TR-7..TR-8: LOOK и сборка {scene}
# ---------------------------------------------------------------------------


def test_tr_7_look_ok_empty_location(canon, templates):
    snap = make_look_snapshot(canon, "loc_bunker")
    text = render("LOOK", "ok", ok_outcome(), snap, 0, templates)
    assert text.endswith("Здесь пусто.")


def test_tr_8_look_ok_lists_all_entities(canon, templates):
    snap = make_look_snapshot(
        canon,
        "loc_market",
        npcs_here=("Раненый у входа", "Торговец"),
        items_here=(("Фильтр", 2), ("Сломанный фонарь", 1)),
        use_targets_here=("Дверь в бункер",),
    )
    text = render("LOOK", "ok", ok_outcome(), snap, 0, templates)
    assert "Раненый у входа" in text
    assert "Торговец" in text
    assert "Фильтр" in text
    assert "Сломанный фонарь" in text
    assert "Дверь в бункер" in text
    assert "Фильтр ×2" in text  # количество для count > 1


# ---------------------------------------------------------------------------
# TR-9: UNKNOWN идёт напрямую в generic[unknown]
# ---------------------------------------------------------------------------


def test_tr_9_unknown_uses_generic_unknown(canon, templates):
    snap = make_snapshot(canon)
    outcome = fail_outcome("unknown")
    text = render("UNKNOWN", "unknown", outcome, snap, 0, templates)
    assert text in templates.generic["unknown"]


# ---------------------------------------------------------------------------
# TR-10: USE invalid_combination со слотом {item}
# ---------------------------------------------------------------------------


def test_tr_10_use_invalid_combination_item_name(canon, templates):
    snap = make_snapshot(canon, item_id="item_lamp", target_id="exit_north_market")
    outcome = fail_outcome("invalid_combination")
    text = render("USE", "invalid_combination", outcome, snap, 0, templates)
    assert text == "Сломанный фонарь здесь не поможет."


# ---------------------------------------------------------------------------
# TR-11: direction=null → specific пропущен → generic
# ---------------------------------------------------------------------------


def test_tr_11_go_no_exit_direction_null_falls_to_generic(canon, templates):
    snap = make_snapshot(canon, direction_id=None)
    outcome = fail_outcome("no_exit")
    text = render("GO", "no_exit", outcome, snap, 0, templates)
    assert text in templates.generic["no_exit"]


# ---------------------------------------------------------------------------
# TR-12..TR-13: детерминизм
# ---------------------------------------------------------------------------


def test_tr_12_turn0_and_turn1_differ(canon, templates):
    snap = make_snapshot(canon, direction_id="north")
    outcome = ok_outcome()
    t0 = render("GO", "ok", outcome, snap, 0, templates)
    t1 = render("GO", "ok", outcome, snap, 1, templates)
    assert t0 != t1


def test_tr_13_replay_same_turn_same_text(canon, templates):
    snap = make_snapshot(canon, direction_id="north")
    outcome = ok_outcome()
    a = render("GO", "ok", outcome, snap, 5, templates)
    b = render("GO", "ok", outcome, snap, 5, templates)
    assert a == b


# ---------------------------------------------------------------------------
# TR-14: в результате нет незаполненных {}
# ---------------------------------------------------------------------------


def test_tr_14_no_unfilled_slots(canon, templates):
    cases = [
        ("GO", "ok", make_snapshot(canon, direction_id="north"), ok_outcome(), 0),
        ("GO", "ok", make_snapshot(canon, direction_id="north"), ok_outcome(), 3),
        ("TAKE", "ok", make_snapshot(canon, item_id="item_flask"), ok_outcome(), 0),
        ("TAKE", "ok", make_snapshot(canon, item_id=None), ok_outcome(), 0),
        ("GO", "no_exit", make_snapshot(canon, direction_id=None), fail_outcome("no_exit"), 0),
        (
            "USE",
            "invalid_combination",
            make_snapshot(canon, item_id="item_lamp", target_id="exit_north_market"),
            fail_outcome("invalid_combination"),
            0,
        ),
        (
            "LOOK",
            "ok",
            make_look_snapshot(canon, "loc_market", npcs_here=("Торговец",), items_here=(("Фильтр", 1),)),
            ok_outcome(),
            0,
        ),
        ("UNKNOWN", "unknown", make_snapshot(canon), fail_outcome("unknown"), 0),
    ]
    for verb, reason, snap, outcome, turn in cases:
        text = render(verb, reason, outcome, snap, turn, templates)
        assert "{" not in text, f"unfilled slot in {verb}/{reason}: {text}"
        assert "}" not in text, f"unfilled slot in {verb}/{reason}: {text}"