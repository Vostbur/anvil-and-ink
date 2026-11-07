"""Загрузчик канона. specs/loaders.md.

Шесть фаз, порядок жёсткий. Ошибки собираются до конца; при наличии хотя бы
одной — CanonError(errors) с путями в файле. Фатальные (YAML, schema_version,
отсутствие секции) прерывают работу сразу.
"""

import re
import yaml
from pathlib import Path
from typing import Any

from anvil_and_ink.models import Canon


class CanonError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VALID_ARGS = frozenset({"direction", "item", "npc", "topic", "target"})
_VALID_CONDITION_TYPES = frozenset({"flag", "inventory_count"})
_VALID_EFFECT_TYPES = frozenset({
    "set_flag", "add_to_inventory", "remove_from_inventory",
    # внутренние, но в каноне могут не встречаться — допустимы
    "add_to_location", "remove_from_location", "change_location",
})


def _err(errors: list[str], path: str, msg: str) -> None:
    errors.append(f"{path}: {msg}")


def load_canon(path: str | Path) -> Canon:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise CanonError([f"YAML syntax error: {e}"])

    # ---------- Фаза 1. Разбор и форма (фатальная) ----------
    if not isinstance(raw, dict):
        raise CanonError(["root: must be a mapping"])
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        raise CanonError(["meta: missing or not a mapping"])
    if meta.get("schema_version") != 1:
        raise CanonError([f"meta.schema_version: must be 1, got {meta.get('schema_version')!r}"])
    if not isinstance(meta.get("start_location"), str):
        raise CanonError(["meta.start_location: must be a string"])

    required_sections = [
        "initial_state", "flags", "locations", "npcs", "items",
        "use_targets", "verbs", "directions", "reason_codes", "win_condition",
    ]
    for sec in required_sections:
        if sec not in raw:
            raise CanonError([f"root: missing required section {sec!r}"])
        if raw[sec] is None:
            raw[sec] = {} if sec in ("initial_state", "flags", "verbs", "reason_codes") else []

    # ---------- Фаза 2. Реестры и уникальность id ----------

    def index_list(section: str) -> dict[str, dict]:
        """locations, npcs, items, use_targets, directions — списки [{id: ...}]."""
        out: dict[str, dict] = {}
        for i, item in enumerate(raw[section]):
            if not isinstance(item, dict) or "id" not in item:
                _err(errors, f"{section}[{i}]", "missing id")
                continue
            item_id = item["id"]
            if not isinstance(item_id, str) or not ID_RE.fullmatch(item_id):
                _err(errors, f"{section}[{i}].id", f"invalid id {item_id!r}")
                continue
            if item_id in out:
                _err(errors, f"{section}[{i}].id", f"duplicate id {item_id!r}")
                continue
            out[item_id] = item
        return out

    locations = index_list("locations")
    npcs = index_list("npcs")
    items = index_list("items")
    use_targets = index_list("use_targets")
    directions = index_list("directions")

    # verbs — dict {GO: {...}}; id не snake_case, а имя глагола в UPPERCASE
    verbs: dict[str, dict] = {}
    if not isinstance(raw["verbs"], dict):
        _err(errors, "verbs", "must be a mapping")
        raw["verbs"] = {}
    for verb_name, verb_def in raw["verbs"].items():
        if not isinstance(verb_name, str) or not verb_name:
            _err(errors, "verbs", f"invalid verb name {verb_name!r}")
            continue
        if not isinstance(verb_def, dict):
            _err(errors, f"verbs.{verb_name}", "must be a mapping")
            continue
        verbs[verb_name] = verb_def

    # flags — dict
    flags: dict[str, dict] = {}
    if not isinstance(raw["flags"], dict):
        _err(errors, "flags", "must be a mapping")
        raw["flags"] = {}
    for f_id, f_def in raw["flags"].items():
        if not isinstance(f_id, str) or not ID_RE.fullmatch(f_id):
            _err(errors, f"flags.{f_id}", "invalid flag id")
            continue
        if not isinstance(f_def, dict) or f_def.get("type") not in ("bool", "number"):
            _err(errors, f"flags.{f_id}.type", "must be bool or number")
            continue
        if not f_def.get("description"):
            _err(errors, f"flags.{f_id}.description", "must be non-empty")
            continue
        flags[f_id] = f_def

    # reason_codes — dict {ok: "...", no_exit: "...", ...}
    reason_codes: set[str] = set()
    if not isinstance(raw["reason_codes"], dict):
        _err(errors, "reason_codes", "must be a mapping {code: description}")
        raw["reason_codes"] = {}
    for code, desc in raw["reason_codes"].items():
        if not isinstance(code, str) or not ID_RE.fullmatch(code):
            _err(errors, f"reason_codes.{code}", "invalid code id")
            continue
        if not isinstance(desc, str) or not desc.strip():
            _err(errors, f"reason_codes.{code}", "description must be non-empty")
            continue
        reason_codes.add(code)
    # D-1xx: только ok и unknown обязательны
    for required in ("ok", "unknown"):
        if required not in reason_codes:
            _err(errors, "reason_codes", f"missing required code {required!r}")

    # topics: уникальность внутри NPC
    for npc_id, npc in npcs.items():
        topics = npc.get("topics", []) or []
        keys: set[str] = set()
        for i, topic in enumerate(topics):
            if not isinstance(topic, dict) or "key" not in topic:
                _err(errors, f"npcs.{npc_id}.topics[{i}]", "missing key")
                continue
            key = topic["key"]
            if not isinstance(key, str) or not key:
                _err(errors, f"npcs.{npc_id}.topics[{i}].key", "must be non-empty string")
                continue
            if key in keys:
                _err(errors, f"npcs.{npc_id}.topics[{i}].key", f"duplicate topic {key!r}")
                continue
            keys.add(key)

    # ---------- Фаза 3. Валидация реестров ----------
    init_flags = raw["initial_state"].get("flags", {}) or {}
    for f_id in flags:
        if f_id not in init_flags:
            _err(errors, f"initial_state.flags.{f_id}", "missing initial value")
    for f_id in init_flags:
        if f_id not in flags:
            _err(errors, f"initial_state.flags.{f_id}", "not declared in flags")

    for v_name, v_def in verbs.items():
        aliases = v_def.get("aliases", [])
        if not isinstance(aliases, list) or not aliases:
            _err(errors, f"verbs.{v_name}.aliases", "at least one alias required")
        for arg in v_def.get("args", []):
            if arg not in _VALID_ARGS:
                _err(errors, f"verbs.{v_name}.args", f"unknown arg {arg!r}")

    # Алиасы глаголов не пересекаются
    alias_owner: dict[str, str] = {}
    for v_name in sorted(verbs):
        for alias in verbs[v_name].get("aliases", []) or []:
            if not isinstance(alias, str) or not alias.strip():
                _err(errors, f"verbs.{v_name}.aliases", f"invalid alias {alias!r}")
                continue
            key = alias.lower().replace("ё", "е")
            if key in alias_owner and alias_owner[key] != v_name:
                _err(errors, f"verbs.{v_name}.aliases",
                     f"alias {alias!r} already used by {alias_owner[key]!r}")
            else:
                alias_owner[key] = v_name

    for d_id, d_def in directions.items():
        if not d_def.get("name"):
            _err(errors, f"directions.{d_id}.name", "must be non-empty")
        if not d_def.get("aliases"):
            _err(errors, f"directions.{d_id}.aliases", "at least one alias required")

    for ut_id, ut_def in use_targets.items():
        if not ut_def.get("name"):
            _err(errors, f"use_targets.{ut_id}.name", "must be non-empty")
        if not ut_def.get("aliases"):
            _err(errors, f"use_targets.{ut_id}.aliases", "at least one alias required")

    # ---------- Фаза 4. Ссылочная целостность ----------
    location_ids = set(locations)
    direction_ids = set(directions)
    npc_ids = set(npcs)
    item_type_ids = set(items)
    use_target_ids = set(use_targets)

    for loc_id, loc in locations.items():
        for i, exit_ in enumerate(loc.get("exits", []) or []):
            path = f"locations[id={loc_id}].exits[{i}]"
            to_ = exit_.get("to")
            if to_ not in location_ids:
                _err(errors, f"{path}.to", f"unknown location {to_!r}")
            dir_ = exit_.get("direction")
            if dir_ not in direction_ids:
                _err(errors, f"{path}.direction", f"unknown direction {dir_!r}")
        for i, ent in enumerate(loc.get("entities", []) or []):
            if ent not in item_type_ids and ent not in npc_ids:
                _err(errors, f"locations[id={loc_id}].entities[{i}]",
                     f"entity {ent!r} is neither item type nor NPC")

    # NPC ровно в одной локации
    npc_occurrences: dict[str, int] = {npc_id: 0 for npc_id in npc_ids}
    for loc in locations.values():
        for ent in loc.get("entities", []) or []:
            if ent in npc_ids:
                npc_occurrences[ent] += 1
    for npc_id, count in npc_occurrences.items():
        if count != 1:
            _err(errors, f"npcs[id={npc_id}]",
                 f"must appear in exactly one location via entities, found {count}")

    # use_target.exit
    for ut_id, ut in use_targets.items():
        exit_ref = ut.get("exit")
        if exit_ref:
            loc_id = exit_ref.get("location")
            dir_id = exit_ref.get("direction")
            if loc_id not in location_ids or dir_id not in direction_ids:
                _err(errors, f"use_targets[id={ut_id}].exit",
                     "invalid location or direction")
            else:
                exits = locations[loc_id].get("exits", []) or []
                if not any(e.get("direction") == dir_id for e in exits):
                    _err(errors, f"use_targets[id={ut_id}].exit",
                         f"no exit {dir_id!r} in location {loc_id!r}")

    def check_condition(cond: dict, path: str) -> None:
        ctype = cond.get("type")
        if ctype not in _VALID_CONDITION_TYPES:
            _err(errors, path, f"unknown condition type {ctype!r}")
            return
        if ctype == "flag":
            if cond.get("flag") not in flags:
                _err(errors, f"{path}.flag", f"unknown flag {cond.get('flag')!r}")
        elif ctype == "inventory_count":
            if cond.get("item") not in item_type_ids:
                _err(errors, f"{path}.item", f"unknown item type {cond.get('item')!r}")
            if "min" not in cond:
                _err(errors, path, "missing min")
            elif not isinstance(cond["min"], int) or cond["min"] < 0:
                _err(errors, f"{path}.min", "must be non-negative integer")

    def check_effect(eff: dict, path: str) -> None:
        etype = eff.get("type")
        if etype not in _VALID_EFFECT_TYPES:
            _err(errors, path, f"unknown effect type {etype!r}")
            return
        if etype == "set_flag":
            if eff.get("flag") not in flags:
                _err(errors, f"{path}.flag", f"unknown flag {eff.get('flag')!r}")
        elif etype in ("add_to_inventory", "remove_from_inventory"):
            if eff.get("item") not in item_type_ids:
                _err(errors, f"{path}.item", f"unknown item type {eff.get('item')!r}")
            cnt = eff.get("count", 1)
            if not isinstance(cnt, int) or cnt <= 0:
                _err(errors, f"{path}.count", "must be positive integer")

    # Условия и эффекты во всех местах канона
    for loc_id, loc in locations.items():
        for i, exit_ in enumerate(loc.get("exits", []) or []):
            for j, cond in enumerate(exit_.get("condition", []) or []):
                check_condition(cond, f"locations[id={loc_id}].exits[{i}].condition[{j}]")
            for j, eff in enumerate(exit_.get("effects", []) or []):
                check_effect(eff, f"locations[id={loc_id}].exits[{i}].effects[{j}]")

    for ut_id, ut in use_targets.items():
        for i, rule in enumerate(ut.get("use", []) or []):
            for j, cond in enumerate(rule.get("condition", []) or []):
                check_condition(cond, f"use_targets[id={ut_id}].use[{i}].condition[{j}]")
            for j, eff in enumerate(rule.get("effects", []) or []):
                check_effect(eff, f"use_targets[id={ut_id}].use[{i}].effects[{j}]")

    for npc_id, npc in npcs.items():
        for i, topic in enumerate(npc.get("topics", []) or []):
            for j, cond in enumerate(topic.get("condition", []) or []):
                check_condition(cond, f"npcs[id={npc_id}].topics[{i}].condition[{j}]")
            for j, eff in enumerate(topic.get("effects", []) or []):
                check_effect(eff, f"npcs[id={npc_id}].topics[{i}].effects[{j}]")
        for i, eff in enumerate(npc.get("effects", []) or []):
            check_effect(eff, f"npcs[id={npc_id}].effects[{i}]")

    for item_id, item in items.items():
        for i, rule in enumerate(item.get("use", []) or []):
            for j, cond in enumerate(rule.get("condition", []) or []):
                check_condition(cond, f"items[id={item_id}].use[{i}].condition[{j}]")
            for j, eff in enumerate(rule.get("effects", []) or []):
                check_effect(eff, f"items[id={item_id}].use[{i}].effects[{j}]")
            target = rule.get("target")
            if target not in npc_ids | use_target_ids | item_type_ids:
                _err(errors, f"items[id={item_id}].use[{i}].target",
                     f"unknown target {target!r}")

    for i, cond in enumerate(raw["win_condition"]):
        check_condition(cond, f"win_condition[{i}]")

    # ---------- Фаза 5. Семантика ----------

    # Достижимость локаций (без условий)
    start = meta["start_location"]
    if start not in locations:
        _err(errors, "meta.start_location", f"unknown location {start!r}")
    else:
        reachable: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            for exit_ in locations[cur].get("exits", []) or []:
                to_ = exit_.get("to")
                if to_ in locations:
                    stack.append(to_)
        for loc_id in locations:
            if loc_id not in reachable:
                print(f"[WARN] locations[id={loc_id}]: unreachable from start")

    # Открываемость замков: собрать все флаги, устанавливаемые в true
    set_true_flags: set[str] = set()

    def collect_from_effects(effects: list[dict]) -> None:
        for eff in effects:
            if eff.get("type") == "set_flag" and eff.get("value") is True:
                set_true_flags.add(eff["flag"])

    for loc in locations.values():
        for exit_ in loc.get("exits", []) or []:
            collect_from_effects(exit_.get("effects", []) or [])
    for ut in use_targets.values():
        for rule in ut.get("use", []) or []:
            collect_from_effects(rule.get("effects", []) or [])
    for npc in npcs.values():
        collect_from_effects(npc.get("effects", []) or [])
        for topic in npc.get("topics", []) or []:
            collect_from_effects(topic.get("effects", []) or [])
    for item in items.values():
        for rule in item.get("use", []) or []:
            collect_from_effects(rule.get("effects", []) or [])

    # Условия на выходах: flag == true должен кем-то ставиться
    for loc_id, loc in locations.items():
        for i, exit_ in enumerate(loc.get("exits", []) or []):
            for j, cond in enumerate(exit_.get("condition", []) or []):
                if cond.get("type") == "flag" \
                        and cond.get("equals", True) is True \
                        and cond["flag"] not in set_true_flags:
                    _err(errors,
                         f"locations[id={loc_id}].exits[{i}].condition[{j}]",
                         f"flag {cond['flag']!r} is never set to true")

    # win_condition: flag должен кем-то ставиться
    for i, cond in enumerate(raw["win_condition"]):
        if cond.get("type") == "flag" \
                and cond.get("equals", True) is True \
                and cond["flag"] not in set_true_flags:
            _err(errors, f"win_condition[{i}]",
                 f"flag {cond['flag']!r} is never set to true")

    # Живость типов предметов
    item_instances: set[str] = set()
    for loc in locations.values():
        for ent in loc.get("entities", []) or []:
            if ent in item_type_ids:
                item_instances.add(ent)

    produced_items: set[str] = set()

    def collect_produced(effects: list[dict]) -> None:
        for eff in effects:
            if eff.get("type") == "add_to_inventory":
                produced_items.add(eff["item"])

    for loc in locations.values():
        for exit_ in loc.get("exits", []) or []:
            collect_produced(exit_.get("effects", []) or [])
    for ut in use_targets.values():
        for rule in ut.get("use", []) or []:
            collect_produced(rule.get("effects", []) or [])
    for npc in npcs.values():
        collect_produced(npc.get("effects", []) or [])
        for topic in npc.get("topics", []) or []:
            collect_produced(topic.get("effects", []) or [])
    for item in items.values():
        for rule in item.get("use", []) or []:
            collect_produced(rule.get("effects", []) or [])

    for item_id in item_type_ids:
        if item_id not in item_instances and item_id not in produced_items:
            _err(errors, f"items[id={item_id}]",
                 "item type has no instance in any location and no producer")

    # ---------- Фаза 6. Перекрёстная согласованность ----------

    # Для каждого use_target с exit: хотя бы одно USE-правило на этот use_target
    # (в items[*].use) ставит флаг из условия выхода.
    for ut_id, ut in use_targets.items():
        exit_ref = ut.get("exit")
        if not exit_ref:
            continue
        loc_id = exit_ref["location"]
        dir_id = exit_ref["direction"]
        exits = locations[loc_id].get("exits", []) or []
        exit_obj = next((e for e in exits if e.get("direction") == dir_id), None)
        if not exit_obj:
            continue
        cond_flags: set[str] = set()
        for cond in exit_obj.get("condition", []) or []:
            if cond.get("type") == "flag" and cond.get("equals", True) is True:
                cond_flags.add(cond["flag"])
        if not cond_flags:
            continue
        found = False
        for item in items.values():
            for rule in item.get("use", []) or []:
                if rule.get("target") != ut_id:
                    continue
                for eff in rule.get("effects", []) or []:
                    if eff.get("type") == "set_flag" \
                            and eff.get("value") is True \
                            and eff["flag"] in cond_flags:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if not found:
            _err(errors, f"use_targets[id={ut_id}]",
                 "exit requires flag(s) that no items[*].use rule sets")

    # Уникальность имён (warning)
    all_names: list[str] = []
    for registry in (locations, npcs, items, use_targets):
        for entity in registry.values():
            if "name" in entity:
                all_names.append(entity["name"])
    seen: set[str] = set()
    for name in all_names:
        if name in seen:
            print(f"[WARN] duplicate entity name: {name!r}")
        seen.add(name)

    if errors:
        raise CanonError(errors)

    return Canon(
        meta=meta,
        initial_state=raw["initial_state"],
        flags=flags,
        locations=locations,
        npcs=npcs,
        items=items,
        use_targets=use_targets,
        verbs=verbs,
        directions=directions,
        reason_codes=reason_codes,
        win_condition=raw["win_condition"],
    )