import re
from typing import Mapping

from anvil_and_ink.models import (
    Canon, Command, GameState, Outcome, RenderedText, Templates,
)


class RenderError(Exception):
    pass


_SLOT_RE = re.compile(r"\{(\w+)\}")


def _registry_for(canon: Canon, kind: str) -> dict:
    if kind == "item":
        return canon.items
    if kind == "npc":
        return canon.npcs
    if kind == "direction":
        return canon.directions
    if kind == "target":
        return {**canon.use_targets, **canon.npcs}
    return {}


def _entity_name(canon: Canon, kind: str, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    entity = _registry_for(canon, kind).get(entity_id)
    if entity is None:
        return None
    return entity.get("name") or entity_id      # D-69


def _build_slots(
    canon: Canon,
    state: GameState,
    command: Command,
    extra: Mapping[str, str] | None,
) -> dict[str, str]:
    args = command.args
    slots: dict[str, str] = {}

    for kind in ("item", "npc", "direction", "target"):
        name = _entity_name(canon, kind, args.get(kind))
        if name is not None:
            slots[kind] = name

    if args.get("topic"):
        slots["topic"] = args["topic"]

    scene = canon.locations.get(state.location, {}).get("scene")
    if scene:
        slots["scene"] = scene

    if extra:
        for k, v in extra.items():
            slots.setdefault(k, v)

    return slots


def _pick_variant(variants: list[str], turn: int) -> str:
    if not variants:
        raise RenderError("empty variants list")
    idx = max(0, turn - 1) % len(variants)    # D-70
    return variants[idx]


def _fill(template: str, slots: Mapping[str, str]) -> tuple[str, tuple[str, ...]]:
    missing: list[str] = []

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in slots:
            return slots[name]
        missing.append(name)
        return m.group(0)

    text = _SLOT_RE.sub(repl, template)
    return text, tuple(missing)


def render(
    outcome: Outcome,
    command: Command,
    state: GameState,
    canon: Canon,
    templates: Templates,
    turn: int,
    extra_slots: Mapping[str, str] | None = None,
) -> RenderedText:
    verb = command.verb
    code = outcome.reason_code
    slots = _build_slots(canon, state, command, extra_slots)
    warnings: list[str] = []

    # 1. specific[verb][code]
    if verb is not None:
        specific_variants = templates.specific.get(verb, {}).get(code)
        if specific_variants:
            template = _pick_variant(specific_variants, turn)
            text, missing = _fill(template, slots)
            if not missing:
                return RenderedText(
                    text=text, source="specific", template=template,
                )
            warnings.append(
                f"missing slots in specific.{verb}.{code}: {sorted(missing)}"
            )
            # D-74: fallback на generic

    # 2. generic[code]
    generic_variants = templates.generic.get(code)
    if not generic_variants:
        raise RenderError(f"no generic template for reason_code {code!r}")

    template = _pick_variant(generic_variants, turn)
    text, missing = _fill(template, slots)
    if missing:
        warnings.append(
            f"missing slots in generic.{code}: {sorted(missing)} "
            f"(BUG in templates.yaml)"
        )

    return RenderedText(
        text=text, source="generic", template=template, warnings=warnings,
    )