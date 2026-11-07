from __future__ import annotations

from typing import Sequence

from .models import Canon, Command, Outcome, Primitive
from .projection import ProjectionLookup


class EngineError(Exception):
    """Ошибка движка: баг канона или нарушение закрытого набора."""


_KNOWN_APPLIED_TYPES = {
    "set_flag",
    "add_to_inventory",
    "remove_from_inventory",
    "add_to_location",
    "remove_from_location",
    "change_location",
}


def apply_command(
    command: Command,
    projection: ProjectionLookup,
    canon: Canon,
) -> Outcome:
    """
    Чистая функция движка.

    (Command, ProjectionLookup, Canon) -> Outcome.
    Не применяет изменения сам: применит сессия через projection.apply.
    """

    verb = command.verb

    if verb == "UNKNOWN":
        return _fail("unknown")

    if verb == "LOOK":
        return _ok(tuple(), response_text=None)

    if verb == "GO":
        return _go(command, projection, canon)

    if verb == "TAKE":
        return _take(command, projection, canon)

    if verb == "DROP":
        return _drop(command, projection, canon)

    if verb == "TALK":
        return _talk(command, projection, canon)

    if verb == "USE":
        return _use(command, projection, canon)

    # Парсер должен возвращать известный глагол или UNKNOWN.
    raise EngineError(f"Unsupported verb: {verb!r}")


# ---------------------------------------------------------------------------
# Verb handlers
# ---------------------------------------------------------------------------


def _go(command: Command, projection: ProjectionLookup, canon: Canon) -> Outcome:
    # 1. Разрешение.
    if not _resolved(command, "direction"):
        return _fail("no_exit")

    direction_id = command.args["direction"]
    current_loc_id, current_loc = _current_location(projection, canon)

    # 2. Наличие выхода.
    exit_obj = next(
        (e for e in current_loc.exits if e.direction == direction_id),
        None,
    )
    if exit_obj is None:
        return _fail("no_exit")

    # 3. Условие выхода.
    if exit_obj.condition and not _conditions_met(exit_obj.condition, projection):
        return _fail("locked")

    applied = _canonical_effects_to_primitives(exit_obj.effects)
    applied = applied + (
        Primitive(type="change_location", to=exit_obj.to),
    )

    _validate_applied(applied, projection, canon)
    return _ok(applied)


def _take(command: Command, projection: ProjectionLookup, canon: Canon) -> Outcome:
    # 1. Разрешение.
    if not _resolved(command, "item"):
        return _fail("no_item_here")

    item_id = command.args["item"]
    item = canon.items.get(item_id)
    if item is None:
        return _fail("no_item_here")

    current_loc_id, _ = _current_location(projection, canon)

    # 2. Наличие предмета в локации.
    if projection.items_here(current_loc_id).get(item_id, 0) < 1:
        return _fail("no_item_here")

    # 3. takeable.
    if not item.takeable:
        return _fail("not_takeable")

    applied = (
        Primitive(
            type="remove_from_location",
            item=item_id,
            count=1,
            to=current_loc_id,
        ),
        Primitive(
            type="add_to_inventory",
            item=item_id,
            count=1,
        ),
    )

    _validate_applied(applied, projection, canon)
    return _ok(applied)


def _drop(command: Command, projection: ProjectionLookup, canon: Canon) -> Outcome:
    # 1. Разрешение.
    if not _resolved(command, "item"):
        return _fail("not_in_inventory")

    item_id = command.args["item"]
    item = canon.items.get(item_id)
    if item is None:
        return _fail("not_in_inventory")

    # 2. Наличие предмета в инвентаре.
    if not projection.has_item(item_id, min_count=1):
        return _fail("not_in_inventory")

    current_loc_id, _ = _current_location(projection, canon)

    # D-18: DROP не проверяет takeable.
    applied = (
        Primitive(
            type="remove_from_inventory",
            item=item_id,
            count=1,
        ),
        Primitive(
            type="add_to_location",
            item=item_id,
            count=1,
            to=current_loc_id,
        ),
    )

    _validate_applied(applied, projection, canon)
    return _ok(applied)


def _talk(command: Command, projection: ProjectionLookup, canon: Canon) -> Outcome:
    # 1. Разрешение NPC.
    if not _resolved(command, "npc"):
        return _fail("no_npc_here")

    npc_id = command.args["npc"]
    npc = canon.npcs.get(npc_id)
    if npc is None:
        return _fail("no_npc_here")

    current_loc_id, _ = _current_location(projection, canon)

    # 2. Наличие NPC в текущей локации.
    if npc_id not in projection.npcs_here(current_loc_id):
        return _fail("no_npc_here")

    # 3. Разрешение темы.
    if not _resolved(command, "topic"):
        return _fail("no_topic")

    topic_key = command.args["topic"]
    topic = _topic_by_key(npc, topic_key)
    if topic is None:
        return _fail("no_topic")

    # 4. Условие темы.
    if topic.condition and not _conditions_met(topic.condition, projection):
        return _fail("condition_not_met", response_text=topic.response_on_fail)

    applied = _canonical_effects_to_primitives(topic.effects)

    # D-62: информационный TALK без эффектов может иметь applied=[].
    _validate_applied(applied, projection, canon)
    return _ok(applied, response_text=topic.response)


def _use(command: Command, projection: ProjectionLookup, canon: Canon) -> Outcome:
    # 1. Разрешение предмета.
    if not _resolved(command, "item"):
        return _fail("not_in_inventory")

    item_id = command.args["item"]
    item = canon.items.get(item_id)
    if item is None:
        return _fail("not_in_inventory")

    # 2. Наличие предмета в инвентаре.
    if not projection.has_item(item_id, min_count=1):
        return _fail("not_in_inventory")

    # 3. Разрешение цели.
    if not _resolved(command, "target"):
        return _fail("no_target")

    target_id = command.args["target"]
    current_loc_id, _ = _current_location(projection, canon)

    # 4. Наличие/тип цели.
    if target_id in canon.use_targets:
        use_target = canon.use_targets[target_id]

        if use_target.exit is None:
            # В MVP таких целей нет. Если появятся — нужно отдельное решение.
            raise EngineError(
                f"use_target '{target_id}' has no exit; not supported in MVP"
            )

        exit_location, _exit_direction = use_target.exit
        if exit_location != current_loc_id:
            return _fail("target_not_here")

    elif target_id in canon.npcs:
        if target_id not in projection.npcs_here(current_loc_id):
            return _fail("no_npc_here")

    elif target_id in canon.items:
        # USE «предмет на предмет» в MVP не поддерживается.
        return _fail("invalid_combination")

    else:
        return _fail("no_target")

    # 5. Наличие USE-правила для пары (предмет, цель).
    rule = _use_rule(item, target_id)
    if rule is None:
        return _fail("invalid_combination")

    # 6. Условие правила.
    if rule.condition and not _conditions_met(rule.condition, projection):
        return _fail("condition_not_met", response_text=rule.response_on_fail)

    applied = _canonical_effects_to_primitives(rule.effects)

    _validate_applied(applied, projection, canon)
    return _ok(applied, response_text=rule.response)


# ---------------------------------------------------------------------------
# Outcome constructors
# ---------------------------------------------------------------------------


def _fail(reason_code: str, response_text: str | None = None) -> Outcome:
    return Outcome(
        result="fail",
        reason_code=reason_code,
        applied=tuple(),
        response_text=response_text,
        seed=None,
    )


def _ok(
    applied: Sequence[Primitive],
    response_text: str | None = None,
) -> Outcome:
    return Outcome(
        result="ok",
        reason_code="ok",
        applied=tuple(applied),
        response_text=response_text,
        seed=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolved(command: Command, arg_name: str) -> bool:
    return (
        bool(command.resolved.get(arg_name, False))
        and command.args.get(arg_name) is not None
    )


def _current_location(
    projection: ProjectionLookup,
    canon: Canon,
) -> tuple[str, object]:
    loc_id = projection.current_location()
    loc = canon.locations.get(loc_id)
    if loc is None:
        raise EngineError(f"Current location '{loc_id}' is not in canon")
    return loc_id, loc


def _topic_by_key(npc, topic_key: str):
    return next((t for t in npc.topics if t.key == topic_key), None)


def _use_rule(item, target_id: str):
    return next((r for r in item.use if r.target == target_id), None)


def _conditions_met(conditions, projection: ProjectionLookup) -> bool:
    """
    Закрытый набор условий:
      - flag
      - inventory_count

    Внутри блока — конъюнкция.
    """

    for condition in conditions:
        if condition.type == "flag":
            if condition.flag is None:
                raise EngineError("flag condition requires 'flag'")

            if projection.flag(condition.flag) != condition.equals:
                return False

        elif condition.type == "inventory_count":
            if condition.item is None:
                raise EngineError("inventory_count condition requires 'item'")

            min_count = condition.min if condition.min is not None else 1
            if not projection.has_item(condition.item, min_count=min_count):
                return False

        else:
            raise EngineError(f"Unknown condition type: {condition.type!r}")

    return True


def _canonical_effects_to_primitives(effects) -> tuple[Primitive, ...]:
    """
    Закрытый набор канонических эффектов:
      - set_flag
      - add_to_inventory
      - remove_from_inventory

    Внутренние операции движка добавляются отдельно в обработчиках глаголов.
    """

    primitives: list[Primitive] = []

    for effect in effects:
        if effect.type not in {
            "set_flag",
            "add_to_inventory",
            "remove_from_inventory",
        }:
            raise EngineError(f"Unknown canonical effect type: {effect.type!r}")

        count = effect.count

        if effect.type in {"add_to_inventory", "remove_from_inventory"}:
            if count is None:
                count = 1
            if count <= 0:
                raise EngineError(
                    f"Effect '{effect.type}' must have positive count, got {count!r}"
                )

        primitives.append(
            Primitive(
                type=effect.type,
                flag=effect.flag,
                value=effect.value,
                item=effect.item,
                count=count,
                to=None,
            )
        )

    return tuple(primitives)


def _validate_applied(
    applied: Sequence[Primitive],
    projection: ProjectionLookup,
    canon: Canon,
) -> None:
    """
    Проверка атомарного применения без реального изменения состояния.

    Движок падает с внятной ошибкой, если канон пытается списать больше,
    чем есть. Это баг канона, не тихая деградация.
    """

    inventory = dict(projection.inventory())
    placement_cache: dict[str, dict[str, int]] = {}

    def loc_items(loc_id: str) -> dict[str, int]:
        if loc_id not in placement_cache:
            placement_cache[loc_id] = dict(projection.items_here(loc_id))
        return placement_cache[loc_id]

    for primitive in applied:
        if primitive.type not in _KNOWN_APPLIED_TYPES:
            raise EngineError(f"Unknown applied primitive type: {primitive.type!r}")

        if primitive.type == "set_flag":
            if primitive.flag is None or primitive.flag not in canon.flags:
                raise EngineError(f"set_flag references unknown flag: {primitive.flag!r}")

        elif primitive.type in {"add_to_inventory", "remove_from_inventory"}:
            if primitive.item is None or primitive.item not in canon.items:
                raise EngineError(
                    f"{primitive.type} references unknown item: {primitive.item!r}"
                )

            count = primitive.count if primitive.count is not None else 1
            if count <= 0:
                raise EngineError(
                    f"{primitive.type} must have positive count, got {count!r}"
                )

            have = inventory.get(primitive.item, 0)

            if primitive.type == "add_to_inventory":
                inventory[primitive.item] = have + count
            else:
                if have < count:
                    raise EngineError(
                        f"Cannot remove {count} of '{primitive.item}' "
                        f"from inventory; only {have} available"
                    )

                have -= count
                if have == 0:
                    inventory.pop(primitive.item, None)
                else:
                    inventory[primitive.item] = have

        elif primitive.type in {"add_to_location", "remove_from_location"}:
            if primitive.to is None or primitive.to not in canon.locations:
                raise EngineError(
                    f"{primitive.type} references unknown location: {primitive.to!r}"
                )

            if primitive.item is None or primitive.item not in canon.items:
                raise EngineError(
                    f"{primitive.type} references unknown item: {primitive.item!r}"
                )

            count = primitive.count if primitive.count is not None else 1
            if count <= 0:
                raise EngineError(
                    f"{primitive.type} must have positive count, got {count!r}"
                )

            items = loc_items(primitive.to)
            have = items.get(primitive.item, 0)

            if primitive.type == "add_to_location":
                items[primitive.item] = have + count
            else:
                if have < count:
                    raise EngineError(
                        f"Cannot remove {count} of '{primitive.item}' "
                        f"from location '{primitive.to}'; only {have} available"
                    )

                have -= count
                if have == 0:
                    items.pop(primitive.item, None)
                else:
                    items[primitive.item] = have

        elif primitive.type == "change_location":
            if primitive.to is None or primitive.to not in canon.locations:
                raise EngineError(
                    f"change_location references unknown location: {primitive.to!r}"
                )