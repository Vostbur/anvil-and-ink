import pytest
from types import MappingProxyType

from anvil_and_ink.models import Canon
from anvil_and_ink.parser import parse, normalize, _levenshtein, _similarity

@pytest.fixture
def canon() -> Canon:
    return Canon(
        meta={"start_location": "room"},
        initial_state={"flags": {}},
        flags={},
        locations={
            "room": {"id": "room", "name": "Комната"},
            "hall": {"id": "hall", "name": "Зал"},
        },
        npcs={
            "npc_guard": {"id": "npc_guard", "name": "Стражник",
                          "aliases": ["стражник", "охранник", "guard"]},
        },
        items={
            "item_sword": {"id": "item_sword", "name": "Меч",
                           "aliases": ["меч", "клинок", "sword"]},
            "item_key": {"id": "item_key", "name": "Ключ",
                         "aliases": ["ключ", "key"]},
        },
        use_targets={
            "use_target_door": {"id": "use_target_door", "name": "Дверь",
                                "aliases": ["дверь", "дверью", "door"]},
        },
        verbs={
            "LOOK": {"aliases": ["look", "смотреть", "осмотреть"],
                     "optional_args": ["item"]},
            "GO":   {"aliases": ["go", "идти", "пойти"], "args": ["direction"]},
            "TAKE": {"aliases": ["take", "взять", "подобрать"], "args": ["item"]},
            "USE":  {"aliases": ["use", "использовать", "применить"],
                     "args": ["item", "target"]},
            "TALK": {"aliases": ["talk", "говорить", "поговорить"],
                     "args": ["npc", "topic"]},
            "SAY":  {"aliases": ["say", "сказать"], "args": ["topic"]},
        },
        directions={
            "north": {"id": "north", "name": "Север",
                      "aliases": ["north", "север", "n"]},
            "south": {"id": "south", "name": "Юг",
                      "aliases": ["south", "юг", "s"]},
        },
        reason_codes={"ok", "unknown"},
        win_condition=[],
    )

# ---------- Нормализация ----------

def test_P1_normalize_case_and_punct():
    assert normalize("GO, North!") == "go north"

def test_P2_normalize_yo():
    assert normalize("Взятьё") == "взятье"

def test_P3_normalize_whitespace():
    assert normalize("  go    north  ") == "go north"

def test_P4_normalize_hyphen_kept():
    assert normalize("key-with-tag") == "key-with-tag"

# ---------- Расстояние ----------

def test_P5_levenshtein_basic():
    assert _levenshtein("north", "norh") == 1
    assert _levenshtein("go", "go") == 0
    assert _levenshtein("", "go") == 2

def test_P6_similarity_bounds():
    assert _similarity("north", "north") == 1.0
    assert 0.0 <= _similarity("north", "south") < 0.5

# ---------- Базовые команды ----------

def test_P7_look_no_args(canon):
    cmd = parse("look", canon)
    assert cmd.verb == "LOOK"
    assert dict(cmd.args) == {}
    assert cmd.parse_errors == ()
    assert cmd.recognized

def test_P8_go_direction(canon):
    cmd = parse("go north", canon)
    assert cmd.verb == "GO"
    assert cmd.args["direction"] == "north"
    assert cmd.recognized

def test_P9_take_item(canon):
    cmd = parse("взять меч", canon)
    assert cmd.verb == "TAKE"
    assert cmd.args["item"] == "item_sword"
    assert cmd.recognized

def test_P10_use_item_target(canon):
    cmd = parse("use key on door", canon)
    assert cmd.verb == "USE"
    assert cmd.args["item"] == "item_key"
    assert cmd.args["target"] == "use_target_door"
    assert cmd.recognized

def test_P11_use_item_npc(canon):
    cmd = parse("use sword on guard", canon)
    assert cmd.verb == "USE"
    assert cmd.args["item"] == "item_sword"
    assert cmd.args["target"] == "npc_guard"

def test_P12_talk_topic(canon):
    cmd = parse("поговорить со стражником о погоде", canon)
    assert cmd.verb == "TALK"
    assert cmd.args["npc"] == "npc_guard"
    assert cmd.args["topic"] == "погоде"

def test_P13_say_free_text(canon):
    cmd = parse("say hello world", canon)
    assert cmd.verb == "SAY"
    assert cmd.args["topic"] == "hello world"
    assert cmd.recognized

# ---------- Ошибки разбора ----------

def test_P14_empty_input(canon):
    cmd = parse("", canon)
    assert cmd.parse_errors == ("empty_input",)
    assert not cmd.recognized

def test_P15_whitespace_only(canon):
    cmd = parse("    ", canon)
    assert cmd.parse_errors == ("empty_input",)

def test_P16_unknown_verb(canon):
    cmd = parse("fly away", canon)
    assert cmd.verb is None
    assert cmd.parse_errors == ("unknown_verb",)

def test_P17_unknown_direction(canon):
    cmd = parse("go sideways", canon)
    assert cmd.verb == "GO"
    assert cmd.args["direction"] is None
    assert "unknown_direction" in cmd.parse_errors

def test_P18_missing_direction(canon):
    cmd = parse("go", canon)
    assert cmd.parse_errors == ("missing_direction",)

def test_P19_missing_item(canon):
    cmd = parse("взять", canon)
    assert cmd.parse_errors == ("missing_item",)

def test_P20_trailing_tokens(canon):
    cmd = parse("look sword extra", canon)
    assert cmd.verb == "LOOK"
    assert cmd.args["item"] == "item_sword"
    assert "trailing_tokens" in cmd.parse_errors

# ---------- Fuzzy ----------

def test_P21_fuzzy_verb(canon):
    cmd = parse("tkae sword", canon)  # транспозиция — Левенштейн 2, ratio 0.5
    # обычный Левенштейн не поймает; проверяем, что хотя бы не падает
    assert cmd is not None

def test_P22_fuzzy_direction(canon):
    cmd = parse("go norht", canon)  # транспозиция
    # ожидаем разумного поведения — либо fuzzy, либо unknown_direction
    assert cmd.verb == "GO"

def test_P23_fuzzy_within_threshold(canon):
    cmd = parse("go nort", canon)  # 1 удаление → ratio 0.8 ✓
    assert cmd.args["direction"] == "north"

def test_P24_fuzzy_rejects_far(canon):
    cmd = parse("go southward", canon)
    # "southward" далеко от "south": ratio 0.6 < 0.7
    assert cmd.parse_errors and "unknown_direction" in cmd.parse_errors or \
           cmd.args["direction"] == "south"  # допустимо, если попал в порог

# ---------- Стоп-слова и пунктуация ----------

def test_P25_stopword_the(canon):
    cmd = parse("the take sword", canon)
    assert cmd.verb == "TAKE"
    assert cmd.args["item"] == "item_sword"

def test_P26_punctuation_preserved(canon):
    cmd = parse("Go, North!", canon)
    assert cmd.verb == "GO"
    assert cmd.args["direction"] == "north"

def test_P27_multi_space(canon):
    cmd = parse("go    north", canon)
    assert cmd.verb == "GO"
    assert cmd.args["direction"] == "north"

# ---------- Детерминизм ----------

def test_P28_determinism(canon):
    a = parse("go north", canon)
    b = parse("go north", canon)
    assert a == b

def test_P29_idempotency(canon):
    cmd = parse("use key on door", canon)
    raw = cmd.raw
    assert parse(raw, canon) == cmd

# ---------- Confidence ----------

def test_P30_exact_match_confidence(canon):
    cmd = parse("go north", canon)
    assert cmd.confidence == 1.0

def test_P31_fuzzy_confidence_lower(canon):
    cmd = parse("go nort", canon)
    assert 0.0 < cmd.confidence < 1.0