import re
import yaml
from pathlib import Path

from anvil_and_ink.models import Templates

# D-72: canon_guarantee — "эта пара (verb, reason_code) может отсутствовать
# в specific без ошибки; generic покроет". Отсутствие в guarantee без specific —
# НЕ ошибка (мы не требуем покрытия всех пар — это делает generic).
# Поле нужно для двух целей: (1) документировать намерение, (2) подсказать
# читателю, что specific тут не ожидается.


class TemplatesError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


ALLOWED_SLOTS = {
    "item", "npc", "target", "direction", "topic",
    "scene", "response",
}


def load_templates(path: str | Path, reason_codes: set[str]) -> Templates:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TemplatesError([f"YAML syntax error: {e}"])

    if not isinstance(raw, dict):
        raise TemplatesError(["Root YAML must be a mapping"])

    canon_guarantee_raw = raw.get("canon_guarantee", []) or []
    canon_guarantee: set[tuple[str, str]] = set()
    for i, g in enumerate(canon_guarantee_raw):
        if not isinstance(g, dict):
            errors.append(f"canon_guarantee[{i}]: must be a mapping")
            continue
        verb = g.get("verb")
        code = g.get("reason_code")
        if not isinstance(verb, str) or not isinstance(code, str):
            errors.append(f"canon_guarantee[{i}]: verb and reason_code must be strings")
            continue
        canon_guarantee.add((verb, code))

    generic = raw.get("generic", {}) or {}
    specific = raw.get("specific", {}) or {}

    # ---- Фатальные проверки ----
    for code in reason_codes:
        if code not in generic or not generic[code]:
            errors.append(f"generic.{code}: missing or empty")

    def check_variants(variants, path: str) -> None:
        if not isinstance(variants, list):
            errors.append(f"{path}: must be a list")
            return
        for i, text in enumerate(variants):
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{path}[{i}]: must be non-empty string")

    for code, variants in generic.items():
        check_variants(variants, f"generic.{code}")

    for verb, codes in specific.items():
        for code, variants in codes.items():
            check_variants(variants, f"specific.{verb}.{code}")

    # ---- Слоты ----
    slot_re = re.compile(r"\{(\w+)\}")

    for code, variants in generic.items():
        if not isinstance(variants, list):
            continue
        for i, text in enumerate(variants):
            if not isinstance(text, str):
                continue
            for slot in slot_re.findall(text):
                errors.append(
                    f"generic.{code}[{i}]: slot {{{slot}}} not allowed in generic"
                )

    for verb, codes in specific.items():
        for code, variants in codes.items():
            if not isinstance(variants, list):
                continue
            for i, text in enumerate(variants):
                if not isinstance(text, str):
                    continue
                for slot in slot_re.findall(text):
                    if slot not in ALLOWED_SLOTS:
                        errors.append(
                            f"specific.{verb}.{code}[{i}]: unknown slot {{{slot}}}"
                        )
                if "{scene}" in text and not (verb == "LOOK" and code == "ok"):
                    errors.append(
                        f"specific.{verb}.{code}[{i}]: "
                        f"{{scene}} only allowed in specific.LOOK.ok"
                    )

    if errors:
        raise TemplatesError(errors)

    return Templates(
        canon_guarantee=canon_guarantee,
        generic=generic,
        specific=specific,
    )