from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Set

from .models import Canon, Manifest


# Лимиты длины. См. D-65: нижний порог — предмет подтверждения.
MIN_LENGTH = 5
MAX_LENGTH = 1000

# D-13: порог валидатора выше парсерного.
FUZZY_THRESHOLD = 90

# Числительные прописью 1–10 (D-37). Больше 10 — игнорируются.
_NUMERAL_WORDS = {
    "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
}

# Служебные слова, выдающие механику (утечка).
_SERVICE_WORDS = ("флаг", "состояние", "outcome", "валидатор", "манифест")

# Усечённый набор русских окончаний для стемминга (эвристика, не морфология).
_ENDINGS = ("ов", "ев", "ой", "ый", "ая", "ое", "ом", "ем", "ах", "ях", "у", "ю", "а", "я", "ы", "и", "е")


@dataclass(frozen=True)
class Reason:
    code: str      # машинный: для лога/тестов/агрегации
    message: str   # человеческая фраза: для заметки ретрая


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: Optional[Reason]


# --- Нечёткое сравнение ---------------------------------------------------

try:
    from rapidfuzz import fuzz as _rf

    def _fuzz_ratio(a: str, b: str) -> float:
        return _rf.ratio(a, b)

except ImportError:
    def _levenshtein(s1: str, s2: str) -> int:
        if len(s1) < len(s2):
            return _levenshtein(s2, s1)
        if not s2:
            return len(s1)
        prev = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
            prev = curr
        return prev[-1]

    def _fuzz_ratio(a: str, b: str) -> float:
        if not a and not b:
            return 100.0
        if not a or not b:
            return 0.0
        dist = _levenshtein(a, b)
        return (1.0 - dist / max(len(a), len(b))) * 100.0


# --- Валидатор -------------------------------------------------------------


class Validator:
    """
    Единственный барьер между галлюцинацией и игроком.

    Сверяет ответ LLM с манифестом (тем, что нарратор видел), а не с «истиной».
    Консервативен: лучше отклонить корректный ответ, чем пропустить галлюцинацию.
    """

    def __init__(self, canon: Canon):
        self.canon = canon
        self._canon_names = self._collect_canon_names(canon)
        self._mechanics_terms = self._collect_mechanics_terms(canon)

    @staticmethod
    def _collect_canon_names(canon: Canon) -> List[str]:
        # Глобальный список имён: локации, NPC, типы предметов, use_targets.
        # Направления намеренно не входят (спецификация §5).
        names: Set[str] = set()
        for loc in canon.locations.values():
            if loc.name:
                names.add(_normalize(loc.name))
        for npc in canon.npcs.values():
            if npc.name:
                names.add(_normalize(npc.name))
        for item in canon.items.values():
            if item.name:
                names.add(_normalize(item.name))
        for ut in canon.use_targets.values():
            if ut.name:
                names.add(_normalize(ut.name))
        return sorted(names)

    @staticmethod
    def _collect_mechanics_terms(canon: Canon) -> Set[str]:
        terms: Set[str] = set()
        for flag_id in canon.flags:
            terms.add(flag_id.lower())
        for code in canon.reason_codes:
            terms.add(code.lower())
        for word in _SERVICE_WORDS:
            terms.add(word.lower())
        return terms

    def validate(self, response: str, manifest: Manifest) -> Verdict:
        # Порядок проверок жёсткий (D-40): первая сработавшая возвращает reject.

        # 1. Пустота.
        stripped = response.strip()
        if not stripped:
            return Verdict(False, Reason("empty", "ответ нарратора пуст"))

        # 2. Длина. Количество предложений не проверяется (D-41).
        length = len(stripped)
        if length < MIN_LENGTH:
            return Verdict(False, Reason("too_short", "ответ слишком короткий"))
        if length > MAX_LENGTH:
            return Verdict(False, Reason("too_long", "ответ слишком длинный"))

        text = _normalize(stripped)

        # 3. Утечка механики.
        for term in self._mechanics_terms:
            if term and term in text:
                return Verdict(
                    False,
                    Reason("mechanics_leak", f"в ответе упомянут служебный термин «{term}»"),
                )

        # 4. Числа.
        number_reason = self._check_numbers(text, manifest)
        if number_reason is not None:
            return Verdict(False, number_reason)

        # 5. Имена.
        name_reason = self._check_names(text, manifest)
        if name_reason is not None:
            return Verdict(False, name_reason)

        return Verdict(True, None)

    def _check_numbers(self, text: str, manifest: Manifest) -> Optional[Reason]:
        allowed = manifest.allowed_numbers

        # Цифры строго.
        for match in re.finditer(r"\d+", text):
            n = int(match.group())
            if n not in allowed:
                return Reason(
                    "unknown_number",
                    f"в ответе упомянуто число {n}, которого не было в произошедшем",
                )

        # Числительные прописью 1–10.
        for word in _split_words(text):
            if word in _NUMERAL_WORDS:
                n = _NUMERAL_WORDS[word]
                if n not in allowed:
                    return Reason(
                        "unknown_number",
                        f"в ответе упомянуто число {n}, которого не было в произошедшем",
                    )

        return None

    def _check_names(self, text: str, manifest: Manifest) -> Optional[Reason]:
        allowed = manifest.allowed_names
        words = _split_words(text)
        if not words:
            return None

        # Разрешённые имена «покрывают» свои слова в ответе (D-66),
        # чтобы под-слова многословного имени не считались отдельными сущностями.
        # Длинные имена обрабатываем первыми.
        covered: Set[int] = set()
        for name in sorted(allowed, key=lambda n: len(n.split()), reverse=True):
            name_words = name.split()
            n = len(name_words)
            if n == 1:
                for i, w in enumerate(words):
                    if _word_matches(name_words[0], w):
                        covered.add(i)
            else:
                for i in range(len(words) - n + 1):
                    if _phrase_matches(name_words, words[i:i + n]):
                        covered.update(range(i, i + n))

        # Канонные имена вне манифеста ищем только среди непокрытых слов.
        for name in self._canon_names:
            if name in allowed:
                continue
            name_words = name.split()
            n = len(name_words)
            if n == 1:
                for i, w in enumerate(words):
                    if i in covered:
                        continue
                    if _word_matches(name_words[0], w):
                        return Reason(
                            "unknown_name",
                            f"в ответе упомянуто имя «{name}», которого не было в контексте",
                        )
            else:
                for i in range(len(words) - n + 1):
                    window = range(i, i + n)
                    if all(j in covered for j in window):
                        continue
                    if _phrase_matches(name_words, words[i:i + n]):
                        return Reason(
                            "unknown_name",
                            f"в ответе упомянуто имя «{name}», которого не было в контексте",
                        )

        return None


# --- Вспомогательные функции ----------------------------------------------


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def _split_words(text: str) -> List[str]:
    return re.findall(r"\w+", text)


def _stem(word: str) -> str:
    for ending in sorted(_ENDINGS, key=len, reverse=True):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _word_matches(name_word: str, text_word: str) -> bool:
    # Точное совпадение.
    if name_word == text_word:
        return True
    # Стемминг.
    name_stem = _stem(name_word)
    if name_stem and name_stem == _stem(text_word):
        return True
    # Fuzzy 90.
    if _fuzz_ratio(name_word, text_word) >= FUZZY_THRESHOLD:
        return True
    return False


def _phrase_matches(name_words: List[str], window_words: List[str]) -> bool:
    if len(name_words) != len(window_words):
        return False
    return all(_word_matches(nw, ww) for nw, ww in zip(name_words, window_words))