"""Валидатор прозы от LLM (D-82..D-84).

Проверяет текст против манифеста (D-75). Pure function: ничего не мутирует,
не вызывает LLM, не пишет в лог. Возвращает ValidationResult, решение
о fallback принимает session.
"""

import re
from dataclasses import dataclass, replace
from typing import Literal

from anvil_and_ink.models import Canon, GameState, Manifest, RenderedText


ValidationSeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: ValidationSeverity
    message: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool                                     # нет ни одной error
    issues: tuple[ValidationIssue, ...]
    fallback_used: bool                          # применён ли fallback к тексту


# --- D-82: закрытый набор кодов валидатора ---
# too_short, too_long — длина
# leaked_marker — {slot} или служебная метка в тексте
# meta_commentary — «как ассистент», «я не могу»
# missing_change_fact — манифест говорит об изменении, текст его не отражает
# invented_entity — упомянута сущность вне зоны видимости (warning)

_SLOT_LEAK_RE = re.compile(r"\{\w+\}")
_BOOKKEEPING_MARKERS = (
    "[Черновик",
    "[Манифест",
    "[Текущее состояние]",
    "[Текущий ход]",
    "[Предыдущие ходы]",
)
_META_PHRASES = (
    "как ассистент",
    "как языковая модель",
    "я не могу",
    "я не способен",
    "извините, я",
    "as an ai",
    "as a language model",
)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?…]+")


# --- D-83: морфологически-устойчивый поиск имени в тексте ---
# Ключ = первые 3..5 букв имени. Ловит «Ключ/Ключом/Ключа», «Торговец/Торговца»,
# «Меч/Мечом». Может дать false positive на редких словах с общим префиксом
# («мечтать» → «меч»), но в контексте валидатора это допустимо:
# для invented_entity это warning, для missing_change_fact — редкое исключение.

def _match_key(name: str) -> str:
    name = name.lower().strip()
    if not name:
        return ""
    if len(name) <= 3:
        return name
    return name[:5] if len(name) >= 5 else name[:len(name) - 1]


def _mentions(text: str, name: str) -> bool:
    key = _match_key(name)
    return bool(key) and key in text.lower()


def _sentence_count(text: str) -> int:
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return sum(1 for p in parts if p.strip())


def _in_scope(
    state: GameState,
    manifest: Manifest,
    canon: Canon,
    entity_id: str,
    kind: Literal["item", "npc"],
) -> bool:
    loc_entities = canon.locations.get(state.location, {}).get("entities", [])
    if kind == "item":
        if entity_id in state.inventory:
            return True
        if entity_id in loc_entities:
            return True
        if entity_id in manifest.inventory_added:
            return True
        if entity_id in manifest.inventory_removed:
            return True
        return False
    if kind == "npc":
        return entity_id in loc_entities
    return True


def _check_manifest_coverage(
    text: str,
    canon: Canon,
    manifest: Manifest,
) -> list[ValidationIssue]:
    """Проверки уровня error: манифест утверждает изменение — текст обязан его отразить."""
    issues: list[ValidationIssue] = []
    for item_id in manifest.inventory_added:
        name = canon.items.get(item_id, {}).get("name", item_id)
        if not _mentions(text, name):
            issues.append(ValidationIssue(
                code="missing_change_fact",
                severity="error",
                message=(
                    f"добавлен предмет {item_id!r}, "
                    f"но текст не упоминает {name!r}"
                ),
            ))
    for item_id in manifest.inventory_removed:
        name = canon.items.get(item_id, {}).get("name", item_id)
        if not _mentions(text, name):
            issues.append(ValidationIssue(
                code="missing_change_fact",
                severity="error",
                message=(
                    f"удалён предмет {item_id!r}, "
                    f"но текст не упоминает {name!r}"
                ),
            ))
    return issues


def _check_invented_entities(
    text: str,
    canon: Canon,
    state: GameState,
    manifest: Manifest,
) -> list[ValidationIssue]:
    """Проверки уровня warning: упомянуты сущности канона вне зоны видимости."""
    issues: list[ValidationIssue] = []

    for item_id, item in canon.items.items():
        name = item.get("name")
        if not name or not _mentions(text, name):
            continue
        if not _in_scope(state, manifest, canon, item_id, "item"):
            issues.append(ValidationIssue(
                code="invented_entity",
                severity="warning",
                message=f"упомянут предмет {item_id!r} вне зоны видимости",
            ))

    for npc_id, npc in canon.npcs.items():
        name = npc.get("name")
        if not name or not _mentions(text, name):
            continue
        if not _in_scope(state, manifest, canon, npc_id, "npc"):
            issues.append(ValidationIssue(
                code="invented_entity",
                severity="warning",
                message=f"упомянут NPC {npc_id!r} вне зоны видимости",
            ))

    return issues


def validate(
    text: str,
    rendered: RenderedText,
    manifest: Manifest,
    canon: Canon,
    state: GameState,
    *,
    min_chars: int = 10,
    max_chars: int = 800,
    max_sentences: int = 5,
) -> ValidationResult:
    """Проверить текст LLM против манифеста и канона.

    rendered используется только для получения fallback-текста при
    последующем apply_fallback; сам validate rendered не модифицирует.
    """
    issues: list[ValidationIssue] = []
    stripped = text.strip()

    # --- длина ---
    if len(stripped) < min_chars:
        issues.append(ValidationIssue(
            code="too_short", severity="error",
            message=f"длина {len(stripped)} < {min_chars}",
        ))
    if len(stripped) > max_chars:
        issues.append(ValidationIssue(
            code="too_long", severity="error",
            message=f"длина {len(stripped)} > {max_chars}",
        ))
    if _sentence_count(stripped) > max_sentences:
        issues.append(ValidationIssue(
            code="too_long", severity="error",
            message=f"предложений больше {max_sentences}",
        ))

    # --- утечки ---
    if _SLOT_LEAK_RE.search(stripped):
        issues.append(ValidationIssue(
            code="leaked_marker", severity="error",
            message="в тексте остался неразвёрнутый {slot}",
        ))
    for marker in _BOOKKEEPING_MARKERS:
        if marker in stripped:
            issues.append(ValidationIssue(
                code="leaked_marker", severity="error",
                message=f"служебная метка {marker!r} попала в ответ",
            ))

    # --- мета-комментарии ---
    low = stripped.lower()
    for phrase in _META_PHRASES:
        if phrase in low:
            issues.append(ValidationIssue(
                code="meta_commentary", severity="error",
                message=f"мета-фраза {phrase!r}",
            ))

    # --- манифест и сущности ---
    issues.extend(_check_manifest_coverage(stripped, canon, manifest))
    issues.extend(_check_invented_entities(stripped, canon, state, manifest))

    has_error = any(i.severity == "error" for i in issues)
    return ValidationResult(
        ok=not has_error,
        issues=tuple(issues),
        fallback_used=False,
    )


def apply_fallback(
    text: str,
    rendered: RenderedText,
    result: ValidationResult,
) -> tuple[str, ValidationResult]:
    """Если результат невалиден — вернуть rendered.text и пометить fallback_used."""
    if result.ok:
        return text, result
    return rendered.text, replace(result, fallback_used=True)