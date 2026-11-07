"""Сборщик промпта для LLM + Manifest (D-75).

Собирает три части:
- system — роль и жёсткие правила;
- user — контекст (состояние, история, черновик, манифест);
- manifest — что фактически изменилось в этом ходу.

Не вызывает LLM. Чистая функция от аргументов.
"""

from types import MappingProxyType
from typing import Any

from anvil_and_ink.models import (
    Canon, Command, Event, GameState, Manifest, Outcome, Prompt, RenderedText,
)


# ---------- Manifest ----------

def build_manifest(
    outcome: Outcome,
    command: Command,
    before: GameState,
    after: GameState,
    rendered: RenderedText,
    turn: int,
) -> Manifest:
    inv_before = set(before.inventory)
    inv_after = set(after.inventory)
    added = tuple(sorted(inv_after - inv_before))
    removed = tuple(sorted(inv_before - inv_after))

    flags_changed: dict[str, tuple[Any, Any]] = {}
    for key in set(before.flags) | set(after.flags):
        v_before = before.flags.get(key)
        v_after = after.flags.get(key)
        if v_before != v_after:
            flags_changed[key] = (v_before, v_after)

    return Manifest(
        turn=turn,
        verb=command.verb,
        reason_code=outcome.reason_code,
        applied=outcome.applied,
        location_before=before.location,
        location_after=after.location,
        inventory_added=added,
        inventory_removed=removed,
        flags_changed=MappingProxyType(flags_changed),
        won=(not before.won) and after.won,
        game_over=(not before.game_over) and after.game_over,
        rendered_source=rendered.source,
    )


# ---------- История ----------

def _collect_history(events: list[Event], n: int) -> list[tuple[str, str]]:
    """Последние n завершённых ходов: [(raw_command, final_text), ...].

    Ход считается завершённым, если для него есть и turn-, и presentation-запись.
    Незавершённые (упавший LLM) — пропускаются: их нечего показывать модели.
    """
    if n <= 0:
        return []
    by_turn: dict[int, dict[str, Any]] = {}
    for ev in events:
        if ev.type == "turn":
            by_turn.setdefault(ev.turn, {})["command"] = ev.payload.get("command", {})
        elif ev.type == "presentation":
            by_turn.setdefault(ev.turn, {})["final_text"] = ev.payload.get("final_text")

    completed: list[tuple[int, str, str]] = []
    for t in sorted(by_turn):
        entry = by_turn[t]
        cmd = entry.get("command")
        text = entry.get("final_text")
        if not cmd or text is None:
            continue
        raw = cmd.get("raw", "") if isinstance(cmd, dict) else ""
        completed.append((t, raw, text))

    return [(raw, text) for _, raw, text in completed[-n:]]


# ---------- Форматирование ----------

def _loc_name(canon: Canon, loc_id: str) -> str:
    return canon.locations.get(loc_id, {}).get("name", loc_id)


def _item_name(canon: Canon, item_id: str) -> str:
    return canon.items.get(item_id, {}).get("name", item_id)


def _format_state(canon: Canon, state: GameState) -> str:
    loc = canon.locations.get(state.location, {})
    lines: list[str] = [f"Локация: {loc.get('name', state.location)}"]
    scene = loc.get("scene")
    if scene:
        lines.append(f"Описание: {scene}")
    if state.inventory:
        names = [_item_name(canon, i) for i in state.inventory]
        lines.append(f"Инвентарь: {', '.join(names)}")
    else:
        lines.append("Инвентарь: пусто")
    return "\n".join(lines)


def _format_manifest(canon: Canon, manifest: Manifest) -> str:
    lines: list[str] = []

    if manifest.location_before != manifest.location_after:
        lines.append(
            f"переход: {_loc_name(canon, manifest.location_before)} → "
            f"{_loc_name(canon, manifest.location_after)}"
        )
    else:
        lines.append(
            f"переход: нет (остаёмся в {_loc_name(canon, manifest.location_before)})"
        )

    if manifest.inventory_added:
        names = [_item_name(canon, i) for i in manifest.inventory_added]
        lines.append(f"в инвентарь добавлено: {', '.join(names)}")
    if manifest.inventory_removed:
        names = [_item_name(canon, i) for i in manifest.inventory_removed]
        lines.append(f"из инвентаря убрано: {', '.join(names)}")

    for flag, (old, new) in manifest.flags_changed.items():
        lines.append(f"флаг {flag}: {old} → {new}")

    if manifest.won:
        lines.append("СОСТОЯНИЕ: победа")
    if manifest.game_over:
        lines.append("СОСТОЯНИЕ: конец игры")

    if not manifest.applied:
        lines.append(f"команда не выполнена (причина: {manifest.reason_code})")

    return "\n".join(lines)


def _format_history(history: list[tuple[str, str]]) -> str:
    if not history:
        return ""
    parts = [f"> {raw}\n{text}" for raw, text in history]
    return "[Предыдущие ходы]\n" + "\n\n".join(parts) + "\n\n"


def _build_system(canon: Canon) -> str:
    lines: list[str] = [
        "Ты — рассказчик в текстовой приключенческой игре.",
        "Твоя задача — пересказать результат хода живым языком, не меняя фактов.",
        "",
        "Жёсткие правила:",
        "- Используй только факты из контекста и манифеста.",
        "- Не выдумывай новые предметы, NPC, локации или изменения состояния.",
        "- Не противоречь манифесту: если изменение не указано, его не было.",
        "- Сохраняй жанр и голос мира.",
        "- 1–3 предложения. Без мета-комментариев.",
        "- Не показывай служебные пометки, манифест или черновик как есть.",
    ]
    tone = canon.meta.get("tone")
    if tone:
        lines.insert(2, f"Тон: {tone}")
    return "\n".join(lines)


# ---------- Публичный API ----------

def build_prompt(
    canon: Canon,
    state_before: GameState,
    state_after: GameState,
    events: list[Event],
    command: Command,
    outcome: Outcome,
    rendered: RenderedText,
    turn: int,
    history_n: int = 5,
) -> Prompt:
    manifest = build_manifest(
        outcome=outcome,
        command=command,
        before=state_before,
        after=state_after,
        rendered=rendered,
        turn=turn,
    )
    history = _collect_history(events, history_n)

    user = (
        f"[Текущее состояние]\n"
        f"{_format_state(canon, state_after)}\n\n"
        f"{_format_history(history)}"
        f"[Текущий ход]\n> {command.raw}\n\n"
        f"[Черновик ответа]\n{rendered.text}\n\n"
        f"[Манифест изменения состояния]\n{_format_manifest(canon, manifest)}\n"
    )

    return Prompt(
        system=_build_system(canon),
        user=user,
        manifest=manifest,
        history_turns_used=len(history),
    )