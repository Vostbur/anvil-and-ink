# debug_golden.py
from anvil_and_ink.loaders import load_canon
from anvil_and_ink.session import Session, SessionConfig
from anvil_and_ink.parser import parse
from anvil_and_ink.engine import apply_command

canon = load_canon("canon.yaml")

# (команда, ожидаемый reason_code по integration.md)
GOLDEN = [
    ("осмотреться",                      "ok"),
    ("идти на восток",                   "ok"),
    ("осмотреться",                      "ok"),
    ("поговорить с раненым про фильтры", "ok"),
    ("поговорить с торговцем про код",   "condition_not_met"),
    ("взять фонарь",                     "ok"),
    ("бросить фонарь",                   "ok"),
    ("идти на запад",                    "ok"),
    ("взять флягу",                      "ok"),
    ("идти на восток",                   "ok"),
    ("использовать флягу на раненого",   "ok"),
    ("взять фильтр",                     "ok"),
    ("поговорить с торговцем про код",   "ok"),
    ("использовать код на дверь",        "ok"),
    ("идти на север",                    "ok"),
]

config = SessionConfig(mode="no-llm", canon_path="canon.yaml", templates_path="templates.yaml")
session = Session.start(config)

first_break = None

for i, (cmd_text, expected) in enumerate(GOLDEN, start=1):
    # Инспектируем движок НАПРЯМУЮ (apply_command чистый, не mutate состояние).
    command = parse(cmd_text, canon, session._lookup)
    outcome = apply_command(command, session._lookup, canon)

    # Реальный шаг сессии (меняет состояние).
    result = session.step(cmd_text)

    mark = "OK " if outcome.reason_code == expected else ">>>"
    if outcome.reason_code != expected and first_break is None:
        first_break = i

    print(f"\n{mark} Ход {i}: {cmd_text!r}")
    print(f"    verb={command.verb} args={dict(command.args)} resolved={dict(command.resolved)}")
    print(f"    outcome: result={outcome.result} reason={outcome.reason_code} (ожидали {expected})")
    print(f"    applied={list(outcome.applied)}")
    print(f"    response_text={outcome.response_text!r}")
    print(f"    state: loc={session._state.location} inv={dict(session._state.inventory)}")
    print(f"    flags={dict(session._state.flags)}")

print("\n" + "=" * 60)
if first_break is None:
    print("Все reason_code совпали. Проблема НЕ в движке/каноне на уровнях reason_code.")
    print("Значит, расхождение в самом эталоне EXPECTED_FINAL_STATE или в save/load.")
else:
    print(f"ПЕРВЫЙ РАЗРЫВ на ходе {first_break}. См. строку с >>> выше.")