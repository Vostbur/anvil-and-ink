from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from .models import Outcome, Templates


# Слоты вида {name}. Набор имён проверен загрузчиком шаблонов.
_SLOT_RE = re.compile(r"\{(\w+)\}")

# Универсальная заглушка. Недостижима в норме: полнота generic[reason_code]
# гарантирована загрузчиком шаблонов. Если возвращена — это баг (сигнал CRITICAL).
_UNIVERSAL_STUB = "…"


@dataclass(frozen=True)
class RenderSnapshot:
    """
    Минимальный набор данных для рендерера шаблонов.

    Рендерер не читает проекцию/канон напрямую — всё приходит сюда.
    Строится сессией перед вызовом render(). См. D-61.
    """

    # id из command.args (None, если аргумент не разрешился)
    item_id: Optional[str] = None
    npc_id: Optional[str] = None
    target_id: Optional[str] = None
    direction_id: Optional[str] = None
    topic: Optional[str] = None

    # имена по id (заполняются сессией из канона)
    item_names: Mapping[str, str] = field(default_factory=dict)
    npc_names: Mapping[str, str] = field(default_factory=dict)
    use_target_names: Mapping[str, str] = field(default_factory=dict)
    direction_names: Mapping[str, str] = field(default_factory=dict)

    # ингредиенты сцены для LOOK (см. D-62)
    location_description: str = ""
    npcs_here: Tuple[str, ...] = ()
    items_here: Tuple[Tuple[str, int], ...] = ()  # (имя, количество)
    use_targets_here: Tuple[str, ...] = ()

    def item_name(self, item_id: str) -> Optional[str]:
        return self.item_names.get(item_id)

    def npc_name(self, npc_id: str) -> Optional[str]:
        return self.npc_names.get(npc_id)

    def use_target_name(self, target_id: str) -> Optional[str]:
        return self.use_target_names.get(target_id)

    def direction_name(self, direction_id: str) -> Optional[str]:
        return self.direction_names.get(direction_id)


def render(
    verb: str,
    reason_code: str,
    outcome: Optional[Outcome],
    snapshot: RenderSnapshot,
    turn: int,
    templates: Templates,
) -> str:
    """
    Детерминированный fallback-нарратор.

    Порядок выбора текста:
      1. outcome.response_text — canon-текст выше шаблона.
      2. specific[verb][reason_code] — если слоты заполнимы.
      3. generic[reason_code] — существует всегда (гарантия загрузчика).
      4. Универсальная заглушка — только при баге.
    """

    # 1. Canon-текст.
    response_text = outcome.response_text if outcome is not None else None
    if response_text is not None:
        return _fill_canon_text(response_text, snapshot)

    # 2. Специфичные шаблоны.
    variants = templates.specific.get(verb, {}).get(reason_code)
    if variants:
        filled = _pick_fillable_variant(variants, turn, snapshot)
        if filled is not None:
            return filled

    # 3. Общие шаблоны (слотов нет — гарантия загрузчика).
    generic = templates.generic.get(reason_code)
    if generic:
        return generic[turn % len(generic)]

    # 4. Недостижимо при корректных шаблонах.
    return _UNIVERSAL_STUB


def _pick_fillable_variant(
    variants: Tuple[str, ...],
    turn: int,
    snapshot: RenderSnapshot,
) -> Optional[str]:
    """
    Начать с варианта turn % len(variants); если его слоты не заполнимы —
    пробовать следующие по кругу. Все не заполнимы → None (переход к generic).
    """

    n = len(variants)
    start = turn % n
    for offset in range(n):
        variant = variants[(start + offset) % n]
        filled, ok = _try_fill(variant, snapshot)
        if ok:
            return filled
    return None


def _try_fill(template: str, snapshot: RenderSnapshot) -> Tuple[Optional[str], bool]:
    """
    Подставить все слоты. Если хотя бы один слот не заполним —
    вариант пропускается целиком (возвращается (None, False)).
    """

    slot_names = set(_SLOT_RE.findall(template))
    values: dict[str, str] = {}
    for name in slot_names:
        value = _resolve_slot(name, snapshot)
        if value is None:
            return None, False
        values[name] = value

    result = template
    for name, value in values.items():
        result = result.replace("{" + name + "}", value)
    return result, True


def _fill_canon_text(text: str, snapshot: RenderSnapshot) -> str:
    """
    Best-effort подстановка для canon-текста (response_text).
    Незаполненные слоты в canon-тексте не трогаем — это ответственность
    автора канона (в MVP слотов в canon-тексте нет).
    """

    result = text
    for name in set(_SLOT_RE.findall(text)):
        value = _resolve_slot(name, snapshot)
        if value is not None:
            result = result.replace("{" + name + "}", value)
    return result


def _resolve_slot(name: str, snapshot: RenderSnapshot) -> Optional[str]:
    if name == "item":
        if snapshot.item_id is None:
            return None
        return snapshot.item_name(snapshot.item_id)
    if name == "npc":
        if snapshot.npc_id is None:
            return None
        return snapshot.npc_name(snapshot.npc_id)
    if name == "target":
        if snapshot.target_id is None:
            return None
        return snapshot.use_target_name(snapshot.target_id)
    if name == "direction":
        if snapshot.direction_id is None:
            return None
        return snapshot.direction_name(snapshot.direction_id)
    if name == "topic":
        return snapshot.topic
    if name == "scene":
        return _assemble_scene(snapshot)
    # {response} и {response_on_fail} зарезервированы, в MVP не используются.
    return None


def _assemble_scene(snapshot: RenderSnapshot) -> str:
    """
    Сборка {scene} для LOOK. Порядок: NPC → предметы → use_targets (D-62).
    Пустая локация → «{описание} Здесь пусто.»
    """

    entities: list[str] = []
    for name in snapshot.npcs_here:
        entities.append(name)
    for name, count in snapshot.items_here:
        if count > 1:
            entities.append(f"{name} ×{count}")
        else:
            entities.append(name)
    for name in snapshot.use_targets_here:
        entities.append(name)

    description = snapshot.location_description
    if entities:
        return f"{description} Здесь: {', '.join(entities)}."
    return f"{description} Здесь пусто."

def build_render_snapshot(command, outcome, projection, canon) -> RenderSnapshot:
    """
    Собирает минимальный снапшот для рендерера шаблонов.

    Используется лестницей на фолбэк-путях (ступень 3/4 и UNKNOWN). См. D-67.
    """

    loc_id = projection.current_location()
    loc = canon.locations.get(loc_id)

    item_names = {iid: item.name for iid, item in canon.items.items()}
    npc_names = {nid: npc.name for nid, npc in canon.npcs.items()}
    use_target_names = {uid: ut.name for uid, ut in canon.use_targets.items()}
    direction_names = {did: d.name for did, d in canon.directions.items()}

    npcs_here = tuple(
        npc_names[nid] for nid in projection.npcs_here() if nid in npc_names
    )
    items_here = tuple(
        (item_names[iid], count)
        for iid, count in projection.items_here().items()
        if iid in item_names
    )
    use_targets_here = tuple(
        use_target_names[uid]
        for uid in projection.use_targets_here()
        if uid in use_target_names
    )

    return RenderSnapshot(
        item_id=command.args.get("item"),
        npc_id=command.args.get("npc"),
        target_id=command.args.get("target"),
        direction_id=command.args.get("direction"),
        topic=command.args.get("topic"),
        item_names=item_names,
        npc_names=npc_names,
        use_target_names=use_target_names,
        direction_names=direction_names,
        location_description=loc.description if loc else "",
        npcs_here=npcs_here,
        items_here=items_here,
        use_targets_here=use_targets_here,
    )