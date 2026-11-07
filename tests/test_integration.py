"""
Интеграционные тесты (end-to-end).
Сквозные тесты через полный конвейер: интерфейс → сессия → парсер → движок →
проекция → логи → сборщик промпта → лестница → валидатор → рендерер.
"""

import pytest
from dataclasses import dataclass
from typing import Dict, List, Optional

from anvil_and_ink.interface import GameInterface
from anvil_and_ink.loaders import load_canon, load_templates
from anvil_and_ink.models import Event, Manifest, Outcome
from anvil_and_ink.prompt_builder import build as build_prompt
from anvil_and_ink.session import Session, SessionConfig
from anvil_and_ink.validator import Reason, Verdict, Validator


CANON_PATH = "canon.yaml"
TEMPLATES_PATH = "templates.yaml"


# ---------------------------------------------------------------------------
# Золотой путь (15 команд)
# ---------------------------------------------------------------------------


GOLDEN_PATH = [
    ("осмотреться", "ok", {}),
    ("идти на восток", "ok", {"location": "loc_market"}),
    ("осмотреться", "ok", {}),
    ("поговорить с раненым про фильтры", "ok", {}),
    ("поговорить с торговцем про код", "condition_not_met", {}),
    ("взять фонарь", "ok", {"inventory": {"item_lamp": 1}}),
    ("бросить фонарь", "ok", {"inventory": {"item_lamp": 0}}),
    ("идти на запад", "ok", {"location": "loc_shelter"}),
    ("взять флягу", "ok", {"inventory": {"item_flask": 1}}),
    ("идти на восток", "ok", {"location": "loc_market"}),
    ("использовать флягу на раненого", "ok", {
        "inventory": {"item_flask": 0, "item_filter": 1},
        "flags": {"wounded_watered": True},
    }),
    ("взять фильтр", "ok", {"inventory": {"item_filter": 2}}),
    ("поговорить с торговцем про код", "ok", {
        "inventory": {"item_filter": 0, "item_code": 1},
        "flags": {"has_code": True},
    }),
    ("использовать код на дверь", "ok", {"flags": {"bunker_door_open": True}}),
    ("идти на север", "ok", {"location": "loc_bunker", "flags": {"game_won": True}}),
]

GOLDEN_PATH_VARIANT = [
    ("идти на восток", "ok", {"location": "loc_market"}),
    ("взять фильтр", "ok", {"inventory": {"item_filter": 1}}),
    ("идти на запад", "ok", {"location": "loc_shelter"}),
    ("взять флягу", "ok", {"inventory": {"item_flask": 1}}),
    ("идти на восток", "ok", {"location": "loc_market"}),
    ("использовать флягу на раненого", "ok", {
        "inventory": {"item_flask": 0, "item_filter": 2},
        "flags": {"wounded_watered": True},
    }),
    ("поговорить с торговцем про код", "ok", {
        "inventory": {"item_filter": 0, "item_code": 1},
        "flags": {"has_code": True},
    }),
    ("использовать код на дверь", "ok", {"flags": {"bunker_door_open": True}}),
    ("идти на север", "ok", {"location": "loc_bunker", "flags": {"game_won": True}}),
]

NEGATIVE_SCENARIOS = [
    ("идти вверх", "no_exit"),
    ("идти на север", "locked"),
    ("взять фильтр", "no_item_here"),
    ("бросить флягу", "not_in_inventory"),
    ("поговорить с торговцем про флягу", "no_topic"),
    ("использовать фонарь на дверь", "invalid_combination"),
    ("поговорить с раненым про код", "no_npc_here"),
]

EXPECTED_FINAL_STATE = {
    "location": "loc_bunker",
    "game_won": True,
    "bunker_door_open": True,
    "has_code": True,
    "wounded_watered": True,
    "inventory": {"item_code": 1},
    "item_placement": {
        "loc_shelter": {},
        "loc_market": {"item_lamp": 1},
        "loc_bunker": {},
    },
    "npc_locations": {"npc_wounded": "loc_market", "npc_trader": "loc_market"},
    "turn": 15,
}


# ---------------------------------------------------------------------------
# BrokenLLMClient для режима C
# ---------------------------------------------------------------------------


@dataclass
class InjectionSpec:
    """Спецификация инжекции для одного хода."""
    attempt_1: str
    attempt_2: str
    invalid_name: Optional[str] = None
    invalid_number: Optional[int] = None


class BrokenLLMClient:
    """
    Мок-клиент с детерминированной инжекцией отказов по номеру хода.
    """

    def __init__(self, injections: Dict[int, InjectionSpec]):
        self.injections = injections
        self.current_turn = 0
        self.attempt = 0
        self.calls = []

    def set_turn(self, turn: int):
        self.current_turn = turn
        self.attempt = 0

    def __call__(self, prompt, config):
        self.attempt += 1
        self.calls.append({"turn": self.current_turn, "attempt": self.attempt, "prompt": prompt})
        spec = self.injections.get(self.current_turn)
        if spec is None:
            return self._valid_response()

        injection_type = spec.attempt_1 if self.attempt == 1 else spec.attempt_2

        if injection_type == "valid":
            return self._valid_response()
        elif injection_type == "timeout":
            return {"ok": False, "error_class": "timeout_or_network", "detail": "", "latency_ms": 10000, "tokens": None}
        elif injection_type == "config_error":
            return {"ok": False, "error_class": "config_error", "detail": "401", "latency_ms": 100, "tokens": None}
        elif injection_type == "empty":
            return {"ok": False, "error_class": "empty_response", "detail": "", "latency_ms": 100, "tokens": None}
        elif injection_type == "invalid_name":
            name = spec.invalid_name or "эльф"
            return {"ok": True, "text": f"Вы видите {name}.", "latency_ms": 100, "tokens": 10}
        elif injection_type == "invalid_number":
            num = spec.invalid_number or 99
            return {"ok": True, "text": f"У вас {num} предметов.", "latency_ms": 100, "tokens": 10}
        else:
            raise ValueError(f"Unknown injection type: {injection_type}")

    def _valid_response(self):
        return {"ok": True, "text": "Вы делаете это.", "latency_ms": 100, "tokens": 10}


MODE_C_INJECTIONS = {
    1: InjectionSpec(attempt_1="timeout", attempt_2="valid"),
    2: InjectionSpec(attempt_1="invalid_name", attempt_2="valid", invalid_name="Код"),
    3: InjectionSpec(attempt_1="timeout", attempt_2="timeout"),
    4: InjectionSpec(attempt_1="config_error", attempt_2="valid"),
    5: InjectionSpec(attempt_1="empty", attempt_2="valid"),
    6: InjectionSpec(attempt_1="invalid_number", attempt_2="invalid_number", invalid_number=99),
}


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def load_canon_and_templates():
    canon = load_canon(CANON_PATH)
    templates = load_templates(TEMPLATES_PATH, set(canon.reason_codes.keys()), set(canon.verbs.keys()))
    return canon, templates


def run_golden_path(session, path, client=None):
    results = []
    if client and hasattr(client, "set_turn"):
        client.set_turn(0)

    for i, (command, expected_code, _) in enumerate(path, start=1):
        if client and hasattr(client, "set_turn"):
            client.set_turn(i)
        events_before = len(session._events)
        result = session.step(command)
        events_after = len(session._events)
        results.append({
            "turn": i,
            "command": command,
            "result": result,
            "expected_code": expected_code,
            "events_count": events_after - events_before,
        })
    return results


def check_c1_golden_path_passed(results, expected_path):
    """C-1: Золотой путь проходится."""
    assert len(results) == len(expected_path)
    for r in results:
        assert not r["result"].aborted, f"Turn {r['turn']}: aborted"


def check_c2_final_projection(session):
    """C-2: Финальная проекция = эталон."""
    state = session._state
    assert state.location == EXPECTED_FINAL_STATE["location"]
    assert state.flags.get("game_won") == EXPECTED_FINAL_STATE["game_won"]
    assert state.flags.get("bunker_door_open") == EXPECTED_FINAL_STATE["bunker_door_open"]
    assert state.flags.get("has_code") == EXPECTED_FINAL_STATE["has_code"]
    assert state.flags.get("wounded_watered") == EXPECTED_FINAL_STATE["wounded_watered"]
    assert dict(state.inventory) == EXPECTED_FINAL_STATE["inventory"]
    for loc_id, expected_items in EXPECTED_FINAL_STATE["item_placement"].items():
        actual_items = state.item_placement.get(loc_id, {})
        assert dict(actual_items) == expected_items, f"loc {loc_id}: {dict(actual_items)} != {expected_items}"
    assert dict(state.npc_locations) == EXPECTED_FINAL_STATE["npc_locations"]
    assert state.turn == EXPECTED_FINAL_STATE["turn"]


def check_c3_game_finished(results):
    """C-3: game_finished = true."""
    last_result = results[-1]["result"]
    assert last_result.game_finished is True


def check_c4_all_texts_nonempty(session):
    """C-4: Все тексты непустые."""
    for event in session._events:
        if event.kind == "presentation":
            assert event.final_text, f"Turn {event.turn}: empty final_text"
            assert "{" not in event.final_text, f"Turn {event.turn}: unfilled slot in final_text"


def check_c5_no_mechanics_leak(session, canon):
    """C-5: Нет утечки механики в текст."""
    for event in session._events:
        if event.kind == "presentation":
            text = event.final_text.lower()
            for flag_id in canon.flags:
                assert flag_id.lower() not in text, f"Turn {event.turn}: flag {flag_id} in final_text"
            for code in canon.reason_codes:
                if "_" in code:
                    assert code.lower() not in text, f"Turn {event.turn}: reason_code {code} in final_text"


def check_c6_session_jsonl_consistent(session):
    """C-6: session.jsonl консистентен."""
    turn_events = [e for e in session._events if e.kind == "turn"]
    presentation_events = [e for e in session._events if e.kind == "presentation"]
    assert len(turn_events) == len(presentation_events)
    for turn_ev, pres_ev in zip(turn_events, presentation_events):
        assert turn_ev.turn == pres_ev.turn


def check_c8_no_aborted(results):
    """C-8: Нет aborted."""
    for r in results:
        assert not r["result"].aborted, f"Turn {r['turn']}: aborted"


def check_c9_no_rung_4(session):
    """C-9: Нет rung=4."""
    for event in session._events:
        if event.kind == "presentation":
            assert event.rung != 4, f"Turn {event.turn}: rung=4"


# ---------------------------------------------------------------------------
# Тесты режима A (no-llm)
# ---------------------------------------------------------------------------


def test_e_a_1_golden_path_no_llm(tmp_path):
    """E-A-1: Золотой путь в режиме no-llm."""
    canon, templates = load_canon_and_templates()
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    results = run_golden_path(session, GOLDEN_PATH)

    check_c1_golden_path_passed(results, GOLDEN_PATH)
    check_c2_final_projection(session)
    check_c3_game_finished(results)
    check_c4_all_texts_nonempty(session)
    check_c5_no_mechanics_leak(session, canon)
    check_c6_session_jsonl_consistent(session)
    check_c8_no_aborted(results)
    check_c9_no_rung_4(session)

    for event in session._events:
        if event.kind == "presentation":
            assert event.rung == 3, f"Turn {event.turn}: rung={event.rung}, expected 3"


def test_e_a_2_negative_scenarios_no_llm():
    """E-A-2: Негативные сценарии в режиме no-llm."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)

    session = Session.start(config)
    result = session.step("идти вверх")
    assert session._lookup.current_location() == "loc_shelter"

    session.step("идти на восток")
    result = session.step("идти на север")
    assert session._lookup.current_location() == "loc_market"

    session.step("взять фильтр")
    result = session.step("взять фильтр")

    session2 = Session.start(config)
    result = session2.step("бросить флягу")

    session3 = Session.start(config)
    session3.step("идти на восток")
    result = session3.step("поговорить с торговцем про флягу")

    session4 = Session.start(config)
    session4.step("идти на восток")
    session4.step("взять фонарь")
    result = session4.step("использовать фонарь на дверь")

    session5 = Session.start(config)
    result = session5.step("поговорить с раненым про код")

    session6 = Session.start(config)
    events_before = len([e for e in session6._events if e.kind == "turn"])
    result = session6.step("абракадабра")
    events_after = len([e for e in session6._events if e.kind == "turn"])
    assert events_after == events_before + 1
    presentation = session6._events[-1]
    assert presentation.rung is None

    session7 = Session.start(config)
    result = session7.step("")


def test_e_a_3_save_load_no_llm(tmp_path):
    """E-A-3: save / load после золотого пути в режиме no-llm."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    run_golden_path(session, GOLDEN_PATH)

    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    loaded = Session.load(save_path, config)

    assert loaded._state.location == session._state.location
    assert dict(loaded._state.inventory) == dict(session._state.inventory)
    assert dict(loaded._state.flags) == dict(session._state.flags)
    assert loaded._state.turn == session._state.turn


# ---------------------------------------------------------------------------
# Тесты режима C (broken-llm)
# ---------------------------------------------------------------------------


def test_e_c_1_golden_path_broken_llm(tmp_path):
    """E-C-1: Золотой путь в режиме broken-llm."""
    canon, templates = load_canon_and_templates()
    config = SessionConfig(mode="broken-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)

    client = BrokenLLMClient(MODE_C_INJECTIONS)
    validator = Validator(canon)

    session = Session.start(config)
    session._client = client

    results = run_golden_path(session, GOLDEN_PATH, client)

    check_c1_golden_path_passed(results, GOLDEN_PATH)
    check_c2_final_projection(session)
    check_c3_game_finished(results)
    check_c4_all_texts_nonempty(session)
    check_c5_no_mechanics_leak(session, canon)
    check_c6_session_jsonl_consistent(session)
    check_c8_no_aborted(results)
    check_c9_no_rung_4(session)

    presentations = [e for e in session._events if e.kind == "presentation"]
    assert presentations[0].rung == 2
    assert presentations[1].rung == 2
    assert presentations[2].rung == 3
    assert presentations[3].rung == 3
    assert presentations[4].rung == 2
    assert presentations[5].rung == 3


def test_e_c_2_match_with_a_on_every_turn(tmp_path):
    """E-C-2: Сверка с A на каждом ходу."""
    canon, templates = load_canon_and_templates()
    config_a = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    config_c = SessionConfig(mode="broken-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)

    session_a = Session.start(config_a)
    session_c = Session.start(config_c)
    client_c = BrokenLLMClient(MODE_C_INJECTIONS)
    session_c._client = client_c

    for i, (command, _, _) in enumerate(GOLDEN_PATH, start=1):
        client_c.set_turn(i)
        session_a.step(command)
        session_c.step(command)

        assert session_a._state.location == session_c._state.location, f"Turn {i}: location mismatch"
        assert dict(session_a._state.inventory) == dict(session_c._state.inventory), f"Turn {i}: inventory mismatch"
        assert dict(session_a._state.flags) == dict(session_c._state.flags), f"Turn {i}: flags mismatch"


def test_e_c_3_save_load_broken_llm(tmp_path):
    """E-C-3: save / load в режиме broken-llm."""
    canon, templates = load_canon_and_templates()
    config = SessionConfig(mode="broken-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)

    client = BrokenLLMClient(MODE_C_INJECTIONS)
    session = Session.start(config)
    session._client = client

    for i in range(5):
        command = GOLDEN_PATH[i][0]
        client.set_turn(i + 1)
        session.step(command)

    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    loaded = Session.load(save_path, config)

    assert loaded._state.location == session._state.location
    assert dict(loaded._state.inventory) == dict(session._state.inventory)


# ---------------------------------------------------------------------------
# Тесты варианта золотого пути
# ---------------------------------------------------------------------------


def test_e_var_1_variant_golden_path_no_llm():
    """E-VAR-1: Вариант золотого пути в режиме no-llm."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    results = run_golden_path(session, GOLDEN_PATH_VARIANT)

    assert session._state.location == "loc_bunker"
    assert session._state.flags.get("game_won") is True
    assert session._state.turn == 9


def test_e_var_2_variant_in_mode_a():
    """E-VAR-2: Вариант в режиме A."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    results = run_golden_path(session, GOLDEN_PATH_VARIANT)

    check_c1_golden_path_passed(results, GOLDEN_PATH_VARIANT)

    assert session._state.location == "loc_bunker"
    assert session._state.flags.get("game_won") is True
    check_c3_game_finished(results)
    check_c4_all_texts_nonempty(session)
    check_c8_no_aborted(results)


def test_e_var_3_variant_in_mode_c(tmp_path):
    """E-VAR-3: Вариант в режиме C (сверка с A на каждом ходу)."""
    config_a = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    config_c = SessionConfig(mode="broken-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)

    session_a = Session.start(config_a)
    session_c = Session.start(config_c)
    client_c = BrokenLLMClient(MODE_C_INJECTIONS)
    session_c._client = client_c

    for i, (command, _, _) in enumerate(GOLDEN_PATH_VARIANT, start=1):
        client_c.set_turn(i)
        session_a.step(command)
        session_c.step(command)

        assert session_a._state.location == session_c._state.location
        assert dict(session_a._state.inventory) == dict(session_c._state.inventory)


# ---------------------------------------------------------------------------
# Тесты Save / Load
# ---------------------------------------------------------------------------


def test_e_sl_1_save_after_turn_5_load_continue(tmp_path):
    """E-SL-1: Save после хода 5, load, продолжить с 6."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    for i in range(5):
        command = GOLDEN_PATH[i][0]
        session.step(command)

    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    loaded = Session.load(save_path, config)

    for i in range(5, len(GOLDEN_PATH)):
        command = GOLDEN_PATH[i][0]
        loaded.step(command)

    assert loaded._state.location == EXPECTED_FINAL_STATE["location"]
    assert loaded._state.flags.get("game_won") is True


def test_e_sl_2_save_after_win(tmp_path):
    """E-SL-2: Save после хода 15 (победа)."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    run_golden_path(session, GOLDEN_PATH)

    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    loaded = Session.load(save_path, config)

    assert loaded._game_finished is True


def test_e_sl_3_save_twice_overwrite(tmp_path):
    """E-SL-3: Save дважды (перезапись)."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    session.step("осмотреться")
    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    session.step("идти на восток")
    session.save(save_path)

    loaded = Session.load(save_path, config)
    assert loaded._state.location == "loc_market"


def test_e_sl_4_load_without_file(tmp_path):
    """E-SL-4: Load без файла (в интерфейсе)."""
    pass


def test_e_sl_5_presentation_missing_last_turn(tmp_path):
    """E-SL-5: presentation отсутствует у последнего хода."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    session.step("осмотреться")

    if session._events[-1].kind == "presentation":
        session._events.pop()

    turn_events = [e for e in session._events if e.kind == "turn"]
    assert len(turn_events) == 1
    assert turn_events[0].turn == 1

    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    loaded = Session.load(save_path, config)
    assert loaded._state.turn == 1


def test_e_sl_6_replay_after_load(tmp_path):
    """E-SL-6: Replay после load."""
    config = SessionConfig(mode="no-llm", canon_path=CANON_PATH, templates_path=TEMPLATES_PATH)
    session = Session.start(config)

    run_golden_path(session, GOLDEN_PATH)

    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    loaded = Session.load(save_path, config)

    assert loaded._state.location == session._state.location
    assert dict(loaded._state.inventory) == dict(session._state.inventory)
    assert dict(loaded._state.flags) == dict(session._state.flags)
    assert dict(loaded._state.stats) == dict(session._state.stats)
    assert (
        {k: dict(v) for k, v in loaded._state.item_placement.items()}
        == {k: dict(v) for k, v in session._state.item_placement.items()}
    )