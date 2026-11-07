from __future__ import annotations

import re
from typing import Optional

from .models import Canon, Command
from .projection import ProjectionLookup

try:
    from rapidfuzz import fuzz
except ImportError:
    class fuzz:
        @staticmethod
        def ratio(s1: str, s2: str) -> float:
            if not s1 or not s2:
                return 0.0

            set1 = set(s1)
            set2 = set(s2)

            intersection = set1.intersection(set2)
            union = set1.union(set2)

            if not union:
                return 0.0

            return (len(intersection) / len(union)) * 100


def normalize(text: str) -> str:
    """
    Этап 1: нормализация.

    - нижний регистр;
    - ё -> е;
    - удаление знаков препинания, кроме дефисов внутри слов;
    - схлопывание пробелов.
    """

    text = text.lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _find_verb(
    tokens: list[str],
    canon: Canon,
    fuzzy_threshold: int,
) -> tuple[Optional[str], int]:
    """
    Этап 2: определение глагола.

    Сначала точный longest match по алиасам.
    Затем одно-токенный fuzzy match по первому слову.
    """

    if not tokens:
        return None, 0

    aliases: list[tuple[str, str]] = []
    for verb_id, verb in canon.verbs.items():
        for alias in verb.aliases:
            aliases.append((normalize(alias), verb_id))

    # Точный longest match.
    for alias_norm, verb_id in sorted(
        aliases,
        key=lambda item: len(item[0].split()),
        reverse=True,
    ):
        alias_tokens = alias_norm.split()

        if len(alias_tokens) <= len(tokens):
            if tokens[:len(alias_tokens)] == alias_tokens:
                return verb_id, len(alias_tokens)

    # Fuzzy fallback для одно-токенных алиасов.
    # Нужно, например, для "осмотреться" -> "осмотреть".
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
    """
    Этап 4: разрешение фразы в id.

    Типы:
      - direction
      - item
      - npc
      - topic
      - target
    """

    if not phrase:
        return None

    phrase_norm = normalize(phrase)
    candidates: list[tuple] = []

    if arg_type == "direction":
        for direction_id, direction in canon.directions.items():
            for alias in direction.aliases:
                if normalize(alias) == phrase_norm:
                    return direction_id

            candidates.append(
                (
                    direction_id,
                    normalize(direction.name),
                    tuple(direction.aliases),
                )
            )

    elif arg_type == "item":
        for item_id, item in canon.items.items():
            if normalize(item.name) == phrase_norm:
                return item_id

            candidates.append(
                (
                    item_id,
                    normalize(item.name),
                    tuple(),
                )
            )

    elif arg_type == "npc":
        for npc_id, npc in canon.npcs.items():
            if normalize(npc.name) == phrase_norm:
                return npc_id

            candidates.append(
                (
                    npc_id,
                    normalize(npc.name),
                    tuple(),
                )
            )

    elif arg_type == "topic":
        for npc in canon.npcs.values():
            for topic in npc.topics:
                if normalize(topic.key) == phrase_norm:
                    return topic.key

                candidates.append(
                    (
                        topic.key,
                        normalize(topic.key),
                        tuple(),
                    )
                )

    elif arg_type == "target":
        # Приоритет: use_targets -> npcs -> items.
        for target_id, use_target in canon.use_targets.items():
            if normalize(use_target.name) == phrase_norm:
                return target_id

            for alias in use_target.aliases:
                if normalize(alias) == phrase_norm:
                    return target_id

            candidates.append(
                (
                    target_id,
                    normalize(use_target.name),
                    tuple(use_target.aliases),
                    "use_target",
                )
            )

        for npc_id, npc in canon.npcs.items():
            if normalize(npc.name) == phrase_norm:
                return npc_id

            candidates.append(
                (
                    npc_id,
                    normalize(npc.name),
                    tuple(),
                    "npc",
                )
            )

        for item_id, item in canon.items.items():
            if normalize(item.name) == phrase_norm:
                return item_id

            candidates.append(
                (
                    item_id,
                    normalize(item.name),
                    tuple(),
                    "item",
                )
            )

    else:
        return None

    # 2. Fuzzy по всей фразе.
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

    # 4. Фраза цели: longest substring match.
    if arg_type in {"item", "npc", "target"}:
        phrase_tokens = phrase_norm.split()

        for length in range(len(phrase_tokens), 0, -1):
            for start in range(len(phrase_tokens) - length + 1):
                subphrase = " ".join(phrase_tokens[start:start + length])

                for candidate in candidates:
                    candidate_id = candidate[0]
                    candidate_name = candidate[1]
                    candidate_aliases = candidate[2] if len(candidate) > 2 else tuple()

                    score = fuzz.ratio(subphrase, candidate_name)
                    if score >= fuzzy_threshold:
                        return candidate_id

                    for alias in candidate_aliases:
                        alias_norm = normalize(alias)
                        score = fuzz.ratio(subphrase, alias_norm)
                        if score >= fuzzy_threshold:
                            return candidate_id

    return None


def parse(
    raw: str,
    canon: Canon,
    projection: Optional[ProjectionLookup] = None,
    fuzzy_threshold: int = 85,
) -> Command:
    """
    Основной вход парсера.

    Возвращает Command.
    Не проверяет предусловия и не формирует коды причин — это движок.
    """

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

    # LOOK: аргументы игнорируются.
    if verb_id == "LOOK":
        pass

    # GO: direction.
    elif verb_id == "GO":
        direction_id = resolve_phrase(
            remainder_str,
            "direction",
            canon,
            projection,
            fuzzy_threshold,
        )

        args["direction"] = direction_id
        resolved["direction"] = direction_id is not None

    # TAKE / DROP: item.
    elif verb_id in {"TAKE", "DROP"}:
        item_id = resolve_phrase(
            remainder_str,
            "item",
            canon,
            projection,
            fuzzy_threshold,
        )

        args["item"] = item_id
        resolved["item"] = item_id is not None

    # TALK: npc + topic.
    elif verb_id == "TALK":
        npc_id = None
        topic_key = None

        if remainder_str:
            npc_id = resolve_phrase(
                remainder_str,
                "npc",
                canon,
                projection,
                fuzzy_threshold,
            )

        topic_prepositions = {"про", "о", "об", "насчет"}

        topic_phrase: Optional[str] = None
        for index, token in enumerate(remainder_tokens):
            if token in topic_prepositions:
                topic_phrase = " ".join(remainder_tokens[index + 1:])
                break

        if topic_phrase:
            topic_key = resolve_phrase(
                topic_phrase,
                "topic",
                canon,
                projection,
                fuzzy_threshold,
            )
        else:
            for token in remainder_tokens:
                if token in topic_prepositions:
                    continue

                topic_key = resolve_phrase(
                    token,
                    "topic",
                    canon,
                    projection,
                    fuzzy_threshold,
                )

                if topic_key is not None:
                    break

        args["npc"] = npc_id
        args["topic"] = topic_key
        resolved["npc"] = npc_id is not None
        resolved["topic"] = topic_key is not None

    # USE: item + target.
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
                item_id = resolve_phrase(
                    item_phrase,
                    "item",
                    canon,
                    projection,
                    fuzzy_threshold,
                )

            if target_phrase:
                target_id = resolve_phrase(
                    target_phrase,
                    "target",
                    canon,
                    projection,
                    fuzzy_threshold,
                )

        else:
            # Без предлога: первый токен пробуем разрешить как предмет.
            # Если не получилось — он остаётся частью фразы цели.
            if remainder_tokens:
                item_id = resolve_phrase(
                    remainder_tokens[0],
                    "item",
                    canon,
                    projection,
                    fuzzy_threshold,
                )

            if item_id is not None:
                target_phrase = " ".join(remainder_tokens[1:])
            else:
                target_phrase = remainder_str

            if target_phrase:
                target_id = resolve_phrase(
                    target_phrase,
                    "target",
                    canon,
                    projection,
                    fuzzy_threshold,
                )

        args["item"] = item_id
        args["target"] = target_id
        resolved["item"] = item_id is not None
        resolved["target"] = target_id is not None

    else:
        return Command(verb="UNKNOWN", args={}, resolved={}, raw=raw)

    return Command(
        verb=verb_id,
        args=args,
        resolved=resolved,
        raw=raw,
    )