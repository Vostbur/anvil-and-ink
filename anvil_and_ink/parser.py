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
            return (len(intersection) / len(union)) * 100 if union else 0.0

def normalize(text: str) -> str:
    """Этап 1: Нормализация строки."""
    text = text.lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def longest_match_aliases(aliases: list[str], tokens: list[str]) -> Optional[tuple[str, int]]:
    """Находит самый длинный алиас, совпадающий с началом токенов."""
    sorted_aliases = sorted(aliases, key=len, reverse=True)
    for alias in sorted_aliases:
        alias_tokens = alias.split()
        if len(alias_tokens) <= len(tokens):
            if tokens[:len(alias_tokens)] == alias_tokens:
                return (alias, len(alias_tokens))
    return None

def resolve_phrase(phrase: str, arg_type: str, canon: Canon, projection: Optional[ProjectionLookup] = None, fuzzy_threshold: int = 85) -> Optional[str]:
    """Этап 4: Разрешение фразы в id."""
    if not phrase:
        return None
    phrase_norm = normalize(phrase)
    candidates = []
    
    if arg_type == "direction":
        for did, d in canon.directions.items():
            for alias in d.aliases:
                if normalize(alias) == phrase_norm:
                    return did
            candidates.append((did, normalize(d.name), d.aliases))
    elif arg_type == "item":
        for iid, item in canon.items.items():
            if normalize(item.name) == phrase_norm:
                return iid
            candidates.append((iid, normalize(item.name), []))
    elif arg_type == "npc":
        for nid, npc in canon.npcs.items():
            if normalize(npc.name) == phrase_norm:
                return nid
            candidates.append((nid, normalize(npc.name), []))
    elif arg_type == "topic":
        for nid, npc in canon.npcs.items():
            for topic in npc.topics:
                if normalize(topic.key) == phrase_norm:
                    return topic.key
                candidates.append((topic.key, normalize(topic.key), []))
    elif arg_type == "target":
        for uid, ut in canon.use_targets.items():
            if normalize(ut.name) == phrase_norm:
                return uid
            for alias in ut.aliases:
                if normalize(alias) == phrase_norm:
                    return uid
            candidates.append((uid, normalize(ut.name), ut.aliases, "use_target"))
        for nid, npc in canon.npcs.items():
            if normalize(npc.name) == phrase_norm:
                return nid
            candidates.append((nid, normalize(npc.name), [], "npc"))
        for iid, item in canon.items.items():
            if normalize(item.name) == phrase_norm:
                return iid
            candidates.append((iid, normalize(item.name), [], "item"))
    
    best_match = None
    best_score = 0
    for candidate in candidates:
        cid = candidate[0]
        cname = candidate[1]
        calises = candidate[2] if len(candidate) > 2 else []
        score = fuzz.ratio(phrase_norm, cname)
        if score > best_score:
            best_score = score
            best_match = cid
        for alias in calises:
            score = fuzz.ratio(phrase_norm, normalize(alias))
            if score > best_score:
                best_score = score
                best_match = cid
    
    if best_score >= fuzzy_threshold:
        return best_match
    
    if arg_type in ("item", "npc", "target"):
        phrase_tokens = phrase_norm.split()
        for length in range(len(phrase_tokens), 0, -1):
            for start in range(len(phrase_tokens) - length + 1):
                subphrase = " ".join(phrase_tokens[start:start+length])
                for candidate in candidates:
                    cid = candidate[0]
                    cname = candidate[1]
                    calises = candidate[2] if len(candidate) > 2 else []
                    score = fuzz.ratio(subphrase, cname)
                    if score >= fuzzy_threshold:
                        return cid
                    for alias in calises:
                        score = fuzz.ratio(subphrase, normalize(alias))
                        if score >= fuzzy_threshold:
                            return cid
    return None

def parse(raw: str, canon: Canon, projection: Optional[ProjectionLookup] = None, fuzzy_threshold: int = 85) -> Command:
    """Основная функция парсера."""
    normalized = normalize(raw)
    tokens = normalized.split()
    if not tokens:
        return Command(verb="UNKNOWN", args={}, resolved={}, raw=raw)
    
    verb_id = None
    verb_tokens_count = 0
    all_aliases = []
    for vid, v in canon.verbs.items():
        for alias in v.aliases:
            all_aliases.append((alias, vid))
    
    match = longest_match_aliases([a[0] for a in all_aliases], tokens)
    if match:
        alias, count = match
        for a, v in all_aliases:
            if a == alias:
                verb_id = v
                verb_tokens_count = count
                break
    
    if not verb_id:
        return Command(verb="UNKNOWN", args={}, resolved={}, raw=raw)
    
    remainder_tokens = tokens[verb_tokens_count:]
    remainder_str = " ".join(remainder_tokens)
    args = {}
    resolved = {}
    
    if verb_id == "LOOK":
        pass
    elif verb_id == "GO":
        dir_id = resolve_phrase(remainder_str, "direction", canon, projection, fuzzy_threshold)
        args["direction"] = dir_id
        resolved["direction"] = dir_id is not None
    elif verb_id in ("TAKE", "DROP"):
        item_id = resolve_phrase(remainder_str, "item", canon, projection, fuzzy_threshold)
        args["item"] = item_id
        resolved["item"] = item_id is not None
    elif verb_id == "TALK":
        npc_id = None
        topic_key = None
        for nid, npc in canon.npcs.items():
            npc_name_norm = normalize(npc.name)
            if npc_name_norm in remainder_str:
                npc_id = nid
                break
            if fuzz.ratio(npc_name_norm, remainder_str) >= fuzzy_threshold:
                npc_id = nid
                break
        prepositions = ["про", "о", "об", "насчет"]
        topic_phrase = None
        for prep in prepositions:
            if prep in remainder_tokens:
                prep_idx = remainder_tokens.index(prep)
                topic_phrase = " ".join(remainder_tokens[prep_idx+1:])
                break
        if topic_phrase:
            topic_key = resolve_phrase(topic_phrase, "topic", canon, projection, fuzzy_threshold)
        else:
            for token in remainder_tokens:
                topic_key = resolve_phrase(token, "topic", canon, projection, fuzzy_threshold)
                if topic_key:
                    break
        args["npc"] = npc_id
        args["topic"] = topic_key
        resolved["npc"] = npc_id is not None
        resolved["topic"] = topic_key is not None
    elif verb_id == "USE":
        item_id = None
        target_id = None
        prepositions = ["на", "к", "с", "об"]
        prep_idx = -1
        for prep in prepositions:
            if prep in remainder_tokens:
                prep_idx = remainder_tokens.index(prep)
                break
        if prep_idx >= 0:
            item_phrase = " ".join(remainder_tokens[:prep_idx])
            target_phrase = " ".join(remainder_tokens[prep_idx+1:])
            item_id = resolve_phrase(item_phrase, "item", canon, projection, fuzzy_threshold)
            target_id = resolve_phrase(target_phrase, "target", canon, projection, fuzzy_threshold)
        else:
            if remainder_tokens:
                item_phrase = remainder_tokens[0]
                target_phrase = " ".join(remainder_tokens[1:])
                item_id = resolve_phrase(item_phrase, "item", canon, projection, fuzzy_threshold)
                target_id = resolve_phrase(target_phrase, "target", canon, projection, fuzzy_threshold)
        args["item"] = item_id
        args["target"] = target_id
        resolved["item"] = item_id is not None
        resolved["target"] = target_id is not None
    
    return Command(verb=verb_id, args=args, resolved=resolved, raw=raw)