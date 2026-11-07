# debug_parser.py
import inspect
from anvil_and_ink.loaders import load_canon
from anvil_and_ink.parser import resolve_phrase, parse, normalize

canon = load_canon("canon.yaml")

# 1. Есть ли цикл по словам в resolve_phrase?
src = inspect.getsource(resolve_phrase)
has_word_loop = "for word in norm_name.split()" in src
print(f"[1] Цикл по словам в resolve_phrase: {'ЕСТЬ' if has_word_loop else 'НЕТ'}")

# 2. rapidfuzz установлен?
try:
    from rapidfuzz import fuzz
    score = fuzz.ratio("раненым", "раненый")
    print(f"[2] rapidfuzz: ЕСТЬ, ratio('раненым','раненый') = {score:.1f}")
except ImportError:
    print("[2] rapidfuzz: НЕТ (fallback set-based, ratio будет ниже 85)")

# 3. Что возвращает resolve_phrase для изолированного слова?
r1 = resolve_phrase("раненым", "npc", canon)
print(f"[3] resolve_phrase('раненым', 'npc') = {r1}")

# 4. Что возвращает resolve_phrase для полной remainder_str TALK?
r2 = resolve_phrase("с раненым про фильтры", "npc", canon)
print(f"[4] resolve_phrase('с раненым про фильтры', 'npc') = {r2}")

# 5. Что возвращает parse для полной команды?
cmd = parse("поговорить с раненым про фильтры", canon)
print(f"[5] parse: verb={cmd.verb}, npc={cmd.args.get('npc')}, topic={cmd.args.get('topic')}")

# 6. Имена NPC в каноне
for nid, npc in canon.npcs.items():
    print(f"[6] NPC {nid}: name={npc.name!r}, normalized={normalize(npc.name)!r}")