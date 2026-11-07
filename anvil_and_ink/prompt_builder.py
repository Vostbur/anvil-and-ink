from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .models import Canon, Command, Event, Manifest, Outcome
from .projection import ProjectionLookup


class BuildError(Exception):
    """Переполнение бюджета после всех усечений. Сессия переходит к шаблону (WARNING)."""


@dataclass(frozen=True)
class PromptBuilderConfig:
    total_budget: int = 1250   # D-31
    system_budget: int = 300
    min_recent: int = 3
    max_recent: int = 5


_MODE_LOOK = "look"
_MODE_DESCRIBE = "describe"

_MODE_INSTRUCTIONS = {
    _MODE_LOOK: "Опиши сцену, которую видит игрок.",
    _MODE_DESCRIBE: "Опиши, что произошло.",
}

# Наблюдаемые описания кодов причин. Сами коды в промпт не попадают.
_REASON_DESCRIPTIONS = {
    "ok": "действие удалось",
    "no_exit": "пройти туда нельзя — выхода нет",
    "locked": "путь заперт",
    "no_item_here": "такого предмета здесь нет",
    "not_takeable": "это нельзя взять",
    "not_in_inventory": "этого нет в инвентаре",
    "no_npc_here": "такого собеседника здесь нет",
    "no_topic": "собеседник не отвечает на эту тему",
    "condition_not_met": "пока это невозможно",
    "no_target": "нет такой цели",
    "target_not_here": "цель находится в другом месте",
    "invalid_combination": "так это применить нельзя",
    "unknown": "команда не распознана",
}

# Русские подписи глаголов для блока RECENT.
_VERB_LABELS = {
    "GO": "идти",
    "LOOK": "осмотреться",
    "TAKE": "взять",
    "DROP": "бросить",
    "TALK": "поговорить",
    "USE": "использовать",
}

_SYSTEM_TEMPLATE = """Ты — рассказчик текстовой приключенческой игры.
Роль: превращать сухие факты о мире в живую, атмосферную прозу.
Стиль: 2–4 предложения, второе лицо («вы»), мрачный постапокалиптический тон, без пафоса и клише.
Запреты:
- Не выдумывай сущности, события и детали, которых нет в данных этого хода.
- Не упоминай игровую механику: флаги, коды причин, служебные слова и числа, не указанные явно.
- Не меняй состояние мира и не предлагай действия за игрока.
- Не используй имена, которых нет в списке разрешённых.
Режим: {mode_instruction}"""

_TASK_TEXT = "Опиши это в 2–4 предложениях."


@dataclass
class _Block:
    text: str
    names: set
    numbers: set


def build(
    command: Command,
    outcome: Outcome,
    projection: ProjectionLookup,
    canon: Canon,
    recent_events: Optional[List[Event]],
    turn: int,
    config: Optional[PromptBuilderConfig] = None,
) -> tuple[str, Manifest]:
    """
    Собирает промпт для LLM и манифест для валидатора.

    Не вызывает LLM и не валидирует. Для UNKNOWN сессией не вызывается.
    """

    if config is None:
        config = PromptBuilderConfig()

    recent_events = recent_events or []

    mode = _MODE_LOOK if command.verb == "LOOK" else _MODE_DESCRIBE

    system_b = _build_system(mode)
    if _tokens(system_b.text) > config.system_budget:
        # Ошибка конфигурации, а не рантайма.
        raise BuildError("SYSTEM block exceeds system_budget")

    this_turn_b = _build_this_turn(command, outcome, canon, projection)
    task_b = _build_task()

    recent_pool = _filter_recent(recent_events)

    for recent_count, scene_level in _truncation_attempts(len(recent_pool), config):
        scene_b = _build_scene(command, outcome, projection, canon, scene_level)
        recent_b = _build_recent(recent_pool, canon, recent_count)

        prompt = _assemble(
            system_b.text,
            scene_b.text,
            recent_b.text,
            this_turn_b.text,
            task_b.text,
        )

        if _tokens(prompt) <= config.total_budget:
            names: set = set()
            numbers: set = set()
            for block in (system_b, scene_b, recent_b, this_turn_b, task_b):
                names |= block.names
                numbers |= block.numbers

            manifest = Manifest(
                allowed_names=names,
                allowed_numbers=numbers,
                mode=mode,
                verb=command.verb,
                reason_code=outcome.reason_code,
            )
            return prompt, manifest

    raise BuildError("Prompt exceeds total_budget after all truncations")


# ---------------------------------------------------------------------------
# Блоки
# ---------------------------------------------------------------------------


def _build_system(mode: str) -> _Block:
    text = _SYSTEM_TEMPLATE.format(mode_instruction=_MODE_INSTRUCTIONS[mode])
    return _Block(text, set(), set())


def _build_task() -> _Block:
    return _Block(_TASK_TEXT, set(), set())


def _build_scene(
    command: Command,
    outcome: Outcome,
    projection: ProjectionLookup,
    canon: Canon,
    level: int,
) -> _Block:
    """
    Блок SCENE.

    Уровни усечения (D-63):
      0 — полное описание, все сущности, инвентарь;
      1 — описание до первого предложения;
      2 — как 1, но только релевантные сущности;
      3 — как 2, но без инвентаря.
    """

    names: set = set()
    numbers: set = set()
    lines: list[str] = []

    loc_id = projection.current_location()
    loc = canon.locations.get(loc_id)

    loc_name = loc.name if loc else ""
    description = loc.description if loc else ""
    if level >= 1:
        description = _first_sentence(description)

    if loc_name:
        _add_name(names, loc_name)
        lines.append(f"Локация: {loc_name}")
    if description:
        lines.append(description)

    relevant_ids = None
    if level >= 2 and command.verb != "LOOK":
        relevant_ids = _command_entity_ids(command)

    # Кто здесь.
    npc_names = []
    for npc_id in projection.npcs_here():
        if relevant_ids is not None and npc_id not in relevant_ids:
            continue
        npc = canon.npcs.get(npc_id)
        if npc:
            npc_names.append(npc.name)
            _add_name(names, npc.name)

    # Что здесь лежит.
    item_strs = []
    for item_id, count in projection.items_here().items():
        if relevant_ids is not None and item_id not in relevant_ids:
            continue
        item = canon.items.get(item_id)
        if not item:
            continue
        if count > 1:
            item_strs.append(f"{item.name} ×{count}")
            numbers.add(count)
        else:
            item_strs.append(item.name)
        _add_name(names, item.name)

    # Цели применения здесь.
    target_names = []
    for ut_id in projection.use_targets_here():
        if relevant_ids is not None and ut_id not in relevant_ids:
            continue
        ut = canon.use_targets.get(ut_id)
        if ut:
            target_names.append(ut.name)
            _add_name(names, ut.name)

    entities = npc_names + item_strs + target_names
    if entities:
        lines.append("Здесь: " + ", ".join(entities) + ".")

    # Инвентарь (убирается на уровне 3).
    if level < 3:
        inv = projection.inventory()
        inv_strs = []
        for item_id, count in inv.items():
            item = canon.items.get(item_id)
            if not item:
                continue
            if count > 1:
                inv_strs.append(f"{item.name} ×{count}")
                numbers.add(count)
            else:
                inv_strs.append(item.name)
            _add_name(names, item.name)
        if inv_strs:
            lines.append("Инвентарь: " + ", ".join(inv_strs) + ".")

    extension = _scene_verb_extension(command, outcome, projection, canon, names, numbers)
    if extension:
        lines.append(extension)

    return _Block("\n".join(lines), names, numbers)


def _scene_verb_extension(
    command: Command,
    outcome: Outcome,
    projection: ProjectionLookup,
    canon: Canon,
    names: set,
    numbers: set,
) -> Optional[str]:
    verb = command.verb
    parts: list[str] = []

    if verb == "LOOK":
        return None

    if verb == "GO":
        dir_id = command.args.get("direction")
        if dir_id and dir_id in canon.directions:
            dname = canon.directions[dir_id].name
            _add_name(names, dname)
            parts.append(f"Направление: {dname}")
        if outcome.result == "ok" and dir_id:
            origin = _find_origin(canon, projection.current_location(), dir_id)
            if origin:
                _add_name(names, origin.name)
                parts.append(f"Откуда: {origin.name}")

    elif verb in ("TAKE", "DROP"):
        item_id = command.args.get("item")
        if item_id and item_id in canon.items:
            item = canon.items[item_id]
            _add_name(names, item.name)
            inv_count = projection.inventory().get(item_id, 0)
            parts.append(f"Предмет: {item.name} (в инвентаре: {inv_count})")
            if inv_count > 1:
                numbers.add(inv_count)

    elif verb == "TALK":
        npc_id = command.args.get("npc")
        if npc_id and npc_id in canon.npcs:
            npc = canon.npcs[npc_id]
            _add_name(names, npc.name)
            parts.append(f"Собеседник: {npc.name}")
        topic = command.args.get("topic")
        if topic:
            parts.append(f"Тема: {topic}")

    elif verb == "USE":
        item_id = command.args.get("item")
        if item_id and item_id in canon.items:
            item = canon.items[item_id]
            _add_name(names, item.name)
            parts.append(f"Предмет: {item.name}")
        target_id = command.args.get("target")
        target_name = _resolve_target_name(target_id, canon)
        if target_name:
            _add_name(names, target_name)
            parts.append(f"Цель: {target_name}")

    if parts:
        return "; ".join(parts)
    return None


def _build_this_turn(
    command: Command,
    outcome: Outcome,
    canon: Canon,
    projection: ProjectionLookup,
) -> _Block:
    names: set = set()
    numbers: set = set()
    lines: list[str] = []

    lines.append(f"Игрок написал: \"{command.raw}\"")

    reason_desc = _REASON_DESCRIPTIONS.get(outcome.reason_code)
    if reason_desc is None:
        reason_desc = "действие удалось" if outcome.result == "ok" else "действие не удалось"
    lines.append(f"Результат: {reason_desc}.")

    deltas = []
    for prim in outcome.applied:
        delta = _describe_primitive(prim, canon, projection, names, numbers)
        if delta:
            deltas.append(delta)
    if deltas:
        lines.append("Изменения: " + "; ".join(deltas) + ".")

    if outcome.response_text:
        lines.append(f"Текст из мира: \"{outcome.response_text}\"")

    return _Block("\n".join(lines), names, numbers)


def _build_recent(events: List[Event], canon: Canon, count: int) -> _Block:
    names: set = set()
    if count <= 0:
        return _Block("", names, set())

    selected = events[-count:]
    lines = []
    for ev in selected:
        line = _recent_line(ev, canon, names)
        if line:
            lines.append(line)
    return _Block("\n".join(lines), names, set())


def _recent_line(ev: Event, canon: Canon, names: set) -> Optional[str]:
    if ev.command is None:
        return None
    verb = ev.command.verb
    verb_label = _VERB_LABELS.get(verb, verb)

    entity_names = _command_entity_names(ev.command, canon, names)

    failed = ev.outcome is not None and ev.outcome.result == "fail"
    result_phrase = "не удалось" if failed else "получилось"

    line = f"Ход {ev.turn}: {verb_label}"
    if entity_names:
        line += " " + ", ".join(entity_names)
    line += f" — {result_phrase}."
    return line


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _assemble(system: str, scene: str, recent: str, this_turn: str, task: str) -> str:
    recent_body = recent if recent else "(нет предыдущих ходов)"
    return (
        "[SYSTEM]\n" + system + "\n\n"
        "[SCENE]\n" + scene + "\n\n"
        "[RECENT]\n" + recent_body + "\n\n"
        "[THIS TURN]\n" + this_turn + "\n\n"
        "[TASK]\n" + task
    )


def _filter_recent(events: List[Event]) -> List[Event]:
    result = []
    for ev in events:
        if ev is None:
            continue
        if ev.command is None or ev.outcome is None:
            continue
        if ev.command.verb == "UNKNOWN":
            continue
        result.append(ev)
    return result


def _truncation_attempts(pool_size: int, config: PromptBuilderConfig) -> list[tuple[int, int]]:
    """
    Порядок попыток: сначала уменьшаем RECENT до минимума,
    затем усечение SCENE по уровням (D-63).
    """

    max_k = min(pool_size, config.max_recent)
    min_k = min(config.min_recent, pool_size)

    attempts: list[tuple[int, int]] = []
    for k in range(max_k, min_k - 1, -1):
        attempts.append((k, 0))
    for scene_level in range(1, 4):
        attempts.append((min_k, scene_level))
    return attempts


def _describe_primitive(
    prim,
    canon: Canon,
    projection: ProjectionLookup,
    names: set,
    numbers: set,
) -> Optional[str]:
    """
    Перевод примитива в наблюдаемую дельту.

    Правило: если изменение нельзя увидеть из этой локации — его нет в промпте.
    `set_flag` скрыт всегда.
    """

    if prim.type == "set_flag":
        return None

    if prim.type == "add_to_inventory":
        item = canon.items.get(prim.item)
        if item:
            _add_name(names, item.name)
            return f"получен {item.name}"
        return None

    if prim.type == "remove_from_inventory":
        item = canon.items.get(prim.item)
        if item:
            _add_name(names, item.name)
            return f"отдан {item.name}"
        return None

    if prim.type == "add_to_location":
        if prim.to is not None and prim.to != projection.current_location():
            return None
        item = canon.items.get(prim.item)
        if item:
            _add_name(names, item.name)
            return f"{item.name} остаётся здесь"
        return None

    if prim.type == "remove_from_location":
        if prim.to is not None and prim.to != projection.current_location():
            return None
        item = canon.items.get(prim.item)
        if item:
            _add_name(names, item.name)
            return f"{item.name} исчезает отсюда"
        return None

    if prim.type == "change_location":
        loc = canon.locations.get(prim.to)
        if loc:
            _add_name(names, loc.name)
            return f"игрок переместился в {loc.name}"
        return None

    return None


def _command_entity_ids(command: Command) -> Optional[set]:
    ids = set()
    for key in ("item", "npc", "target"):
        value = command.args.get(key)
        if value:
            ids.add(value)
    return ids if ids else None


def _command_entity_names(command: Command, canon: Canon, names: set) -> list[str]:
    result = []
    item_id = command.args.get("item")
    if item_id and item_id in canon.items:
        name = canon.items[item_id].name
        _add_name(names, name)
        result.append(name)
    npc_id = command.args.get("npc")
    if npc_id and npc_id in canon.npcs:
        name = canon.npcs[npc_id].name
        _add_name(names, name)
        result.append(name)
    target_id = command.args.get("target")
    target_name = _resolve_target_name(target_id, canon)
    if target_name:
        _add_name(names, target_name)
        result.append(target_name)
    dir_id = command.args.get("direction")
    if dir_id and dir_id in canon.directions:
        name = canon.directions[dir_id].name
        _add_name(names, name)
        result.append(name)
    return result


def _resolve_target_name(target_id: Optional[str], canon: Canon) -> Optional[str]:
    if not target_id:
        return None
    if target_id in canon.use_targets:
        return canon.use_targets[target_id].name
    if target_id in canon.npcs:
        return canon.npcs[target_id].name
    if target_id in canon.items:
        return canon.items[target_id].name
    return None


def _find_origin(canon: Canon, current_loc_id: str, direction_id: str):
    for loc in canon.locations.values():
        for ex in loc.exits:
            if ex.direction == direction_id and ex.to == current_loc_id:
                return loc
    return None


def _first_sentence(text: str) -> str:
    text = text.strip()
    for i, ch in enumerate(text):
        if ch in ".!?":
            return text[: i + 1]
    return text


def _add_name(names: set, name: Optional[str]) -> None:
    if name:
        names.add(_normalize_name(name))


def _normalize_name(name: str) -> str:
    return name.lower().replace("ё", "е").strip()


def _tokens(text: str) -> float:
    return len(text) / 3