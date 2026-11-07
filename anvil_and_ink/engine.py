from typing import Any

from anvil_and_ink.models import Canon, Command, GameState, Outcome

# D-66
ENGINE_REASON_CODES = frozenset({"ok", "unknown", "denied", "not_applicable"})


def _check_condition(state: GameState, cond: dict[str, Any]) -> bool:
    t = cond.get("type")
    if t == "flag":
        expected = cond.get("equals", True)
        return state.flags.get(cond["flag"]) == expected
    if t == "inventory_count":
        count = sum(1 for x in state.inventory if x == cond["item"])
        op = cond.get("op", ">=")
        target = cond["count"]
        return {
            ">=": count >= target,
            "==": count == target,
            ">":  count >  target,
            "<":  count <  target,
            "<=": count <= target,
            "!=": count != target,
        }.get(op, False)
    return False


def _check_conditions(state: GameState, conditions: list[dict]) -> bool:
    return all(_check_condition(state, c) for c in conditions)


def check_win_condition(state: GameState, canon: Canon) -> bool:
    if state.won:
        return True
    if not canon.win_condition:
        return False
    return all(_check_condition(state, c) for c in canon.win_condition)


class Engine:
    def __init__(self, canon: Canon):
        self.canon = canon
        missing = ENGINE_REASON_CODES - canon.reason_codes
        if missing:
            raise ValueError(
                f"canon.reason_codes missing required codes: {sorted(missing)}"
            )
        self._npc_location: dict[str, str] = {}
        for loc_id, loc in canon.locations.items():
            for ent in loc.get("entities", []):
                if ent in canon.npcs:
                    self._npc_location[ent] = loc_id

    def answer(self, state: GameState, command: Command) -> Outcome:
        if state.game_over:
            return Outcome(applied=False, reason_code="denied")
        if not command.recognized:
            return Outcome(applied=False, reason_code="unknown")
        handler = getattr(self, f"_handle_{command.verb}", None)
        if handler is None:
            return Outcome(applied=False, reason_code="not_applicable")
        return handler(state, command)

    # ---- LOOK ----
    def _handle_LOOK(self, state: GameState, cmd: Command) -> Outcome:
        item = cmd.args.get("item")
        if item is not None:
            loc_entities = self.canon.locations[state.location].get("entities", [])
            if item not in state.inventory and item not in loc_entities:
                return Outcome(False, "unknown")
        return Outcome(True, "ok")

    # ---- GO ----
    def _handle_GO(self, state: GameState, cmd: Command) -> Outcome:
        direction = cmd.args.get("direction")
        if direction is None:
            return Outcome(False, "unknown")
        location = self.canon.locations[state.location]
        exit_ = next(
            (e for e in location.get("exits", []) if e["direction"] == direction),
            None,
        )
        if exit_ is None:
            return Outcome(False, "unknown")
        if not _check_conditions(state, exit_.get("condition", [])):
            return Outcome(False, "denied")
        effects: list[dict] = [
            {"type": "move", "from": state.location, "to": exit_["to"]},
        ]
        effects.extend(exit_.get("effects", []))
        return Outcome(True, "ok", tuple(effects))

    # ---- TAKE ----
    def _handle_TAKE(self, state: GameState, cmd: Command) -> Outcome:
        item = cmd.args.get("item")
        if item is None or item not in self.canon.items:
            return Outcome(False, "unknown")
        if item in state.inventory:
            return Outcome(False, "denied")
        loc_entities = self.canon.locations[state.location].get("entities", [])
        if item not in loc_entities:
            return Outcome(False, "unknown")
        return Outcome(True, "ok", ({"type": "add_to_inventory", "item": item},))

    # ---- USE ----
    # D-67: use_target доступен только из exit.location; NPC — только из локации,
    # где он в entities. Фикс: явное ветвление по реестру (был AttributeError).
    def _handle_USE(self, state: GameState, cmd: Command) -> Outcome:
        item = cmd.args.get("item")
        target = cmd.args.get("target")
        if item is None or target is None:
            return Outcome(False, "unknown")
        if item not in state.inventory:
            return Outcome(False, "unknown")

        rules: list[dict] = []
        if target in self.canon.use_targets:
            ut = self.canon.use_targets[target]
            exit_ref = ut.get("exit")
            if exit_ref and exit_ref.get("location") != state.location:
                return Outcome(False, "unknown")
            rules = ut.get("use", [])
        elif target in self.canon.npcs:
            npc_loc = self._npc_location.get(target)
            if npc_loc is not None and npc_loc != state.location:
                return Outcome(False, "unknown")
            rules = self.canon.npcs[target].get("use", [])
        else:
            return Outcome(False, "unknown")

        for rule in rules:
            if rule.get("target") != item:
                continue
            if not _check_conditions(state, rule.get("condition", [])):
                return Outcome(False, "denied")
            return Outcome(True, "ok", tuple(rule.get("effects", [])))

        return Outcome(False, "not_applicable")

    # ---- TALK ----
    def _handle_TALK(self, state: GameState, cmd: Command) -> Outcome:
        npc = cmd.args.get("npc")
        if npc is None or npc not in self.canon.npcs:
            return Outcome(False, "unknown")
        npc_loc = self._npc_location.get(npc)
        if npc_loc is not None and npc_loc != state.location:
            return Outcome(False, "unknown")
        return Outcome(True, "ok")

    # ---- SAY ----
    def _handle_SAY(self, state: GameState, cmd: Command) -> Outcome:
        return Outcome(True, "ok")