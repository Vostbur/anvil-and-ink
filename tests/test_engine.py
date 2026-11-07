import pytest
from dataclasses import replace

from anvil_and_ink.engine import apply_command
from anvil_and_ink.loaders import load_canon
from anvil_and_ink.models import Command, Primitive
from anvil_and_ink.projection import ProjectionLookup, apply as apply_primitives, build_initial


CANON_PATH = "canon.yaml"


@pytest.fixture(scope="module")
def canon():
    return load_canon(CANON_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_command(verb: str, *, resolved: dict | None = None, **args) -> Command:
    return Command(
        verb=verb,
        args=args,
        resolved=resolved or {},
        raw="",
    )


def make_state(
    canon,
    *,
    location: str | None = None,
    flags: dict | None = None,
    inventory: dict | None = None,
    location_items: dict | None = None,
):
    state = build_initial(canon)
    primitives = []

    if location is not None:
        primitives.append(Primitive(type="change_location", to=location))

    for flag_name, value in (flags or {}).items():
        primitives.append(Primitive(type="set_flag", flag=flag_name, value=value))

    for item_id, count in (inventory or {}).items():
        primitives.append(Primitive(type="add_to_inventory", item=item_id, count=count))

    for loc_id, items in (location_items or {}).items():
        for item_id, count in items.items():
            primitives.append(
                Primitive(
                    type="add_to_location",
                    to=loc_id,
                    item=item_id,
                    count=count,
                )
            )

    if primitives:
        state = apply_primitives(state, primitives)

    return state


def lookup(state, canon) -> ProjectionLookup:
    return ProjectionLookup(state, canon)


def apply_outcome(state, outcome):
    if not outcome.applied:
        return state
    return apply_primitives(state, list(outcome.applied))


def snapshot(state):
    return (
        state.turn,
        state.location,
        dict(state.inventory),
        dict(state.stats),
        dict(state.flags),
        {loc: dict(items) for loc, items in state.item_placement.items()},
        dict(state.npc_locations),
    )


def assert_outcome(
    outcome,
    *,
    result: str,
    reason: str,
    verb: str,
    allow_empty_ok: bool = False,
) -> None:
    assert outcome.result == result
    assert outcome.reason_code == reason
    assert outcome.seed is None

    if result == "fail":
        assert outcome.applied == tuple()
    elif verb == "LOOK":
        assert outcome.applied == tuple()
    elif not allow_empty_ok:
        assert outcome.applied != tuple()


def get_topic(canon, npc_id: str, topic_key: str):
    return next(t for t in canon.npcs[npc_id].topics if t.key == topic_key)


# ---------------------------------------------------------------------------
# LOOK
# ---------------------------------------------------------------------------


def test_a_look_1(canon):
    state = build_initial(canon)
    outcome = apply_command(make_command("LOOK"), lookup(state, canon), canon)

    assert_outcome(outcome, result="ok", reason="ok", verb="LOOK")
    assert outcome.response_text is None


# ---------------------------------------------------------------------------
# GO
# ---------------------------------------------------------------------------


def test_a_go_1(canon):
    state = build_initial(canon)
    command = make_command("GO", resolved={"direction": False}, direction=None)

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_exit", verb="GO")


def test_a_go_2(canon):
    state = build_initial(canon)
    command = make_command("GO", resolved={"direction": True}, direction="north")

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_exit", verb="GO")


def test_a_go_3(canon):
    state = make_state(canon, location="loc_market")
    before = snapshot(state)

    command = make_command("GO", resolved={"direction": True}, direction="north")
    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="locked", verb="GO")
    assert snapshot(state) == before
    assert state.flags["game_won"] is False


def test_a_go_4(canon):
    state = make_state(
        canon,
        location="loc_market",
        flags={"bunker_door_open": True},
    )

    command = make_command("GO", resolved={"direction": True}, direction="north")
    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="ok", reason="ok", verb="GO")

    new_state = apply_outcome(state, outcome)
    assert new_state.location == "loc_bunker"
    assert new_state.flags["game_won"] is True


def test_a_go_5(canon):
    state = build_initial(canon)

    command = make_command("GO", resolved={"direction": True}, direction="east")
    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="ok", reason="ok", verb="GO")

    new_state = apply_outcome(state, outcome)
    assert new_state.location == "loc_market"


# ---------------------------------------------------------------------------
# TAKE
# ---------------------------------------------------------------------------


def test_a_take_1(canon):
    state = build_initial(canon)
    command = make_command("TAKE", resolved={"item": False}, item=None)

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_item_here", verb="TAKE")


def test_a_take_2(canon):
    state = build_initial(canon)
    command = make_command("TAKE", resolved={"item": True}, item="item_filter")

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_item_here", verb="TAKE")


def test_a_take_3(canon):
    # В поставляемом каноне нет takeable=false, поэтому создаём
    # модифицированную копию канона для проверки ветки движка.
    new_items = dict(canon.items)
    new_items["item_flask"] = replace(
        canon.items["item_flask"],
        takeable=False,
    )
    canon_no_take = replace(canon, items=new_items)

    state = build_initial(canon_no_take)
    command = make_command("TAKE", resolved={"item": True}, item="item_flask")

    outcome = apply_command(command, lookup(state, canon_no_take), canon_no_take)

    assert_outcome(outcome, result="fail", reason="not_takeable", verb="TAKE")


def test_a_take_4(canon):
    state = build_initial(canon)
    command = make_command("TAKE", resolved={"item": True}, item="item_flask")

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="ok", reason="ok", verb="TAKE")

    new_state = apply_outcome(state, outcome)
    assert new_state.inventory.get("item_flask", 0) == 1
    assert new_state.item_placement["loc_shelter"].get("item_flask", 0) == 0


# ---------------------------------------------------------------------------
# DROP
# ---------------------------------------------------------------------------


def test_a_drop_1(canon):
    state = build_initial(canon)
    command = make_command("DROP", resolved={"item": False}, item=None)

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="not_in_inventory", verb="DROP")


def test_a_drop_2(canon):
    state = build_initial(canon)
    command = make_command("DROP", resolved={"item": True}, item="item_flask")

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="not_in_inventory", verb="DROP")


def test_a_drop_3(canon):
    state = build_initial(canon)

    take_command = make_command("TAKE", resolved={"item": True}, item="item_flask")
    take_outcome = apply_command(take_command, lookup(state, canon), canon)
    state = apply_outcome(state, take_outcome)

    drop_command = make_command("DROP", resolved={"item": True}, item="item_flask")
    drop_outcome = apply_command(drop_command, lookup(state, canon), canon)

    assert_outcome(drop_outcome, result="ok", reason="ok", verb="DROP")

    new_state = apply_outcome(state, drop_outcome)
    assert new_state.inventory.get("item_flask", 0) == 0
    assert new_state.item_placement["loc_shelter"].get("item_flask", 0) == 1


# ---------------------------------------------------------------------------
# TALK
# ---------------------------------------------------------------------------


def test_a_talk_1(canon):
    state = make_state(canon, location="loc_market")
    command = make_command(
        "TALK",
        resolved={"npc": False, "topic": False},
        npc=None,
        topic=None,
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_npc_here", verb="TALK")


def test_a_talk_2(canon):
    state = make_state(canon, location="loc_shelter")
    command = make_command(
        "TALK",
        resolved={"npc": True, "topic": True},
        npc="npc_trader",
        topic="код",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_npc_here", verb="TALK")


def test_a_talk_3(canon):
    state = make_state(canon, location="loc_market")
    command = make_command(
        "TALK",
        resolved={"npc": True, "topic": False},
        npc="npc_wounded",
        topic=None,
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_topic", verb="TALK")


def test_a_talk_4(canon):
    state = make_state(canon, location="loc_market")
    command = make_command(
        "TALK",
        resolved={"npc": True, "topic": True},
        npc="npc_wounded",
        topic="нет_такой_темы",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_topic", verb="TALK")


def test_a_talk_5(canon):
    state = make_state(canon, location="loc_market")
    topic = get_topic(canon, "npc_trader", "код")

    command = make_command(
        "TALK",
        resolved={"npc": True, "topic": True},
        npc="npc_trader",
        topic="код",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(
        outcome,
        result="fail",
        reason="condition_not_met",
        verb="TALK",
    )
    assert outcome.response_text == topic.response_on_fail


def test_a_talk_6(canon):
    state = make_state(
        canon,
        location="loc_market",
        inventory={"item_filter": 2},
    )
    topic = get_topic(canon, "npc_trader", "код")

    command = make_command(
        "TALK",
        resolved={"npc": True, "topic": True},
        npc="npc_trader",
        topic="код",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="ok", reason="ok", verb="TALK")
    assert outcome.response_text == topic.response

    new_state = apply_outcome(state, outcome)
    assert new_state.inventory.get("item_filter", 0) == 0
    assert new_state.inventory.get("item_code", 0) == 1
    assert new_state.flags["has_code"] is True


def test_a_talk_7(canon):
    fail_state = make_state(canon, location="loc_market")
    ok_state = make_state(
        canon,
        location="loc_market",
        inventory={"item_filter": 2},
    )

    command = make_command(
        "TALK",
        resolved={"npc": True, "topic": True},
        npc="npc_trader",
        topic="код",
    )

    fail_outcome = apply_command(command, lookup(fail_state, canon), canon)
    ok_outcome = apply_command(command, lookup(ok_state, canon), canon)

    assert fail_outcome.result == "fail"
    assert ok_outcome.result == "ok"
    assert fail_outcome.response_text != ok_outcome.response_text


# ---------------------------------------------------------------------------
# USE
# ---------------------------------------------------------------------------


def test_a_use_1(canon):
    state = make_state(canon, location="loc_market")
    command = make_command(
        "USE",
        resolved={"item": False, "target": True},
        item=None,
        target="exit_north_market",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="not_in_inventory", verb="USE")


def test_a_use_2(canon):
    state = make_state(canon, location="loc_market")
    command = make_command(
        "USE",
        resolved={"item": True, "target": True},
        item="item_code",
        target="exit_north_market",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="not_in_inventory", verb="USE")


def test_a_use_3(canon):
    state = make_state(
        canon,
        location="loc_market",
        inventory={"item_code": 1},
    )
    command = make_command(
        "USE",
        resolved={"item": True, "target": False},
        item="item_code",
        target=None,
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_target", verb="USE")


def test_a_use_4(canon):
    state = make_state(
        canon,
        location="loc_shelter",
        inventory={"item_code": 1},
    )
    command = make_command(
        "USE",
        resolved={"item": True, "target": True},
        item="item_code",
        target="exit_north_market",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="target_not_here", verb="USE")


def test_a_use_5(canon):
    state = make_state(
        canon,
        location="loc_shelter",
        inventory={"item_flask": 1},
    )
    command = make_command(
        "USE",
        resolved={"item": True, "target": True},
        item="item_flask",
        target="npc_wounded",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="no_npc_here", verb="USE")


def test_a_use_6(canon):
    state = make_state(
        canon,
        location="loc_market",
        inventory={"item_filter": 1},
    )
    command = make_command(
        "USE",
        resolved={"item": True, "target": True},
        item="item_filter",
        target="npc_wounded",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="invalid_combination", verb="USE")


def test_a_use_7(canon):
    state = make_state(
        canon,
        location="loc_market",
        flags={"wounded_watered": True},
        inventory={"item_flask": 1},
    )
    before = snapshot(state)

    command = make_command(
        "USE",
        resolved={"item": True, "target": True},
        item="item_flask",
        target="npc_wounded",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(
        outcome,
        result="fail",
        reason="condition_not_met",
        verb="USE",
    )
    assert outcome.response_text == "Он уже напился воды."
    assert snapshot(state) == before


def test_a_use_8(canon):
    state = make_state(
        canon,
        location="loc_market",
        inventory={"item_flask": 1},
    )

    command = make_command(
        "USE",
        resolved={"item": True, "target": True},
        item="item_flask",
        target="npc_wounded",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="ok", reason="ok", verb="USE")
    assert outcome.response_text == "Раненый жадно пьёт и протягивает вам фильтр."

    new_state = apply_outcome(state, outcome)
    assert new_state.inventory.get("item_flask", 0) == 0
    assert new_state.inventory.get("item_filter", 0) == 1
    assert new_state.flags["wounded_watered"] is True


def test_a_use_9(canon):
    state = make_state(
        canon,
        location="loc_market",
        inventory={"item_code": 1},
    )

    command = make_command(
        "USE",
        resolved={"item": True, "target": True},
        item="item_code",
        target="exit_north_market",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="ok", reason="ok", verb="USE")
    assert outcome.response_text == "Замок щёлкает, дверь приоткрывается."

    new_state = apply_outcome(state, outcome)
    assert new_state.flags["bunker_door_open"] is True
    assert new_state.inventory.get("item_code", 0) == 1


# ---------------------------------------------------------------------------
# UNKNOWN
# ---------------------------------------------------------------------------


def test_a_unk_1(canon):
    state = build_initial(canon)
    command = make_command("UNKNOWN")

    outcome = apply_command(command, lookup(state, canon), canon)

    assert_outcome(outcome, result="fail", reason="unknown", verb="UNKNOWN")


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_at_1_go_locked_projection_unchanged(canon):
    state = make_state(canon, location="loc_market")
    before = snapshot(state)

    command = make_command("GO", resolved={"direction": True}, direction="north")
    outcome = apply_command(command, lookup(state, canon), canon)

    assert outcome.result == "fail"
    assert outcome.reason_code == "locked"
    assert outcome.applied == tuple()
    assert snapshot(state) == before


def test_at_2_talk_condition_not_met_projection_unchanged(canon):
    state = make_state(
        canon,
        location="loc_market",
        inventory={"item_filter": 1},
    )
    before = snapshot(state)

    command = make_command(
        "TALK",
        resolved={"npc": True, "topic": True},
        npc="npc_trader",
        topic="код",
    )

    outcome = apply_command(command, lookup(state, canon), canon)

    assert outcome.result == "fail"
    assert outcome.reason_code == "condition_not_met"
    assert outcome.applied == tuple()
    assert snapshot(state) == before

    # Код не появился, фильтр не пропал.
    assert state.inventory.get("item_code", 0) == 0
    assert state.inventory.get("item_filter", 0) == 1


def test_at_3_use_flask_twice_second_time_unchanged(canon):
    state = make_state(
        canon,
        location="loc_market",
        inventory={"item_flask": 1},
    )

    command = make_command(
        "USE",
        resolved={"item": True, "target": True},
        item="item_flask",
        target="npc_wounded",
    )

    first_outcome = apply_command(command, lookup(state, canon), canon)
    assert first_outcome.result == "ok"

    state = apply_outcome(state, first_outcome)
    before = snapshot(state)

    second_outcome = apply_command(command, lookup(state, canon), canon)

    assert second_outcome.result == "fail"
    assert second_outcome.reason_code == "not_in_inventory"
    assert second_outcome.applied == tuple()
    assert snapshot(state) == before