from __future__ import annotations

import os
from typing import Callable, Optional

from .models import TurnResult


SAVE_PATH = "./save.jsonl"
META_COMMANDS = {"save", "load", "history", "quit"}
POST_WIN_ALLOWED = {"save", "history", "quit"}

PROMPT = "> "
WIN_MESSAGE = "Игра пройдена."
WIN_INPUT_HINT = "Игра пройдена. Доступны: save, history, quit."
ABORTED_MESSAGE = (
    "Сессия прервана из-за внутренней ошибки. "
    "Прогресс сохранён до последнего успешного хода."
)
LOAD_MISSING_MESSAGE = "Файл сохранения не найден."
HISTORY_EMPTY_MESSAGE = "История пуста."
SAVE_CONFIRM = "Файл сохранения уже существует. Перезаписать? (y/n)"
LOAD_CONFIRM = "Текущая сессия будет потеряна. Продолжить загрузку? (y/n)"


class GameInterface:
    """
    Самый тонкий слой. read → session.step() → write.

    Не знает про rung, проекцию, флаги — только про TurnResult.
    """

    def __init__(
        self,
        session,
        *,
        reader: Callable[[], str],
        writer: Callable[[str], None],
        confirm: Callable[[str], bool],
        session_loader: Callable[[str], object],
        session_starter: Callable[[], object],
        save_path: str = SAVE_PATH,
        file_exists: Optional[Callable[[str], bool]] = None,
    ):
        self._session = session
        self._reader = reader
        self._writer = writer
        self._confirm = confirm
        self._session_loader = session_loader
        self._session_starter = session_starter
        self._save_path = save_path
        self._file_exists = file_exists if file_exists is not None else _default_file_exists

    def run(self) -> None:
        """Основной цикл. Возвращает управление только при quit / aborted / EOF."""

        game_finished = False

        while True:
            self._writer(PROMPT)

            try:
                raw = self._reader()
            except EOFError:
                break

            stripped = raw.strip().lower()

            # Мета-команды распознаются регистронезависимо и до сессии (D-51).
            if stripped in META_COMMANDS:
                action = self._handle_meta(stripped, game_finished)
                if action == "quit":
                    break
                if action == "aborted":
                    break
                continue

            # После победы блокируем игровой ввод (D-74).
            if game_finished:
                self._writer(WIN_INPUT_HINT)
                continue

            result: TurnResult = self._session.step(raw)
            self._writer(result.text)

            if result.aborted:
                self._writer(ABORTED_MESSAGE)
                break

            if result.game_finished:
                game_finished = True
                self._writer(WIN_MESSAGE)
                # Не выходим автоматически (D-50). Цикл продолжится,
                # но игровой ввод будет блокироваться.

    def _handle_meta(self, command: str, game_finished: bool) -> str:
        """
        Обработка мета-команды.
        Возвращает 'continue' | 'quit' | 'aborted'.
        """

        # После победы разрешены только save / history / quit (D-74).
        if game_finished and command not in POST_WIN_ALLOWED:
            self._writer(WIN_INPUT_HINT)
            return "continue"

        if command == "save":
            return self._do_save()
        if command == "load":
            return self._do_load()
        if command == "history":
            return self._do_history()
        if command == "quit":
            return "quit"
        return "continue"

    def _do_save(self) -> str:
        if self._file_exists(self._save_path):
            if not self._confirm(SAVE_CONFIRM):
                return "continue"
        self._session.save(self._save_path)
        return "continue"

    def _do_load(self) -> str:
        if not self._file_exists(self._save_path):
            self._writer(LOAD_MISSING_MESSAGE)
            return "continue"

        # Подтверждение, если текущая сессия уже имеет ходы.
        # session.history() — публичный метод.
        if len(self._session.history()) > 0:
            if not self._confirm(LOAD_CONFIRM):
                return "continue"

        try:
            new_session = self._session_loader(self._save_path)
        except Exception:
            self._writer(LOAD_MISSING_MESSAGE)
            return "continue"

        self._session = new_session
        return "continue"

    def _do_history(self) -> str:
        history = self._session.history()
        if not history:
            self._writer(HISTORY_EMPTY_MESSAGE)
            return "continue"
        for index, text in enumerate(history, start=1):
            self._writer(f"{index}. {text}")
        return "continue"


def _default_file_exists(path: str) -> bool:
    return os.path.exists(path)