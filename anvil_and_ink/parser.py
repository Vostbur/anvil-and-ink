import re
from types import MappingProxyType

from anvil_and_ink.models import Canon, Command

# D-62: коды парсера. Не пересекаются с canon.reason_codes.
PARSE_ERRORS = frozenset({
    "empty_input",
    "unknown_verb",
    "unknown_direction",
    "unknown_item",
    "unknown_npc",
    "unknown_target",
    "missing_direction",
    "missing_item",
    "missing_npc",
    "missing_target",
    "missing_topic",
    "trailing_tokens",
})

_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)

_STOPWORDS = frozenset({
    "в", "во", "на", "с", "со", "к", "ко", "от", "ото", "из",
    "по", "и", "о", "об", "обо", "про",
    "the", "a", "an", "to", "on", "in", "with", "from", "about", "at",
})


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (ca != cb),
            ))
        prev = curr
    return prev[-1]


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return 1.0 - _levenshtein(a, b) / max(len(a), len(b))


def _threshold(a: str, b: str) -> float:
    return 0.6 if max(len(a), len(b)) <= 3 else 0.7


def _match(token: str, aliases: dict[str, str]) -> tuple[str | None, float]:
    if token in aliases:
        return aliases[token], 1.0
    best_id: str | None = None
    best_score = 0.0
    for alias in sorted(aliases):
        score = _similarity(token, alias)
        if score > best_score and score >= _threshold(token, alias):
            best_score = score
            best_id = aliases[alias]
    return best_id, best_score


def _aliases(section: dict[str, dict], include_name: bool = True) -> dict[str, str]:
    out: dict[str, str] = {}
    for entity_id, entity in section.items():
        if include_name and entity.get("name"):
            out[normalize(entity["name"])] = entity_id
        for alias in entity.get("aliases", []):
            out[normalize(alias)] = entity_id
    return out


def _verb_aliases(verbs: dict[str, dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for verb_id in sorted(verbs):
        for alias in verbs[verb_id].get("aliases", []):
            norm = normalize(alias)
            out.setdefault(norm, verb_id)  # D-64
    return out


def _resolve_arg(spec: str, token: str, canon: Canon) -> tuple[str | None, float]:
    """D-80: возвращает (id, score) — score входит в общий confidence."""
    if spec == "direction":
        aliases = _aliases(canon.directions, include_name=False)
        for d_id, d in canon.directions.items():
            aliases[normalize(d["name"])] = d_id
        return _match(token, aliases)
    if spec == "item":
        return _match(token, _aliases(canon.items))
    if spec == "npc":
        return _match(token, _aliases(canon.npcs))
    if spec == "target":
        merged = {**_aliases(canon.use_targets), **_aliases(canon.npcs)}
        return _match(token, merged)
    if spec == "topic":
        return token, 1.0    # topic — свободный текст, всегда "успех"
    return None, 0.0


def _match_verb(tokens: list[str], verbs: dict[str, dict]) -> tuple[str | None, int, float]:
    aliases = _verb_aliases(verbs)
    for n in range(min(3, len(tokens)), 0, -1):
        phrase = " ".join(tokens[:n])
        resolved, score = _match(phrase, aliases)
        if resolved is not None:
            return resolved, n, score
    return None, 0, 0.0


def parse(raw: str, canon: Canon) -> Command:
    norm = normalize(raw)
    if not norm:
        return Command(raw=raw, verb=None, args=MappingProxyType({}),
                       parse_errors=("empty_input",), confidence=0.0)

    tokens = [t for t in norm.split() if t not in _STOPWORDS]
    if not tokens:
        return Command(raw=raw, verb=None, args=MappingProxyType({}),
                       parse_errors=("empty_input",), confidence=0.0)

    verb_id, consumed, confidence = _match_verb(tokens, canon.verbs)
    if verb_id is None:
        return Command(raw=raw, verb=None, args=MappingProxyType({}),
                       parse_errors=("unknown_verb",), confidence=0.0)

    remaining = tokens[consumed:]
    verb_def = canon.verbs[verb_id]
    required = list(verb_def.get("args", []))
    optional = list(verb_def.get("optional_args", []))

    args: dict[str, str | None] = {}
    errors: list[str] = []

    tail_is_topic = bool(required and required[-1] == "topic")
    positional = required[:-1] if tail_is_topic else required

    # Обязательные — всегда в args (None, если пропущен)
    for i, spec in enumerate(positional):
        if i < len(remaining):
            token = remaining[i]
            resolved, score = _resolve_arg(spec, token, canon)
            args[spec] = resolved
            confidence = min(confidence, score)
            if resolved is None:
                errors.append(f"unknown_{spec}")
        else:
            args[spec] = None
            errors.append(f"missing_{spec}")

    consumed_pos = len(positional)
    leftover = remaining[consumed_pos:]

    if tail_is_topic:
        args["topic"] = " ".join(leftover) if leftover else None
        if not leftover:
            errors.append("missing_topic")
        leftover = []

    # D-79: optional args попадают в args ТОЛЬКО если указаны
    for spec in optional:
        if leftover:
            token = leftover.pop(0)
            resolved, score = _resolve_arg(spec, token, canon)
            args[spec] = resolved
            confidence = min(confidence, score)
            if resolved is None:
                errors.append(f"unknown_{spec}")

    if leftover:
        errors.append("trailing_tokens")

    return Command(
        raw=raw,
        verb=verb_id,
        args=MappingProxyType(args),
        parse_errors=tuple(errors),
        confidence=confidence,
    )