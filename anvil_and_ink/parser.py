from __future__ import annotations

import re
from typing import Optional

from .models import Canon, Command
from .projection import ProjectionLookup


try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
    class _FuzzFallback:
        @staticmethod
        def _levenshtein(s1: str, s2: str) -> int:
            if len(s1) < len(s2):
                s1, s2 = s2, s1
            if not s2:
                return len(s1)
            prev = list(range(len(s2) + 1))
            for i, c1 in enumerate(s1):
                curr = [i + 1]
                for j, c2 in enumerate(s2):
                    curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
                prev = curr
            return prev[-1]

        @staticmethod
        def ratio(s1: str, s2: str) -> float:
            if not s1 and not s2:
                return 100.0
            if not s1 or not s2:
                return 0.0
            dist = _FuzzFallback._levenshtein(s1, s2)
            return (1.0 - dist / max(len(s1), len(s2))) * 100.0
    fuzz = _FuzzFallback()


def normalize(text: str) -> str:
    text = text.lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

# Стемминг: усечённый набор окончаний + падежные формы причастий/прилагательных.
# Симметрично валидатору (D-35), расширено для косвенных падежей (D-76).
_ENDINGS = (
    "ого", "его", "ому", "ему", "ыми", "ими", "ую",
    "ов", "ев", "ой", "ый", "ая", "ое", "ом", "ем",
    "ах", "ях", "у", "ю", "а", "я", "ы", "и", "е",
)


def _stem(word: str) -> str:
    for ending in _ENDINGS:  # уже отсортированы от длинных к коротким
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _find_verb(
    tokens: list[str],
    canon: Canon,
    fuzzy_threshold: int,
) -> tuple[Optional[str], int]:
    if not tokens:
        return None, 0

    aliases: list[tuple[str, str]] = []
    for verb_id, verb in canon.verbs.items():
        for alias in verb.aliases:
            aliases.append((normalize(alias), verb_id))

    for alias_norm, verb_id in sorted(
        aliases, key=lambda item: len(item[0].split()), reverse=True
    ):
        alias_tokens = alias_norm.split()
        if len(alias_tokens) <= len(tokens):
            if tokens[:len(alias_tokens)] == alias_tokens:
                return verb_id, len(alias_tokens)

    best_verb: Optional[str] = None
    best_score = 0
    first_token = tokens[0]

    for alias_norm, verb_id in aliases:
        if " " in alias_norm:
            continue
        score = fuzz.ratio(first_token, alias_norm)
        if score > best_score:
            best_score = score
            best_verb = verb_id

    if best_score >= fuzzy_threshold and best_verb is not None:
        return best_verb, 1

    return None, 0


def resolve_phrase(
    phrase: str,
    arg_type: str,
    canon: Canon,
    projection: Optional[ProjectionLookup] = None,
    fuzzy_threshold: int = 85,
) -> Optional[str]:
    if not phrase:
        return None

    phrase_norm = normalize(phrase)
    # Порог для пословного/подстрочного матча ниже, чем для всей фразы (D-76).
    word_threshold = 75
    candidates: list[tuple] = []

    if arg_type == "direction":
        for direction_id, direction in canon.directions.items():
            for alias in direction.aliases:
                if normalize(alias) == phrase_norm:
                    return direction_id
            candidates.append((direction_id, normalize(direction.name), tuple(direction.aliases)))

    elif arg_type == "item":
        for item_id, item in canon.items.items():
            norm_name = normalize(item.name)
            if norm_name == phrase_norm:
                return item_id
            candidates.append((item_id, norm_name, tuple()))
            # КЛЮЧЕВАЯ ПРАВКА: отдельные слова многословного имени
            for word in norm_name.split():
                if len(word) > 2:
                    candidates.append((item_id, word, tuple()))

    elif arg_type == "npc":
        for npc_id, npc in canon.npcs.items():
            norm_name = normalize(npc.name)
            if norm_name == phrase_norm:
                return npc_id
            candidates.append((npc_id, norm_name, tuple()))
            # КЛЮЧЕВАЯ ПРАВКА: отдельные слова многословного имени
            for word in norm_name.split():
                if len(word) > 2:
                    candidates.append((npc_id, word, tuple()))

    elif arg_type == "topic":
        for npc in canon.npcs.values():
            for topic in npc.topics:
                norm_key = normalize(topic.key)
                if norm_key == phrase_norm:
                    return topic.key
                candidates.append((topic.key, norm_key, tuple()))

    elif arg_type == "target":
        for target_id, use_target in canon.use_targets.items():
            norm_name = normalize(use_target.name)
            if norm_name == phrase_norm:
                return target_id
            for alias in use_target.aliases:
                if normalize(alias) == phrase_norm:
                    return target_id
            candidates.append((target_id, norm_name, tuple(use_target.aliases), "use_target"))
            # КЛЮЧЕВАЯ ПРАВКА
            for word in norm_name.split():
                if len(word) > 2:
                    candidates.append((target_id, word, tuple(use_target.aliases), "use_target"))

        for npc_id, npc in canon.npcs.items():
            norm_name = normalize(npc.name)
            if norm_name == phrase_norm:
                return npc_id
            candidates.append((npc_id, norm_name, tuple(), "npc"))
            for word in norm_name.split():
                if len(word) > 2:
                    candidates.append((npc_id, word, tuple(), "npc"))

        for item_id, item in canon.items.items():
            norm_name = normalize(item.name)
            if norm_name == phrase_norm:
                return item_id
            candidates.append((item_id, norm_name, tuple(), "item"))
            for word in norm_name.split():
                if len(word) > 2:
                    candidates.append((item_id, word, tuple(), "item"))

    else:
        return None

    # Fuzzy по всей фразе
    best_match: Optional[str] = None
    best_score = 0

    for candidate in candidates:
        candidate_id = candidate[0]
        candidate_name = candidate[1]
        candidate_aliases = candidate[2] if len(candidate) > 2 else tuple()

        score = fuzz.ratio(phrase_norm, candidate_name)
        if score > best_score:
            best_score = score
            best_match = candidate_id

        for alias in candidate_aliases:
            alias_norm = normalize(alias)
            score = fuzz.ratio(phrase_norm, alias_norm)
            if score > best_score:
                best_score = score
                best_match = candidate_id

    if best_score >= fuzzy_threshold:
        return best_match

    # 4. Фраза цели: longest substring match со стеммингом и порогом 75 (D-76).
    if arg_type in {"item", "npc", "target"}:
        phrase_tokens = phrase_norm.split()

        for length in range(len(phrase_tokens), 0, -1):
            for start in range(len(phrase_tokens) - length + 1):
                subphrase = " ".join(phrase_tokens[start:start + length])
                sub_stems = " ".join(_stem(t) for t in phrase_tokens[start:start + length])

                for candidate in candidates:
                    candidate_id = candidate[0]
                    candidate_name = candidate[1]
                    candidate_aliases = candidate[2] if len(candidate) > 2 else tuple()

                    # 4a. Точное совпадение подстроки.
                    if subphrase == candidate_name:
                        return candidate_id

                    # 4b. Стемминг (пословно).
                    cand_stems = " ".join(_stem(t) for t in candidate_name.split())
                    if sub_stems == cand_stems:
                        return candidate_id

                    # 4c. Fuzzy 75 по подстроке.
                    if fuzz.ratio(subphrase, candidate_name) >= word_threshold:
                        return candidate_id

                    for alias in candidate_aliases:
                        alias_norm = normalize(alias)
                        if subphrase == alias_norm:
                            return candidate_id
                        alias_stems = " ".join(_stem(t) for t in alias_norm.split())
                        if sub_stems == alias_stems:
                            return candidate_id
                        if fuzz.ratio(subphrase, alias_norm) >= word_threshold:
                            return candidate_id

    return None


def parse(
    raw: str,
    canon: Canon,
    projection: Optional[ProjectionLookup] = None,
    fuzzy_threshold: int = 85,
) -> Command:
    normalized = normalize(raw)
    tokens = normalized.split()

    if not tokens:
        return Command(verb="UNKNOWN", args={}, resolved={}, raw=raw)

    verb_id, verb_token_count = _find_verb(tokens, canon, fuzzy_threshold)

    if verb_id is None:
        return Command(verb="UNKNOWN", args={}, resolved={}, raw=raw)

    remainder_tokens = tokens[verb_token_count:]
    remainder_str = " ".join(remainder_tokens)

    args: dict[str, Optional[str]] = {}
    resolved: dict[str, bool] = {}

    if verb_id == "LOOK":
        pass

    elif verb_id == "GO":
        direction_id = resolve_phrase(remainder_str, "direction", canon, projection, fuzzy_threshold)
        args["direction"] = direction_id
        resolved["direction"] = direction_id is not None

    elif verb_id in {"TAKE", "DROP"}:
        item_id = resolve_phrase(remainder_str, "item", canon, projection, fuzzy_threshold)
        args["item"] = item_id
        resolved["item"] = item_id is not None

    elif verb_id == "TALK":
        npc_id = None
        topic_key = None

        if remainder_str:
            npc_id = resolve_phrase(remainder_str, "npc", canon, projection, fuzzy_threshold)

        topic_prepositions = {"про", "о", "об", "насчет"}

        topic_phrase: Optional[str] = None
        for index, token in enumerate(remainder_tokens):
            if token in topic_prepositions:
                topic_phrase = " ".join(remainder_tokens[index + 1:])
                break

        if topic_phrase:
            topic_key = resolve_phrase(topic_phrase, "topic", canon, projection, fuzzy_threshold)
        else:
            for token in remainder_tokens:
                if token in topic_prepositions:
                    continue
                topic_key = resolve_phrase(token, "topic", canon, projection, fuzzy_threshold)
                if topic_key is not None:
                    break

        args["npc"] = npc_id
        args["topic"] = topic_key
        resolved["npc"] = npc_id is not None
        resolved["topic"] = topic_key is not None

    elif verb_id == "USE":
        item_id = None
        target_id = None
        target_prepositions = {"на", "к", "с", "об"}

        preposition_index = -1
        for index, token in enumerate(remainder_tokens):
            if token in target_prepositions:
                preposition_index = index
                break

        if preposition_index >= 0:
            item_phrase = " ".join(remainder_tokens[:preposition_index])
            target_phrase = " ".join(remainder_tokens[preposition_index + 1:])

            if item_phrase:
                item_id = resolve_phrase(item_phrase, "item", canon, projection, fuzzy_threshold)
            if target_phrase:
                target_id = resolve_phrase(target_phrase, "target", canon, projection, fuzzy_threshold)
        else:
            if remainder_tokens:
                item_id = resolve_phrase(remainder_tokens[0], "item", canon, projection, fuzzy_threshold)

            if item_id is not None:
                target_phrase = " ".join(remainder_tokens[1:])
            else:
                target_phrase = remainder_str

            if target_phrase:
                target_id = resolve_phrase(target_phrase, "target", canon, projection, fuzzy_threshold)

        args["item"] = item_id
        args["target"] = target_id
        resolved["item"] = item_id is not None
        resolved["target"] = target_id is not None

    else:
        return Command(verb="UNKNOWN", args={}, resolved={}, raw=raw)

    return Command(verb=verb_id, args=args, resolved=resolved, raw=raw)