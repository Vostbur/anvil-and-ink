import pytest
import sys

sys.path.insert(0, ".")
from anvil_and_ink.loaders import load_canon
from anvil_and_ink.parser import parse

CANON_PATH = "canon.yaml"

@pytest.fixture
def canon():
    return load_canon(CANON_PATH)

def test_pa_1(canon):
    cmd = parse("взять фильтр", canon)
    assert cmd.verb == "TAKE"
    assert cmd.args.get("item") == "item_filter"
    assert cmd.resolved.get("item") == True

def test_pa_2(canon):
    cmd = parse("подобрать фильтра", canon)
    assert cmd.verb == "TAKE"
    assert cmd.args.get("item") == "item_filter"

def test_pa_3(canon):
    cmd = parse("взять ржавый фильтр", canon)
    assert cmd.verb == "TAKE"
    assert cmd.args.get("item") == "item_filter"

def test_pa_4(canon):
    cmd = parse("идти на север", canon)
    assert cmd.verb == "GO"
    assert cmd.args.get("direction") == "north"
    assert cmd.resolved.get("direction") == True

def test_pa_5(canon):
    cmd = parse("поговорить с торговцем про код", canon)
    assert cmd.verb == "TALK"
    assert cmd.args.get("npc") == "npc_trader"
    assert cmd.args.get("topic") == "код"

def test_pa_6(canon):
    cmd = parse("поговорить с торговцем", canon)
    assert cmd.verb == "TALK"
    assert cmd.args.get("npc") == "npc_trader"
    assert cmd.resolved.get("topic") == False

def test_pa_7(canon):
    cmd = parse("поговорить с кем-то про код", canon)
    assert cmd.verb == "TALK"
    assert cmd.resolved.get("npc") == False

def test_pa_8(canon):
    cmd = parse("использовать код на дверь", canon)
    assert cmd.verb == "USE"
    assert cmd.args.get("item") == "item_code"
    assert cmd.args.get("target") == "exit_north_market"

def test_pa_9(canon):
    cmd = parse("использовать дверь кодом", canon)
    assert cmd.verb == "USE"
    # Известное ограничение: без предлога первый токен — item
    assert cmd.args.get("item") == "exit_north_market"
    assert cmd.args.get("target") == "item_code"

def test_pa_10(canon):
    cmd = parse("абракадабра", canon)
    assert cmd.verb == "UNKNOWN"

def test_pa_11(canon):
    cmd = parse("взять", canon)
    assert cmd.verb == "TAKE"
    assert cmd.resolved.get("item") == False

def test_pa_12(canon):
    cmd = parse("взять ключ", canon)
    assert cmd.verb == "TAKE"
    assert cmd.resolved.get("item") == False

def test_pa_13(canon):
    cmd = parse("открыть дверь", canon)
    assert cmd.verb == "USE"
    assert cmd.args.get("target") == "exit_north_market"

def test_pa_14(canon):
    cmd = parse("осмотреться", canon)
    assert cmd.verb == "LOOK"

def test_pa_15(canon):
    cmd = parse("осмотреть дверь", canon)
    assert cmd.verb == "LOOK"

def test_pa_16(canon):
    cmd = parse("пойти восток", canon)
    assert cmd.verb == "GO"
    assert cmd.args.get("direction") == "east"

def test_pa_17(canon):
    cmd = parse("взять фильтр и идти", canon)
    assert cmd.verb == "TAKE"
    assert cmd.args.get("item") == "item_filter"

def test_pa_18(canon):
    cmd = parse("идти куда-то", canon)
    assert cmd.verb == "GO"
    assert cmd.resolved.get("direction") == False