import re
import yaml
from pathlib import Path

from anvil_and_ink.models import Templates

class TemplatesError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))

ALLOWED_SLOTS = {
    "item", "npc", "target", "direction", "topic",
    "scene", "response", "response_on_fail",
}

def load_templates(path: str | Path, reason_codes: set[str]) -> Templates:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TemplatesError([f"YAML syntax error: {e}"])

    if not isinstance(raw, dict):
        raise TemplatesError(["Root YAML must be a mapping"])

    canon_guarantee_raw = raw.get("canon_guarantee", [])
    canon_guarantee = {(g["verb"], g["reason_code"]) for g in canon_guarantee_raw}

    generic = raw.get("generic", {})
    specific = raw.get("specific", {})

    # ---------- Фатальные проверки ----------
    for code in reason_codes:
        if code not in generic or not generic[code]:
            errors.append(f"generic.{code}: missing or empty")

    for verb in specific:
        for code in specific[verb]:
            if (verb, code) not in canon_guarantee:
                variants = specific[verb][code]
                if not variants:
                    errors.append(f"specific.{verb}.{code}: empty list")

    # Каждый вариант — непустая строка
    def check_variants(variants: list, path: str) -> None:
        for i, text in enumerate(variants):
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{path}[{i}]: must be non-empty string")

    for code, variants in generic.items():
        check_variants(variants, f"generic.{code}")

    for verb, codes in specific.items():
        for code, variants in codes.items():
            check_variants(variants, f"specific.{verb}.{code}")

    # Слоты
    slot_re = re.compile(r"\{(\w+)\}")

    for code, variants in generic.items():
        for i, text in enumerate(variants):
            for slot in slot_re.findall(text):
                errors.append(f"generic.{code}[{i}]: slot {{{slot}}} not allowed in generic")

    for verb, codes in specific.items():
        for code, variants in codes.items():
            for i, text in enumerate(variants):
                for slot in slot_re.findall(text):
                    if slot not in ALLOWED_SLOTS:
                        errors.append(f"specific.{verb}.{code}[{i}]: unknown slot {{{slot}}}")

    # {scene} только в specific.LOOK.ok
    for verb, codes in specific.items():
        for code, variants in codes.items():
            for i, text in enumerate(variants):
                if "{scene}" in text and not (verb == "LOOK" and code == "ok"):
                    errors.append(
                        f"specific.{verb}.{code}[{i}]: {{scene}} only allowed in specific.LOOK.ok"
                    )

    if errors:
        raise TemplatesError(errors)

    return Templates(
        canon_guarantee=canon_guarantee,
        generic=generic,
        specific=specific,
    )