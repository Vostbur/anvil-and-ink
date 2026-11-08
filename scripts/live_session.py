"""
Живая сессия (шаг 17). Обёртка над сессией с транскриптом и диагностикой.

Запуск:
    python scripts/live_session.py --mode llm
    python scripts/live_session.py --mode no-llm     # проверка парсера/сложности без LLM

Требования для --mode llm:
    - запущен `ollama serve`
    - доступна модель (по умолчанию qwen2.5:7b): `ollama pull qwen2.5:7b`
    - переменные окружения (опционально):
        ANVIL_LLM_MODEL=qwen2.5:7b
        ANVIL_LLM_BASE_URL=http://localhost:11434
        ANVIL_LLM_TIMEOUT=10        # поднять до 30–60 на CPU
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Корень проекта в sys.path (запуск из любого каталога).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anvil_and_ink.session import Session, SessionConfig  # noqa: E402


def check_ollama(base_url: str) -> bool:
    """GET /api/tags — жив ли сервер. Не падает, если нет."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=2) as r:
            r.read()
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Live session (шаг 17)")
    parser.add_argument("--mode", choices=["llm", "no-llm", "broken-llm"], default="llm")
    parser.add_argument("--model", default=None, help="Переопределить ANVIL_LLM_MODEL")
    parser.add_argument("--base-url", default=None, help="Переопределить ANVIL_LLM_BASE_URL")
    parser.add_argument("--timeout", type=float, default=None, help="Таймаут LLM, сек")
    parser.add_argument("--canon", default="canon.yaml")
    parser.add_argument("--templates", default="templates.yaml")
    args = parser.parse_args()

    if args.model:
        import os
        os.environ["ANVIL_LLM_MODEL"] = args.model
    if args.base_url:
        import os
        os.environ["ANVIL_LLM_BASE_URL"] = args.base_url
    if args.timeout is not None:
        import os
        os.environ["ANVIL_LLM_TIMEOUT"] = str(args.timeout)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    transcript_path = ROOT / f"transcript_{stamp}.txt"
    save_path = ROOT / f"save_{stamp}.jsonl"
    diagnostic_path = ROOT / f"diagnostic_{stamp}.jsonl"

    config = SessionConfig(mode=args.mode, canon_path=str(ROOT / args.canon), templates_path=str(ROOT / args.templates))

    # Предупреждение, если Ollama не отвечает (для llm-режима).
    if args.mode == "llm":
        base = args.base_url or "http://localhost:11434"
        if not check_ollama(base):
            print(f"ВНИМАНИЕ: Ollama не отвечает на {base}.", file=sys.stderr)
            print("Запустите `ollama serve` и `ollama pull qwen2.5:7b`.", file=sys.stderr)
            print("Игра всё равно пойдёт — лестница деградирует до шаблонов (rung=3).", file=sys.stderr)
            print()

    session = Session.start(config)

    transcript = open(transcript_path, "w", encoding="utf-8")
    start = time.time()

    def log(kind: str, text: str) -> None:
        elapsed = time.time() - start
        line = f"[{elapsed:7.1f}s] {kind}: {text}\n"
        transcript.write(line)
        transcript.flush()

    print(f"Транскрипт:  {transcript_path}")
    print(f"Сейв:        {save_path}")
    print(f"Диагностика: {diagnostic_path}")
    print(f"Режим:       {args.mode}")
    print()
    print("Текстовая игра. Вводите команды обычным языком.")
    print("Если застрянете — скажите, ведущий запишет, но не поможет.")
    print("Игра короткая, минут на 10–20. Введите quit для выхода.")
    print()

    log("СИСТЕМА", f"старт, режим={args.mode}")

    try:
        while True:
            try:
                raw = input("> ")
            except EOFError:
                log("СИСТЕМА", "EOF, выход")
                break

            log("ИГРОК", raw)

            if raw.strip().lower() == "quit":
                log("СИСТЕМА", "quit")
                break

            result = session.step(raw)

            log("ИГРА", result.text)
            print(result.text)

            # Инкрементальный сейв после каждого хода (кроме aborted).
            if not result.aborted:
                session.save(str(save_path))

            if result.aborted:
                log("СИСТЕМА", f"ABORTED: {result.abort_reason}")
                print("Сессия прервана из-за внутренней ошибки.")
                break

            if result.game_finished:
                log("СИСТЕМА", "GAME FINISHED")
                print("Игра пройдена.")
                print("Доступны: quit. (save/history уже записаны)")
                # Не выходим автоматически (D-50), но игровой ввод больше не имеет смысла.
                # Ждём quit.
    finally:
        transcript.close()

        # Диагностика в конце (не инкрементально — она не критична для восстановления).
        with open(diagnostic_path, "w", encoding="utf-8") as f:
            for diag in session._diagnostics:
                f.write(json.dumps(diag, ensure_ascii=False) + "\n")

        # Финальный сейв (на случай, если последний ход был aborted и save не отработал).
        try:
            session.save(str(save_path))
        except Exception as e:
            print(f"Не удалось сохранить финальный сейв: {e}", file=sys.stderr)

    # Сводка для ведущего.
    presentations = [e for e in session._events if e.kind == "presentation"]
    turns = [e for e in session._events if e.kind == "turn"]
    rungs = [p.rung for p in presentations]
    unknown = sum(1 for r in rungs if r is None)
    llm_rung1 = sum(1 for r in rungs if r == 1)
    template_rung3 = sum(1 for r in rungs if r == 3)
    stub_rung4 = sum(1 for r in rungs if r == 4)

    print()
    print("=" * 60)
    print("СВОДКА (для ведущего, не показывать игроку до пост-интервью)")
    print(f"  Ходов:            {len(turns)}")
    print(f"  UNKNOWN (rung=null): {unknown}")
    print(f"  LLM (rung=1):     {llm_rung1}")
    print(f"  Шаблон (rung=3):  {template_rung3}")
    print(f"  Заглушка (rung=4):{stub_rung4}  {'← КРИТИЧНО' if stub_rung4 else ''}")
    print(f"  Победа:           {session._game_finished}")
    print(f"  Прервана:         {session._aborted}")
    print(f"  Артефакты:        {transcript_path.name}, {save_path.name}, {diagnostic_path.name}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())