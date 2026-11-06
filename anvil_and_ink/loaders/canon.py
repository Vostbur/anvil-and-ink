import re
import yaml
from pathlib import Path
from typing import Any

from anvil_and_ink.models import Canon

# D-66: движок требует полный набор — загрузчик это гарантирует.
_ENGINE_CODES = frozenset({"ok", "unknown", "denied", "not_applicable"})
_VALID_ARGS = frozenset({"direction", "item", "npc", "topic", "target"})


class CanonError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _err(errors: list[str], path: str, msg: str) -> None:
    errors.append(f"{path}: {msg}")


def load_canon(path: str | Path) -> Canon:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise CanonError([f"YAML syntax error: {e}"])

    # ---------- Фаза 1. Форма (фатальная) ----------
    if not isinstance(raw, dict):
        raise CanonError(["Root YAML must be a mapping"])
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        raise CanonError(["meta section is missing or not a mapping"])
    if meta.get("schema_version") != 1:
        raise CanonError([f"meta.schema_version must be 1, got {meta.get('schema_version')!r}"])
    if not isinstance(meta.get("start_location"), str):
        raise CanonError(["meta.start_location must be a string"])

    required_sections = [
        "initial_state", "flags", "locations", "npcs", "items",
        "use_targets", "verbs", "directions", "reason_codes", "win_condition",
    ]
    for sec in required_sections:
        if sec not in raw:
            raise CanonError([f"Missing required section: {sec}"])
        if raw[sec] is None:
            raw[sec] = [] if sec != "initial_state" else {}

    # ---------- Фаза 2. Реестры ----------
    def index_list(section: str, key: str = "id") -> dict[str, dict]:
        index: dict[str, dict] = {}
        for i, item in enumerate(raw[section]):
            if not isinstance(item, dict) or key not in item:
                _err(errors, f"{section}[{i}]", f"missing {key}")
                continue
            item_id = item[key]
            if not isinstance(item_id, str) or not ID_RE.fullmatch(item_id):
                _err(errors, f"{section}[{i}].{key}", f"invalid id {item_id!r}")
                continue
            if item_id in index:
                _err(errors, f"{section}[{i}].{key}", f"duplicate id {item_id!r}")
                continue
            index[item_id] = item
        return index

    locations = index_list("locations")
    npcs = index_list("npcs")
    items = index_list("items")
    use_targets = index_list("use_targets")
    directions = index_list("directions")
    verbs = index_list("verbs")

    flags: dict[str, dict] = {}
    for f_id, f_def in raw["flags"].items():
        if not ID_RE.fullmatch(f_id):
            _err(errors, f"flags.{f_id}", "invalid flag id")
            continue
        if not isinstance(f_def, dict) or f_def.get("type") not in ("bool", "number"):
            _err(errors, f"flags.{f_id}", "type must be bool or number")
            continue
        if not f_def.get("description"):
            _err(errors, f"flags.{f_id}.description", "must be non-empty")
            continue
        flags[f_id] = f_def

    reason_codes: set[str] = set()
    for rc in raw["reason_codes"]:
        if not isinstance(rc, str) or not rc:
            _err(errors, "reason_codes", f"invalid entry {rc!r}")
            continue
        reason_codes.add(rc)
    missing_engine = _ENGINE_CODES - reason_codes
    if missing_engine:
        _err(errors, "reason_codes", f"missing codes required by engine: {sorted(missing_engine)}")

    # ---------- Фаза 3. Валидация реестров ----------
    init_flags = raw["initial_state"].get("flags", {})
    for f_id in flags:
        if f_id not in init_flags:
            _err(errors, f"initial_state.flags.{f_id}", "missing initial value")
    for f_id in init_flags:
        if f_id not in flags:
            _err(errors, f"initial_state.flags.{f_id}", "flag not declared in flags")

    for v_id, v_def in verbs.items():
        if not v_def.get("aliases"):
            _err(errors, f"verbs.{v_id}.aliases", "at least one alias required")
        for arg in v_def.get("args", []):
            if arg not in _VALID_ARGS:
                _err(errors, f"verbs.{v_id}.args", f"unknown arg {arg!r}")
        for arg in v_def.get("optional_args", []):
            if arg not in _VALID_ARGS:
                _err(errors, f"verbs.{v_id}.optional_args", f"unknown arg {arg!r}")

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
        for i, exit_ in enumerate(loc.get("exits", [])):
            path = f"locations[{loc_id}].exits[{i}]"
            if exit_.get("to") not in location_ids:
                _err(errors, f"{path}.to", f"unknown location {exit_.get('to')!r}")
            if exit_.get("direction") not in direction_ids:
                _err(errors, f"{path}.direction", f"unknown direction {exit_.get('direction')!r}")
        for i, ent in enumerate(loc.get("entities", [])):
            if ent not in item_type_ids and ent not in npc_ids:
                _err(errors, f"locations[{loc_id}].entities[{i}]",
                     f"entity {ent!r} is neither item type nor NPC")

    npc_occurrences = {npc_id: 0 for npc_id in npc_ids}
    for loc in locations.values():
        for ent in loc.get("entities", []):
            if ent in npc_ids:
                npc_occurrences[ent] += 1
    for npc_id, count in npc_occurrences.items():
        if count != 1:
            _err(errors, f"npcs.{npc_id}",
                 f"must appear in exactly one location, found {count}")

    for ut_id, ut in use_targets.items():
        exit_ref = ut.get("exit")
        if exit_ref:
            loc_id = exit_ref.get("location")
            dir_id = exit_ref.get("direction")
            if loc_id not in location_ids or dir_id not in direction_ids:
                _err(errors, f"use_targets.{ut_id}.exit", "invalid location or direction")
            else:
                exits = locations[loc_id].get("exits", [])
                if not any(e.get("direction") == dir_id for e in exits):
                    _err(errors, f"use_targets.{ut_id}.exit",
                         f"no exit {dir_id!r} in location {loc_id!r}")

    for ut_id, ut in use_targets.items():
        for i, rule in enumerate(ut.get("use", [])):
            target = rule.get("target")
            if target not in npc_ids | use_target_ids | item_type_ids:
                _err(errors, f"use_targets.{ut_id}.use[{i}].target",
                     f"unknown target {target!r}")

    def check_condition(cond: dict, path: str) -> None:
        ctype = cond.get("type")
        if ctype == "flag":
            if cond.get("flag") not in flags:
                _err(errors, f"{path}.flag", f"unknown flag {cond.get('flag')!r}")
        elif ctype == "inventory_count":
            if cond.get("item") not in item_type_ids:
                _err(errors, f"{path}.item", f"unknown item type {cond.get('item')!r}")
        else:
            _err(errors, path, f"unknown condition type {ctype!r}")

    def check_effect(eff: dict, path: str) -> None:
        etype = eff.get("type")
        if etype in ("set_flag",):
            if eff.get("flag") not in flags:
                _err(errors, f"{path}.flag", f"unknown flag {eff.get('flag')!r}")
        elif etype in ("add_to_inventory", "remove_from_inventory"):
            if eff.get("item") not in item_type_ids:
                _err(errors, f"{path}.item", f"unknown item type {eff.get('item')!r}")
        elif etype == "move":
            if eff.get("from") not in location_ids or eff.get("to") not in location_ids:
                _err(errors, path, "move from/to must be known locations")
        elif etype in ("win", "game_over"):
            pass
        else:
            _err(errors, path, f"unknown effect type {etype!r}")

    for loc_id, loc in locations.items():
        for i, exit_ in enumerate(loc.get("exits", [])):
            for j, cond in enumerate(exit_.get("condition", [])):
                check_condition(cond, f"locations[{loc_id}].exits[{i}].condition[{j}]")
            for j, eff in enumerate(exit_.get("effects", [])):
                check_effect(eff, f"locations[{loc_id}].exits[{i}].effects[{j}]")

    for ut_id, ut in use_targets.items():
        for i, rule in enumerate(ut.get("use", [])):
            for j, cond in enumerate(rule.get("condition", [])):
                check_condition(cond, f"use_targets[{ut_id}].use[{i}].condition[{j}]")
            for j, eff in enumerate(rule.get("effects", [])):
                check_effect(eff, f"use_targets[{ut_id}].use[{i}].effects[{j}]")

    for npc_id, npc in npcs.items():
        for i, rule in enumerate(npc.get("use", [])):
            for j, cond in enumerate(rule.get("condition", [])):
                check_condition(cond, f"npcs[{npc_id}].use[{i}].condition[{j}]")
            for j, eff in enumerate(rule.get("effects", [])):
                check_effect(eff, f"npcs[{npc_id}].use[{i}].effects[{j}]")
        for i, eff in enumerate(npc.get("effects", [])):
            check_effect(eff, f"npcs[{npc_id}].effects[{i}]")

    for i, cond in enumerate(raw["win_condition"]):
        check_condition(cond, f"win_condition[{i}]")

    # ---------- Фаза 5. Семантика ----------
    start = meta["start_location"]
    if start not in locations:
        _err(errors, "meta.start_location", f"unknown location {start!r}")
    else:
        reachable: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            for exit_ in locations[current].get("exits", []):
                to_loc = exit_.get("to")
                if to_loc in locations:
                    stack.append(to_loc)
        for loc_id in locations:
            if loc_id not in reachable:
                print(f"[WARN] Location {loc_id!r} is unreachable from start")

    set_true_flags: set[str] = set()
    for loc in locations.values():
        for exit_ in loc.get("exits", []):
            for eff in exit_.get("effects", []):
                if eff.get("type") == "set_flag" and eff.get("value") is True:
                    set_true_flags.add(eff["flag"])
    for ut in use_targets.values():
        for rule in ut.get("use", []):
            for eff in rule.get("effects", []):
                if eff.get("type") == "set_flag" and eff.get("value") is True:
                    set_true_flags.add(eff["flag"])
    for npc in npcs.values():
        for eff in npc.get("effects", []):
            if eff.get("type") == "set_flag" and eff.get("value") is True:
                set_true_flags.add(eff["flag"])

    for loc_id, loc in locations.items():
        for i, exit_ in enumerate(loc.get("exits", [])):
            for j, cond in enumerate(exit_.get("condition", [])):
                if cond.get("type") == "flag" and cond.get("equals") is True:
                    if cond["flag"] not in set_true_flags:
                        _err(errors,
                             f"locations[{loc_id}].exits[{i}].condition[{j}]",
                             f"flag {cond['flag']!r} is never set to true")

    for i, cond in enumerate(raw["win_condition"]):
        if cond.get("type") == "flag" and cond.get("equals") is True \
                and cond["flag"] not in set_true_flags:
            _err(errors, f"win_condition[{i}]",
                 f"win condition flag {cond['flag']!r} is never set to true")

    item_instances: set[str] = set()
    for loc in locations.values():
        for ent in loc.get("entities", []):
            if ent in item_type_ids:
                item_instances.add(ent)
    produced_items: set[str] = set()
    for ut in use_targets.values():
        for rule in ut.get("use", []):
            for eff in rule.get("effects", []):
                if eff.get("type") == "add_to_inventory":
                    produced_items.add(eff["item"])
    for npc in npcs.values():
        for eff in npc.get("effects", []):
            if eff.get("type") == "add_to_inventory":
                produced_items.add(eff["item"])
    for item_id in item_type_ids:
        if item_id not in item_instances and item_id not in produced_items:
            _err(errors, f"items.{item_id}",
                 "item type has no instance and no producer")

    # ---------- Фаза 6. Перекрёстная согласованность ----------
    for ut_id, ut in use_targets.items():
        exit_ref = ut.get("exit")
        if not exit_ref:
            continue
        loc_id = exit_ref["location"]
        dir_id = exit_ref["direction"]
        exits = locations[loc_id].get("exits", [])
        exit_obj = next((e for e in exits if e.get("direction") == dir_id), None)
        if not exit_obj:
            continue
        cond_flags: set[str] = set()
        for cond in exit_obj.get("condition", []):
            if cond.get("type") == "flag" and cond.get("equals") is True:
                cond_flags.add(cond["flag"])
        if not cond_flags:
            continue
        found = False
        for rule in ut.get("use", []):
            for eff in rule.get("effects", []):
                if eff.get("type") == "set_flag" and eff.get("value") is True \
                        and eff["flag"] in cond_flags:
                    found = True
                    break
            if found:
                break
        if not found:
            _err(errors, f"use_targets.{ut_id}",
                 "exit requires flag(s) that no USE rule sets")

    # Предупреждения, не ошибки
    all_names: list[str] = []
    for registry in (locations, npcs, items, use_targets):
        for entity in registry.values():
            if "name" in entity:
                all_names.append(entity["name"])
    seen: set[str] = set()
    for name in all_names:
        if name in seen:
            print(f"[WARN] Duplicate entity name: {name!r}")
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