import pytest
import sys

sys.path.insert(0, ".")
from anvil_and_ink.loaders import load_canon
from anvil_and_ink.projection import build_initial, apply, ProjectionLookup, replay, IntegrityError
from anvil_and_ink.models import Event, Outcome, Primitive

CANON_PATH = "canon.yaml"

@pytest.fixture
def canon():
    return load_canon(CANON_PATH)

@pytest.fixture
def initial_state(canon):
    return build_initial(canon)

def test_pj_1(initial_state):
    assert initial_state.location == "loc_shelter"
    assert "bunker_door_open" in initial_state.flags
    assert initial_state.flags["bunker_door_open"] == False

def test_pj_2(canon, initial_state):
    lookup = ProjectionLookup(initial_state, canon)
    assert lookup.items_here("loc_bunker") == {}

def test_pj_3(initial_state):
    prim = Primitive(type="set_flag", flag="bunker_door_open", value=True)
    new_state = apply(initial_state, [prim])
    assert new_state.flags["bunker_door_open"] == True
    assert new_state.location == initial_state.location

def test_pj_4(initial_state):
    prim = Primitive(type="add_to_inventory", item="item_code", count=2)
    new_state = apply(initial_state, [prim])
    assert new_state.inventory["item_code"] == 2

def test_pj_5(initial_state):
    state_with_item = apply(initial_state, [Primitive(type="add_to_inventory", item="item_code", count=1)])
    new_state = apply(state_with_item, [Primitive(type="remove_from_inventory", item="item_code", count=1)])
    assert "item_code" not in new_state.inventory

def test_pj_6(initial_state):
    state_with_item = apply(initial_state, [Primitive(type="add_to_inventory", item="item_code", count=1)])
    with pytest.raises(IntegrityError):
        apply(state_with_item, [Primitive(type="remove_from_inventory", item="item_code", count=2)])

def test_pj_7(initial_state):
    state_with_item = apply(initial_state, [Primitive(type="add_to_inventory", item="item_code", count=1)])
    prims = [
        Primitive(type="set_flag", flag="bunker_door_open", value=True),
        Primitive(type="add_to_inventory", item="item_code", count=1),
        Primitive(type="remove_from_inventory", item="item_code", count=5)
    ]
    with pytest.raises(IntegrityError):
        apply(state_with_item, prims)
    assert state_with_item.flags["bunker_door_open"] == False
    assert state_with_item.inventory.get("item_code", 0) == 1

def test_pj_8(initial_state):
    state_with_item = apply(initial_state, [Primitive(type="add_to_inventory", item="item_code", count=1)])
    lookup = ProjectionLookup(state_with_item, None)
    assert lookup.has_item("item_code", min_count=2) == False
    assert lookup.has_item("item_code", min_count=1) == True

def test_pj_9(canon, initial_state):
    lookup = ProjectionLookup(initial_state, canon)
    assert "exit_north_market" in lookup.use_targets_here("loc_market")

def test_pj_10(canon, initial_state):
    lookup = ProjectionLookup(initial_state, canon)
    assert lookup.use_targets_here("loc_shelter") == []

def test_pj_11(canon, initial_state):
    lookup = ProjectionLookup(initial_state, canon)
    assert lookup.npc_location("npc_unknown") is None

def test_pj_12(canon, initial_state):
    replayed = replay(canon, [])
    assert replayed.turn == 0
    assert replayed.location == initial_state.location

def test_pj_13(canon, initial_state):
    ev1 = Event(kind="turn", turn=1, outcome=Outcome(result="ok", reason_code="ok", applied=(Primitive(type="set_flag", flag="bunker_door_open", value=True),)))
    replayed = replay(canon, [ev1])
    assert replayed.flags["bunker_door_open"] == True

def test_pj_14(canon, initial_state):
    ev1 = Event(kind="turn", turn=1, outcome=Outcome(result="ok", reason_code="ok", applied=(Primitive(type="set_flag", flag="non_existent_flag", value=True),)))
    with pytest.raises(IntegrityError):
        replay(canon, [ev1])

def test_pj_15(canon, initial_state):
    ev1 = Event(kind="turn", turn=2, outcome=Outcome(result="ok", reason_code="ok", applied=(Primitive(type="set_flag", flag="bunker_door_open", value=True),)))
    ev2 = Event(kind="turn", turn=1, outcome=Outcome(result="ok", reason_code="ok", applied=(Primitive(type="set_flag", flag="game_won", value=True),)))
    with pytest.raises(IntegrityError):
        replay(canon, [ev1, ev2])