import re
import pytest
from dataclasses import replace

from anvil_and_ink.loaders import load_canon
from anvil_and_ink.models import Command, Event, Outcome, Primitive
from anvil_and_ink.projection import ProjectionLookup, apply as apply_prims, build_initial
from anvil_and_ink.prompt_builder import BuildError, PromptBuilderConfig, build


CANON_PATH = "canon.yaml"
ALL_HEADERS = ["[SYSTEM]", "[SCENE]", "[RECENT]", "[THIS TURN]", "[TASK]"]


@pytest.fixture(scope="module")
def canon():
    return load_canon(CANON_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def proj(canon, state):
    return ProjectionLookup(state, canon)


def state_with(canon, primitives):
    state = build_initial(canon)
    if primitives:
        state = apply_prims(state, primitives)
    return state


def norm(s):
    return s.lower().replace("ё", "е")

def name_present(name_norm, norm_prompt):
    pattern = r"(?<!\w)" + re.escape(name_norm) + r"(?!\w)"
    return re.search(pattern, norm_prompt) is not None

def make_command(verb, raw="<тест>", **args):
    resolved = {k: (v is not None) for k, v in args.items()}
    return Command(verb=verb, args=args, resolved=resolved, raw=raw)


def ok_outcome(applied=(), response_text=None):
    return Outcome(result="ok", reason_code="ok", applied=applied, response_text=response_text)


def fail_outcome(reason, applied=(), response_text=None):
    return Outcome(result="fail", reason_code=reason, applied=applied, response_text=response_text)


def recent_event(turn, verb="GO", direction="east", item=None, npc=None, target=None, result="ok"):
    args = {}
    if direction and verb == "GO":
        args["direction"] = direction
    if item:
        args["item"] = item
    if npc:
        args["npc"] = npc
    if target:
        args["target"] = target
    resolved = {k: True for k in args}
    cmd = Command(verb=verb, args=args, resolved=resolved, raw="...")
    reason = "ok" if result == "ok" else "no_exit"
    out = Outcome(result=result, reason_code=reason, applied=(), response_text=None)
    return Event(kind="turn", turn=turn, command=cmd, outcome=out)


def with_description(canon, loc_id, description):
    loc = canon.locations[loc_id]
    new_loc = replace(loc, description=description)
    new_locations = dict(canon.locations)
    new_locations[loc_id] = new_loc
    return replace(canon, locations=new_locations)


def _verb_setups(canon):
    """Наборы (команда, исход, проекция) для каждого из 6 глаголов."""
    setups = {}

    # LOOK
    st = build_initial(canon)
    setups["LOOK"] = (
        make_command("LOOK", raw="осмотреться"),
        ok_outcome(),
        proj(canon, st),
    )

    # GO (успешный переход на восток → рынок)
    st = state_with(canon, [Primitive(type="change_location", to="loc_market")])
    setups["GO"] = (
        make_command("GO", raw="идти на восток", direction="east"),
        ok_outcome(applied=(Primitive(type="change_location", to="loc_market"),)),
        proj(canon, st),
    )

    # TAKE (фляга в инвентаре)
    st = state_with(canon, [
        Primitive(type="remove_from_location", to="loc_shelter", item="item_flask", count=1),
        Primitive(type="add_to_inventory", item="item_flask", count=1),
    ])
    setups["TAKE"] = (
        make_command("TAKE", raw="взять флягу", item="item_flask"),
        ok_outcome(applied=(
            Primitive(type="remove_from_location", to="loc_shelter", item="item_flask", count=1),
            Primitive(type="add_to_inventory", item="item_flask", count=1),
        )),
        proj(canon, st),
    )

    # DROP
    st = build_initial(canon)
    setups["DROP"] = (
        make_command("DROP", raw="бросить флягу", item="item_flask"),
        ok_outcome(applied=(
            Primitive(type="remove_from_inventory", item="item_flask", count=1),
            Primitive(type="add_to_location", to="loc_shelter", item="item_flask", count=1),
        )),
        proj(canon, st),
    )

    # TALK (торговец, тема «код»)
    st = state_with(canon, [
        Primitive(type="change_location", to="loc_market"),
        Primitive(type="add_to_inventory", item="item_code", count=1),
        Primitive(type="set_flag", flag="has_code", value=True),
    ])
    setups["TALK"] = (
        make_command("TALK", raw="поговорить с торговцем про код", npc="npc_trader", topic="код"),
        ok_outcome(
            applied=(Primitive(type="set_flag", flag="has_code", value=True),),
            response_text="Держи. Не потеряй.",
        ),
        proj(canon, st),
    )

    # USE (код на дверь)
    st = state_with(canon, [
        Primitive(type="change_location", to="loc_market"),
        Primitive(type="add_to_inventory", item="item_code", count=1),
        Primitive(type="set_flag", flag="bunker_door_open", value=True),
    ])
    setups["USE"] = (
        make_command("USE", raw="использовать код на дверь", item="item_code", target="exit_north_market"),
        ok_outcome(
            applied=(Primitive(type="set_flag", flag="bunker_door_open", value=True),),
            response_text="Замок щёлкает, дверь приоткрывается.",
        ),
        proj(canon, st),
    )

    return setups


# ---------------------------------------------------------------------------
# PB-1: все 6 глаголов, все 5 блоков
# ---------------------------------------------------------------------------


def test_pb_1_all_verbs_all_blocks(canon):
    for verb, (cmd, outcome, projection) in _verb_setups(canon).items():
        prompt, manifest = build(cmd, outcome, projection, canon, [], 1)
        for header in ALL_HEADERS:
            assert header in prompt, f"missing {header} for verb {verb}"
        assert manifest.verb == verb


# ---------------------------------------------------------------------------
# PB-2: LOOK-режим
# ---------------------------------------------------------------------------


def test_pb_2_look_mode(canon):
    cmd, outcome, projection = _verb_setups(canon)["LOOK"]
    prompt, manifest = build(cmd, outcome, projection, canon, [], 1)
    assert manifest.mode == "look"
    assert "Опиши сцену, которую видит игрок." in prompt


# ---------------------------------------------------------------------------
# PB-3: приватные (UNKNOWN) события не попадают в RECENT
# ---------------------------------------------------------------------------


def test_pb_3_unknown_events_excluded_from_recent(canon):
    st = state_with(canon, [Primitive(type="change_location", to="loc_market")])
    cmd = make_command("LOOK", raw="осмотреться")
    outcome = ok_outcome()

    unknown_ev = Event(
        kind="turn",
        turn=1,
        command=Command(verb="UNKNOWN", args={}, resolved={}, raw="абракадабра"),
        outcome=fail_outcome("unknown"),
    )
    go_ev = recent_event(2, verb="GO", direction="east")

    prompt, _ = build(cmd, outcome, proj(canon, st), canon, [unknown_ev, go_ev], 3)

    assert "Ход 1" not in prompt
    assert "Ход 2" in prompt


# ---------------------------------------------------------------------------
# PB-4: имена флагов и reason_code не попадают в промпт
# ---------------------------------------------------------------------------


def test_pb_4_no_flags_or_reason_codes_in_prompt(canon):
    cmd, outcome, projection = _verb_setups(canon)["USE"]
    prompt, _ = build(cmd, outcome, projection, canon, [], 1)

    # Имена флагов из applied (set_flag) не упоминаются.
    assert "bunker_door_open" not in prompt
    assert "has_code" not in prompt
    assert "game_won" not in prompt

    # Сырые коды причин не упоминаются.
    for code in canon.reason_codes:
        if "_" in code:  # многословные коды легко проверить на вхождение
            assert code not in prompt


# ---------------------------------------------------------------------------
# PB-5: allowed_names содержит все имена из промпта
# ---------------------------------------------------------------------------


def test_pb_5_allowed_names_cover_prompt(canon):
    st = state_with(canon, [Primitive(type="change_location", to="loc_market")])
    cmd = make_command("LOOK", raw="осмотреться")
    outcome = ok_outcome()
    prompt, manifest = build(cmd, outcome, proj(canon, st), canon, [], 1)

    norm_prompt = norm(prompt)

    all_names = []
    for loc in canon.locations.values():
        all_names.append(loc.name)
    for npc in canon.npcs.values():
        all_names.append(npc.name)
    for item in canon.items.values():
        all_names.append(item.name)
    for ut in canon.use_targets.values():
        all_names.append(ut.name)
    for d in canon.directions.values():
        all_names.append(d.name)

    normed = [norm(n) for n in all_names if n]
    # Ищем по границам слова, чтобы «код» не матчился внутрь «коды» (SYSTEM).
    present = {n for n in normed if name_present(n, norm_prompt)}

    for n in present:
        # Имя может входить как целое слово в более длинное имя сущности,
        # которое само присутствует в промпте (например, «бункер» в «дверь в бункер»).
        # Такое вхождение объяснено длинным именем и не требует отдельного разрешения.
        explained_by_longer = any(n != other and n in other for other in present)
        if explained_by_longer:
            continue
        assert n in manifest.allowed_names, f"{n} in prompt but not in allowed_names"


# ---------------------------------------------------------------------------
# PB-6: allowed_numbers не содержит номеров ходов
# ---------------------------------------------------------------------------


def test_pb_6_allowed_numbers_exclude_turn_numbers(canon):
    st = build_initial(canon)  # инвентарь пуст, количеств > 1 нет
    cmd = make_command("LOOK", raw="осмотреться")
    outcome = ok_outcome()

    recents = [recent_event(i, verb="GO", direction="east") for i in range(1, 4)]
    prompt, manifest = build(cmd, outcome, proj(canon, st), canon, recents, 4)

    assert 1 not in manifest.allowed_numbers
    assert 2 not in manifest.allowed_numbers
    assert 3 not in manifest.allowed_numbers


# ---------------------------------------------------------------------------
# PB-7: длинный recent усекается до >= 3
# ---------------------------------------------------------------------------


def test_pb_7_long_recent_truncated(canon):
    st = state_with(canon, [Primitive(type="change_location", to="loc_market")])
    cmd = make_command("LOOK", raw="осмотреться")
    outcome = ok_outcome()

    recents = [recent_event(i, verb="GO", direction="east") for i in range(1, 11)]
    prompt, _ = build(cmd, outcome, proj(canon, st), canon, recents, 11)

    count = prompt.count("Ход ")
    assert 3 <= count <= 5


# ---------------------------------------------------------------------------
# PB-8: длинное описание локации усекается до первого предложения
# ---------------------------------------------------------------------------


def test_pb_8_long_description_truncated_to_first_sentence(canon):
    first = "Короткое первое предложение."
    # «потолок» встречается только во втором и далее предложениях описания;
    # в SYSTEM, RECENT, THIS TURN и TASK этого слова нет.
    tail = "Обрушившийся потолок завален обломками, которые никто не разбирал уже много лет. "
    long_desc = first + " " + tail * 20
    custom_canon = with_description(canon, "loc_shelter", long_desc)

    st = build_initial(custom_canon)
    cmd = make_command("LOOK", raw="осмотреться")
    outcome = ok_outcome()

    # Бюджет внутри большого зазора:
    # уровень 0 (полное описание) заметно превышает, уровень 1 (первое предложение) — укладывается.
    config = PromptBuilderConfig(total_budget=450)
    prompt, _ = build(cmd, outcome, proj(custom_canon, st), custom_canon, [], 1, config)

    assert first in prompt
    assert "потолок" not in norm(prompt)


# ---------------------------------------------------------------------------
# PB-9: переполнение после всех усечений → BuildError
# ---------------------------------------------------------------------------


def test_pb_9_overflow_raises_build_error(canon):
    st = build_initial(canon)
    cmd = make_command("LOOK", raw="осмотреться")
    outcome = ok_outcome()

    config = PromptBuilderConfig(total_budget=10)
    with pytest.raises(BuildError):
        build(cmd, outcome, proj(canon, st), canon, [], 1, config)


# ---------------------------------------------------------------------------
# PB-10: set_flag в applied не упоминается в промпте
# ---------------------------------------------------------------------------


def test_pb_10_set_flag_hidden(canon):
    cmd, outcome, projection = _verb_setups(canon)["USE"]
    prompt, _ = build(cmd, outcome, projection, canon, [], 1)
    assert "bunker_door_open" not in prompt


# ---------------------------------------------------------------------------
# PB-11: replay того же хода → идентичный промпт
# ---------------------------------------------------------------------------


def test_pb_11_deterministic_replay(canon):
    cmd, outcome, projection = _verb_setups(canon)["TAKE"]
    prompt_a, manifest_a = build(cmd, outcome, projection, canon, [], 5)
    prompt_b, manifest_b = build(cmd, outcome, projection, canon, [], 5)
    assert prompt_a == prompt_b
    assert manifest_a == manifest_b


# ---------------------------------------------------------------------------
# PB-12: {scene} (шаблонный слот) не используется в промпте
# ---------------------------------------------------------------------------


def test_pb_12_no_scene_slot_in_prompt(canon):
    cmd, outcome, projection = _verb_setups(canon)["LOOK"]
    prompt, _ = build(cmd, outcome, projection, canon, [], 1)
    assert "{scene}" not in prompt