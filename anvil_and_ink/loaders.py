import re
import yaml
from typing import Any, Set
from .models import (
    Canon, CanonError, Templates, TemplatesError,
    Meta, InitialState, Flag, Location, Exit, Npc, Topic,
    Item, UseRule, UseTarget, Verb, Direction, WinCondition,
    Condition, Effect
)

def load_canon(path: str) -> Canon:
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise CanonError([f"YAML parse error: {e}"])

    errors = []
    if not isinstance(data, dict):
        raise CanonError(["Root must be a mapping"])
    
    required_sections = [
        "meta", "initial_state", "flags", "locations", "npcs",
        "items", "use_targets", "verbs", "directions", "reason_codes", "win_condition"
    ]
    for sec in required_sections:
        if sec not in data:
            errors.append(f"Missing required section: {sec}")
    if errors: raise CanonError(errors)

    meta_raw = data["meta"]
    if not isinstance(meta_raw, dict) or meta_raw.get("schema_version") != 1:
        errors.append("meta.schema_version must be 1")
    if not meta_raw.get("start_location"):
        errors.append("meta.start_location is missing")
    if errors: raise CanonError(errors)

    locs_raw = data["locations"]
    npcs_raw = data["npcs"]
    items_raw = data["items"]
    flags_raw = data["flags"]
    verbs_raw = data["verbs"]
    directions_raw = data["directions"]
    use_targets_raw = data["use_targets"]
    reason_codes_raw = data["reason_codes"]
    win_cond_raw = data["win_condition"]
    initial_state_raw = data["initial_state"]

    locations, npcs, items, flags, verbs, directions, use_targets, reason_codes = {}, {}, {}, {}, {}, {}, {}, {}
    id_re = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$") # D-08: verbs uppercase allowed

    # --- Phase 2 & 3: Registries and Validation ---
    for idx, loc in enumerate(locs_raw):
        if not isinstance(loc, dict) or "id" not in loc:
            errors.append(f"locations[{idx}]: missing or invalid id"); continue
        lid = loc["id"]
        if not id_re.match(str(lid)): errors.append(f"locations[{idx}].id='{lid}': invalid format")
        if lid in locations: errors.append(f"locations: duplicate id '{lid}'")
        exits = []
        for ex_idx, ex in enumerate(loc.get("exits", [])):
            if not isinstance(ex, dict) or "direction" not in ex or "to" not in ex:
                errors.append(f"locations[id={lid}].exits[{ex_idx}]: missing direction or to"); continue
            conds = [Condition(**c) for c in ex.get("condition", [])]
            effs = [Effect(**e) for e in ex.get("effects", [])]
            exits.append(Exit(direction=ex["direction"], to=ex["to"], condition=tuple(conds), effects=tuple(effs)))
        locations[lid] = Location(id=lid, name=loc.get("name", ""), description=loc.get("description", ""), exits=tuple(exits), entities=tuple(loc.get("entities", [])))

    for fid, fdata in flags_raw.items():
        if not id_re.match(str(fid)): errors.append(f"flags.id='{fid}': invalid format")
        if not isinstance(fdata, dict) or fdata.get("type") not in ("bool", "number"): errors.append(f"flags[{fid}]: type must be 'bool' or 'number'")
        if not fdata.get("description"): errors.append(f"flags[{fid}]: description missing")
        flags[fid] = Flag(type=fdata.get("type", "bool"), description=fdata.get("description", ""))

    for code, desc in reason_codes_raw.items(): reason_codes[code] = desc
    if "ok" not in reason_codes: errors.append("reason_codes: 'ok' missing")
    if "unknown" not in reason_codes: errors.append("reason_codes: 'unknown' missing")

    for npc in npcs_raw:
        nid = npc.get("id")
        if not nid: continue
        if not id_re.match(str(nid)): errors.append(f"npcs.id='{nid}': invalid format")
        if nid in npcs: errors.append(f"npcs: duplicate id '{nid}'")
        topics = []
        seen_keys = set()
        for t in npc.get("topics", []):
            key = t.get("key")
            if key in seen_keys: errors.append(f"npcs[id={nid}]: duplicate topic key '{key}'")
            seen_keys.add(key)
            conds = [Condition(**c) for c in t.get("condition", [])]
            effs = [Effect(**e) for e in t.get("effects", [])]
            topics.append(Topic(key=key, response=t.get("response", ""), response_on_fail=t.get("response_on_fail"), condition=tuple(conds), effects=tuple(effs)))
        npcs[nid] = Npc(id=nid, name=npc.get("name", ""), topics=tuple(topics))

    for item in items_raw:
        iid = item.get("id")
        if not iid: continue
        if not id_re.match(str(iid)): errors.append(f"items.id='{iid}': invalid format")
        if iid in items: errors.append(f"items: duplicate id '{iid}'")
        rules = []
        for u in item.get("use", []):
            conds = [Condition(**c) for c in u.get("condition", [])]
            effs = [Effect(**e) for e in u.get("effects", [])]
            rules.append(UseRule(target=u.get("target"), condition=tuple(conds), effects=tuple(effs), response=u.get("response"), response_on_fail=u.get("response_on_fail")))
        items[iid] = Item(id=iid, name=item.get("name", ""), takeable=item.get("takeable", False), use=tuple(rules))

    for ut in use_targets_raw:
        uid = ut.get("id")
        if not uid: continue
        if not id_re.match(str(uid)): errors.append(f"use_targets.id='{uid}': invalid format")
        if uid in use_targets: errors.append(f"use_targets: duplicate id '{uid}'")
        exit_ref = None
        if "exit" in ut and ut["exit"]:
            exit_ref = (ut["exit"].get("location"), ut["exit"].get("direction"))
        use_targets[uid] = UseTarget(id=uid, name=ut.get("name", ""), aliases=tuple(ut.get("aliases", [])), exit=exit_ref)

    all_aliases = set()
    for vid, vdata in verbs_raw.items():
        if not id_re.match(str(vid)): errors.append(f"verbs.id='{vid}': invalid format")
        aliases = vdata.get("aliases", [])
        if not aliases: errors.append(f"verbs[{vid}]: must have >=1 alias")
        for a in aliases:
            if a in all_aliases: errors.append(f"verbs: alias '{a}' overlaps with another verb")
            all_aliases.add(a)
        args = vdata.get("args", [])
        for a in args:
            if a not in ("direction", "item", "npc", "topic", "target"): errors.append(f"verbs[{vid}]: invalid arg type '{a}'")
        verbs[vid] = Verb(id=vid, aliases=tuple(aliases), args=tuple(args))

    for d in directions_raw:
        did = d.get("id")
        if not did: continue
        if not id_re.match(str(did)): errors.append(f"directions.id='{did}': invalid format")
        if not d.get("name"): errors.append(f"directions[id={did}]: name missing")
        if not d.get("aliases"): errors.append(f"directions[id={did}]: must have >=1 alias")
        directions[did] = Direction(id=did, name=d["name"], aliases=tuple(d["aliases"]))

    init_flags = initial_state_raw.get("flags", {})
    for f_id in init_flags:
        if f_id not in flags: errors.append(f"initial_state.flags: '{f_id}' not declared in flags")
    if errors: raise CanonError(errors)

    # --- Phase 4: Referential Integrity ---
    for lid, loc in locations.items():
        for ex in loc.exits:
            if ex.to not in locations: errors.append(f"locations[id={lid}].exits[direction={ex.direction}].to='{ex.to}': unknown location")
            if ex.direction not in directions: errors.append(f"locations[id={lid}].exits[direction={ex.direction}]: unknown direction")
        for eid in loc.entities:
            if eid not in items and eid not in npcs: errors.append(f"locations[id={lid}].entities: '{eid}' is neither item nor npc")

    npc_locs = {}
    for lid, loc in locations.items():
        for eid in loc.entities:
            if eid in npcs:
                if eid in npc_locs: errors.append(f"NPC '{eid}' in multiple locations: {npc_locs[eid]} and {lid}")
                else: npc_locs[eid] = lid
    for nid in npcs:
        if nid not in npc_locs: errors.append(f"NPC '{nid}' not in any location")

    for uid, ut in use_targets.items():
        if ut.exit:
            loc_id, dir_id = ut.exit
            if loc_id not in locations or dir_id not in [ex.direction for ex in locations[loc_id].exits]:
                errors.append(f"use_targets[id={uid}].exit: ({loc_id}, {dir_id}) does not resolve to an existing exit")

    for iid, item in items.items():
        for rule in item.use:
            t = rule.target
            if t not in npcs and t not in use_targets and t not in items: errors.append(f"items[id={iid}].use.target='{t}': unknown target")
            for c in rule.condition:
                if c.type == "flag" and c.flag not in flags: errors.append(f"items[{iid}].use.condition: unknown flag '{c.flag}'")
                if c.type == "inventory_count" and c.item not in items: errors.append(f"items[{iid}].use.condition: unknown item '{c.item}'")
            for e in rule.effects:
                if e.type == "set_flag" and e.flag not in flags: errors.append(f"items[{iid}].use.effects: unknown flag '{e.flag}'")
                if e.type in ("add_to_inventory", "remove_from_inventory") and e.item not in items: errors.append(f"items[{iid}].use.effects: unknown item '{e.item}'")

    for lid, loc in locations.items():
        for ex in loc.exits:
            for c in ex.condition:
                if c.type == "flag" and c.flag not in flags: errors.append(f"locations[{lid}].exits[{ex.direction}].condition: unknown flag")
            for e in ex.effects:
                if e.type == "set_flag" and e.flag not in flags: errors.append(f"locations[{lid}].exits[{ex.direction}].effects: unknown flag")

    for nid, npc in npcs.items():
        for topic in npc.topics:
            for c in topic.condition:
                if c.type == "flag" and c.flag not in flags: errors.append(f"npcs[{nid}].topics[{topic.key}].condition: unknown flag")
                if c.type == "inventory_count" and c.item not in items: errors.append(f"npcs[{nid}].topics[{topic.key}].condition: unknown item")
            for e in topic.effects:
                if e.type == "set_flag" and e.flag not in flags: errors.append(f"npcs[{nid}].topics[{topic.key}].effects: unknown flag")
                if e.type in ("add_to_inventory", "remove_from_inventory") and e.item not in items: errors.append(f"npcs[{nid}].topics[{topic.key}].effects: unknown item")

    if win_cond_raw:
        for c in win_cond_raw:
            if c.get("type") == "flag" and c.get("flag") not in flags: errors.append(f"win_condition: unknown flag '{c.get('flag')}'")
    if errors: raise CanonError(errors)

    # --- Phase 5 & 6: Semantics and Cross-consistency ---
    all_effects = []
    for iid, item in items.items():
        for r in item.use: all_effects.extend(r.effects)
    for nid, npc in npcs.items():
        for t in npc.topics: all_effects.extend(t.effects)
    for lid, loc in locations.items():
        for ex in loc.exits: all_effects.extend(ex.effects)

    for lid, loc in locations.items():
        for ex in loc.exits:
            for c in ex.condition:
                if c.type == "flag" and c.equals is True:
                    found = any(e.type == "set_flag" and e.flag == c.flag and e.value is True for e in all_effects)
                    if not found: errors.append(f"locations[{lid}].exits[{ex.direction}]: condition flag '{c.flag}' never set to true")

    if win_cond_raw:
        for c in win_cond_raw:
            if c.get("type") == "flag" and c.get("equals") is True:
                found = any(e.type == "set_flag" and e.flag == c.get("flag") and e.value is True for e in all_effects)
                if not found: errors.append(f"win_condition: flag '{c.get('flag')}' never set to true")

    item_in_entities = set()
    for loc in locations.values():
        for eid in loc.entities:
            if eid in items: item_in_entities.add(eid)
    item_produced = set()
    for e in all_effects:
        if e.type == "add_to_inventory" and e.item: item_produced.add(e.item)
    for iid in items:
        if iid not in item_in_entities and iid not in item_produced: errors.append(f"items[{iid}]: no instance in entities and never produced")

    for uid, ut in use_targets.items():
        if ut.exit:
            loc_id, dir_id = ut.exit
            loc = locations[loc_id]
            ex = next((e for e in loc.exits if e.direction == dir_id), None)
            if ex:
                for c in ex.condition:
                    if c.type == "flag" and c.equals is True:
                        use_rules_for_target = []
                        for item in items.values():
                            for r in item.use:
                                if r.target == uid: use_rules_for_target.append(r)
                        found = any(e.type == "set_flag" and e.flag == c.flag and e.value is True for r in use_rules_for_target for e in r.effects)
                        if not found: errors.append(f"use_targets[{uid}]: exit condition flag '{c.flag}' not set by any USE rule")

    if errors: raise CanonError(errors)

    meta = Meta(schema_version=meta_raw["schema_version"], title=meta_raw.get("title", ""), start_location=meta_raw["start_location"], note=meta_raw.get("note"))
    init_state = InitialState(location=initial_state_raw.get("location", meta.start_location), stats=initial_state_raw.get("stats", {}), inventory=initial_state_raw.get("inventory", {}), flags=initial_state_raw.get("flags", {}))
    win_cond_list = [WinCondition(**w) for w in win_cond_raw] if win_cond_raw else []
    
    return Canon(
        meta=meta, initial_state=init_state, flags=flags, locations=locations, npcs=npcs, items=items,
        use_targets=use_targets, verbs=verbs, directions=directions, reason_codes=reason_codes,
        win_condition=win_cond_list[0] if win_cond_list else WinCondition(type="flag", flag="game_won", equals=True)
    )

def load_templates(path: str, reason_codes: Set[str], verbs: Set[str] | None = None) -> Templates:
    with open(path, "r", encoding="utf-8") as f:
        try: data = yaml.safe_load(f)
        except yaml.YAMLError as e: raise TemplatesError([f"YAML parse error: {e}"])

    errors = []
    if not isinstance(data, dict): raise TemplatesError(["Root must be a mapping"])

    canon_guarantee = [(g["verb"], g["reason_code"]) for g in data.get("canon_guarantee", [])]

    generic = {}
    for code, variants in data.get("generic", {}).items():
        if not variants: errors.append(f"generic[{code}]: empty variants list")
        generic[code] = tuple(variants)

    for code in reason_codes:
        if code not in generic: errors.append(f"generic: missing for reason_code '{code}'")
        for v in generic.get(code, []):
            if "{" in v: errors.append(f"generic[{code}]: contains slots, but generic cannot have slots")

    specific = {}
    for verb, codes in data.get("specific", {}).items():
        specific[verb] = {code: tuple(variants) for code, variants in codes.items()}

    valid_slots = {"item", "npc", "target", "direction", "topic", "scene", "response", "response_on_fail"}
    for verb, codes in specific.items():
        for code, variants in codes.items():
            if not variants: errors.append(f"specific[{verb}][{code}]: empty")
            for v in variants:
                slots = set(re.findall(r"{([a-zA-Z_]+)}", v))
                for s in slots:
                    if s not in valid_slots: errors.append(f"specific[{verb}][{code}]: unknown slot '{{{s}}}'")
                if verb != "LOOK" and code == "ok" and "scene" in slots: errors.append(f"specific[{verb}][{code}]: '{{scene}}' only allowed in LOOK.ok")
                if code != "ok" and "scene" in slots: errors.append(f"specific[{verb}][{code}]: '{{scene}}' only allowed in LOOK.ok")

    check_verbs = verbs if verbs is not None else set(specific.keys())
    for v in check_verbs:
        if (v, "ok") not in canon_guarantee:
            if v not in specific or "ok" not in specific[v] or not specific[v]["ok"]:
                errors.append(f"specific[{v}][ok]: missing or empty, and not in canon_guarantee")

    if errors: raise TemplatesError(errors)

    return Templates(
        canon_guarantee=tuple(canon_guarantee),
        generic=generic,
        specific={v: {c: tuple(vars) for c, vars in codes.items()} for v, codes in specific.items()}
    )