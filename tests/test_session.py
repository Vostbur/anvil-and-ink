import pytest

from anvil_and_ink.engine import apply_command as engine_apply_command
from anvil_and_ink.llm_ladder import UNIVERSAL_STUB, run as ladder_run
from anvil_and_ink.loaders import load_canon, load_templates
from anvil_and_ink.models import Manifest, Outcome, Primitive
from anvil_and_ink.parser import parse as parse_command
from anvil_and_ink.projection import apply as projection_apply, build_initial
from anvil_and_ink.prompt_builder import BuildError
from anvil_and_ink.session import Session, SessionConfig, _event_to_dict
from anvil_and_ink.templates_renderer import build_render_snapshot, render as render_template
from anvil_and_ink.validator import Reason, Verdict


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def canon():
    return load_canon("canon.yaml")


@pytest.fixture(scope="module")
def templates(canon):
    return load_templates(
        "templates.yaml",
        set(canon.reason_codes.keys()),
        set(canon.verbs.keys()),
    )


# ---------------------------------------------------------------------------
# Фейки
# ---------------------------------------------------------------------------


class FakeBuilder:
    def __init__(self, manifest, raise_build_error=False):
        self.manifest = manifest
        self.raise_build_error = raise_build_error
        self.calls = []

    def __call__(self, command, outcome, projection, canon, recent_events, turn, config, retry_note=None):
        if self.raise_build_error:
            raise BuildError("prompt overflow")
        self.calls.append({"retry_note": retry_note})
        return "PROMPT", self.manifest


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, prompt, config):
        self.calls.append(prompt)
        idx = len(self.calls) - 1
        if idx < len(self.results):
            return self.results[idx]
        return self.results[-1] if self.results else {"ok": False, "error_class": "timeout_or_network"}


class FakeValidator:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    def validate(self, response, manifest):
        self.calls.append(response)
        idx = len(self.calls) - 1
        if idx < len(self.verdicts):
            return self.verdicts[idx]
        return self.verdicts[-1]


class FakeRenderer:
    def __init__(self, text="ШАБЛОН", should_raise=False):
        self.text = text
        self.should_raise = should_raise
        self.calls = []

    def __call__(self, verb, reason_code, outcome, snapshot, turn, templates):
        self.calls.append({"verb": verb, "reason_code": reason_code})
        if self.should_raise:
            raise RuntimeError("renderer crashed")
        return self.text


def ok_verdict():
    return Verdict(True, None)


def ok_response(text="хороший ответ"):
    return {"ok": True, "text": text, "latency_ms": 100, "tokens": 10}


def error_response(error_class):
    return {"ok": False, "error_class": error_class, "detail": "", "latency_ms": 100, "tokens": None}


def make_manifest():
    return Manifest(allowed_names=set(), allowed_numbers=set(), mode="describe", verb="TAKE", reason_code="ok")


def state_at_market(canon):
    return projection_apply(build_initial(canon), [Primitive(type="change_location", to="loc_market")])


def make_session(canon, templates, *, client, validator, builder, renderer=None, engine=None, state=None, config=None):
    config = config or SessionConfig()
    return Session(
        config=config,
        canon=canon,
        templates=templates,
        parse=parse_command,
        apply_command=engine or engine_apply_command,
        build=builder,
        client=client,
        validator=validator,
        render=renderer or render_template,
        ladder=ladder_run,
        snapshot_builder=build_render_snapshot,
        state=state or build_initial(canon),
        events=[],
        turn=0,
        game_finished=False,
    )


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_s_1_valid_llm_turn(canon, templates):
    builder = FakeBuilder(make_manifest())
    client = FakeClient([ok_response("Вы берёте фильтр.")])
    validator = FakeValidator([ok_verdict()])
    session = make_session(canon, templates, client=client, validator=validator,
                           builder=builder, state=state_at_market(canon))

    result = session.step("взять фильтр")

    assert result.text == "Вы берёте фильтр."
    assert result.aborted is False
    events = session._events
    assert len(events) == 2
    assert events[0].kind == "turn"
    assert events[1].kind == "presentation"
    assert events[1].rung == 1
    # Проекция обновлена, ход записан до презентации.
    assert session._lookup.has_item("item_filter")


def test_s_2_llm_failed_twice(canon, templates):
    builder = FakeBuilder(make_manifest())
    client = FakeClient([error_response("timeout_or_network"), error_response("timeout_or_network")])
    validator = FakeValidator([])
    session = make_session(canon, templates, client=client, validator=validator,
                           builder=builder, state=state_at_market(canon))

    result = session.step("взять фильтр")

    presentation = session._events[-1]
    assert presentation.kind == "presentation"
    assert presentation.rung == 3
    assert result.text  # шаблонный текст не пуст
    # Проекция обновлена, несмотря на отказ LLM.
    assert session._lookup.has_item("item_filter")


def test_s_3_unknown(canon, templates):
    builder = FakeBuilder(make_manifest())
    client = FakeClient([])
    validator = FakeValidator([])
    session = make_session(canon, templates, client=client, validator=validator, builder=builder)

    result = session.step("абракадабра")

    assert len(client.calls) == 0
    presentation = session._events[-1]
    assert presentation.rung is None
    assert result.text
    # Состояние не изменилось.
    assert session._lookup.current_location() == "loc_shelter"


def test_s_4_build_error(canon, templates):
    builder = FakeBuilder(make_manifest(), raise_build_error=True)
    client = FakeClient([])
    validator = FakeValidator([])
    session = make_session(canon, templates, client=client, validator=validator,
                           builder=builder, state=state_at_market(canon))

    result = session.step("взять фильтр")

    assert len(client.calls) == 0
    presentation = session._events[-1]
    assert presentation.rung == 3
    assert result.text


def test_s_5_renderer_crash(canon, templates):
    builder = FakeBuilder(make_manifest())
    client = FakeClient([error_response("config_error")])
    validator = FakeValidator([])
    renderer = FakeRenderer(should_raise=True)
    session = make_session(canon, templates, client=client, validator=validator,
                           builder=builder, renderer=renderer, state=state_at_market(canon))

    result = session.step("взять фильтр")

    assert result.text == UNIVERSAL_STUB
    presentation = session._events[-1]
    assert presentation.rung == 4


def test_s_6_integrity_error_aborts(canon, templates):
    bad_outcome = Outcome(
        result="ok", reason_code="ok",
        applied=(Primitive(type="remove_from_inventory", item="item_filter", count=999),),
        response_text=None,
    )

    def bad_engine(command, projection, canon_):
        return bad_outcome

    builder = FakeBuilder(make_manifest())
    client = FakeClient([])
    validator = FakeValidator([])
    session = make_session(canon, templates, client=client, validator=validator,
                           builder=builder, engine=bad_engine, state=state_at_market(canon))

    events_before = len(session._events)
    result = session.step("взять фильтр")

    assert result.aborted is True
    assert result.abort_reason == "integrity_error"
    # Лог без частичной записи: turn-событие не добавлено.
    assert len(session._events) == events_before


def test_s_7_win(canon, templates):
    state = build_initial(canon)
    state = projection_apply(state, [
        Primitive(type="change_location", to="loc_market"),
        Primitive(type="set_flag", flag="bunker_door_open", value=True),
    ])
    builder = FakeBuilder(make_manifest())
    validator = FakeValidator([])
    session = make_session(canon, templates, client=None, validator=validator,
                           builder=builder, state=state)

    result = session.step("идти на север")

    assert result.game_finished is True
    assert any(e.kind == "presentation" for e in session._events)
    assert session._lookup.flag("game_won") is True
    assert session._lookup.current_location() == "loc_bunker"


def test_s_8_replay(canon, templates, tmp_path):
    builder = FakeBuilder(make_manifest())
    validator = FakeValidator([])
    session = make_session(canon, templates, client=None, validator=validator, builder=builder)

    session.step("идти на восток")
    session.step("осмотреться")

    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    loaded = Session.load(save_path, SessionConfig())

    assert loaded._state.location == session._state.location == "loc_market"
    assert dict(loaded._state.inventory) == dict(session._state.inventory)
    assert dict(loaded._state.flags) == dict(session._state.flags)
    assert dict(loaded._state.stats) == dict(session._state.stats)
    assert (
        {k: dict(v) for k, v in loaded._state.item_placement.items()}
        == {k: dict(v) for k, v in session._state.item_placement.items()}
    )
    assert dict(loaded._state.npc_locations) == dict(session._state.npc_locations)
    assert loaded._turn == session._turn


def test_s_9_record_order_on_llm_failure(canon, templates):
    builder = FakeBuilder(make_manifest())
    client = FakeClient([error_response("timeout_or_network"), error_response("timeout_or_network")])
    validator = FakeValidator([])
    session = make_session(canon, templates, client=client, validator=validator,
                           builder=builder, state=state_at_market(canon))

    session.step("взять фильтр")

    events = session._events
    assert events[0].kind == "turn"
    assert events[1].kind == "presentation"
    assert events[1].rung == 3


def test_s_10_no_llm_all_templates(canon, templates):
    builder = FakeBuilder(make_manifest())
    validator = FakeValidator([])
    session = make_session(canon, templates, client=None, validator=validator, builder=builder)

    session.step("идти на восток")
    session.step("осмотреться")

    presentations = [e for e in session._events if e.kind == "presentation"]
    assert len(presentations) == 2
    assert all(p.rung == 3 for p in presentations)


def test_s_11_broken_llm_projection_matches_no_llm(canon, templates):
    builder = FakeBuilder(make_manifest())
    broken_client = FakeClient([error_response("timeout_or_network")] * 10)
    validator = FakeValidator([])
    broken = make_session(canon, templates, client=broken_client, validator=validator, builder=builder)
    broken.step("идти на восток")

    builder2 = FakeBuilder(make_manifest())
    validator2 = FakeValidator([])
    reference = make_session(canon, templates, client=None, validator=validator2, builder=builder2)
    reference.step("идти на восток")

    assert broken._state.location == reference._state.location
    assert dict(broken._state.inventory) == dict(reference._state.inventory)
    assert dict(broken._state.flags) == dict(reference._state.flags)


def test_s_12_empty_input(canon, templates):
    builder = FakeBuilder(make_manifest())
    client = FakeClient([])
    validator = FakeValidator([])
    session = make_session(canon, templates, client=client, validator=validator, builder=builder)

    location_before = session._lookup.current_location()
    result = session.step("")

    assert len(client.calls) == 0
    presentation = session._events[-1]
    assert presentation.rung is None
    assert result.text
    assert session._lookup.current_location() == location_before


def test_s_13_load_and_continue(canon, templates, tmp_path):
    builder = FakeBuilder(make_manifest())
    validator = FakeValidator([])
    session = make_session(canon, templates, client=None, validator=validator, builder=builder)

    session.step("идти на восток")
    save_path = str(tmp_path / "save.jsonl")
    session.save(save_path)

    loaded = Session.load(save_path, SessionConfig())
    assert loaded._turn == 1
    assert loaded._state.location == "loc_market"

    result = loaded.step("осмотреться")
    assert result.text
    assert loaded._turn == 2


def test_s_14_two_looks_different_text_same_state(canon, templates):
    builder = FakeBuilder(make_manifest())
    validator = FakeValidator([])
    session = make_session(canon, templates, client=None, validator=validator, builder=builder)

    r1 = session.step("осмотреться")
    state1 = session._state
    r2 = session.step("осмотреться")
    state2 = session._state

    assert r1.text != r2.text
    assert state1.location == state2.location
    assert dict(state1.inventory) == dict(state2.inventory)
    assert dict(state1.flags) == dict(state2.flags)


def test_s_15_final_text_not_in_turn_record(canon, templates):
    builder = FakeBuilder(make_manifest())
    client = FakeClient([ok_response("Вы берёте фильтр.")])
    validator = FakeValidator([ok_verdict()])
    session = make_session(canon, templates, client=client, validator=validator,
                           builder=builder, state=state_at_market(canon))

    session.step("взять фильтр")

    turn_event = next(e for e in session._events if e.kind == "turn")
    presentation_event = next(e for e in session._events if e.kind == "presentation")

    assert turn_event.final_text is None
    assert presentation_event.final_text == "Вы берёте фильтр."

    turn_dict = _event_to_dict(turn_event)
    assert "final_text" not in turn_dict