import pytest

from anvil_and_ink.interface import (
    ABORTED_MESSAGE,
    GameInterface,
    HISTORY_EMPTY_MESSAGE,
    LOAD_CONFIRM,
    LOAD_MISSING_MESSAGE,
    SAVE_CONFIRM,
    SAVE_PATH,
    WIN_INPUT_HINT,
    WIN_MESSAGE,
)
from anvil_and_ink.models import TurnResult


# ---------------------------------------------------------------------------
# Фейки
# ---------------------------------------------------------------------------


class FakeSession:
    def __init__(self, results, history=None):
        self._results = list(results)
        self._step_calls = []
        self._save_calls = []
        self._history_calls = 0
        self._history = list(history) if history is not None else []

    def step(self, raw):
        self._step_calls.append(raw)
        if self._results:
            return self._results.pop(0)
        return TurnResult(text="fallback", game_finished=False, aborted=False)

    def save(self, path):
        self._save_calls.append(path)

    def history(self):
        self._history_calls += 1
        return list(self._history)


class FakeReader:
    def __init__(self, inputs):
        self._inputs = list(inputs)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self._inputs:
            raise EOFError
        return self._inputs.pop(0)


class FakeWriter:
    def __init__(self):
        self.outputs = []

    def __call__(self, text):
        self.outputs.append(text)


class FakeConfirm:
    def __init__(self, answers):
        self._answers = list(answers)
        self.questions = []

    def __call__(self, question):
        self.questions.append(question)
        if not self._answers:
            return False
        return self._answers.pop(0)


def make_interface(session, *, reader, writer, confirm,
                   loader=None, starter=None, file_exists=None):
    return GameInterface(
        session,
        reader=reader,
        writer=writer,
        confirm=confirm,
        session_loader=loader if loader is not None else (lambda p: session),
        session_starter=starter if starter is not None else (lambda: session),
        file_exists=file_exists,
    )


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_u_1_game_input_goes_to_step():
    session = FakeSession([TurnResult("ответ", False, False)])
    reader = FakeReader(["взять флягу", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(session, reader=reader, writer=writer, confirm=confirm)

    iface.run()

    assert session._step_calls == ["взять флягу"]
    assert "ответ" in writer.outputs


def test_u_2_save_calls_session_save():
    session = FakeSession([])
    reader = FakeReader(["save", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(
        session, reader=reader, writer=writer, confirm=confirm,
        file_exists=lambda p: False,
    )

    iface.run()

    assert session._save_calls == [SAVE_PATH]
    assert session._step_calls == []


def test_u_3_save_existing_file_declined():
    session = FakeSession([])
    reader = FakeReader(["save", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([False])
    iface = make_interface(
        session, reader=reader, writer=writer, confirm=confirm,
        file_exists=lambda p: True,
    )

    iface.run()

    assert session._save_calls == []
    assert confirm.questions == [SAVE_CONFIRM]


def test_u_4_load_creates_new_session():
    original = FakeSession([], history=["старый ход"])
    new_session = FakeSession([])
    reader = FakeReader(["load", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([True])

    def loader(path):
        assert path == SAVE_PATH
        return new_session

    iface = make_interface(
        original, reader=reader, writer=writer, confirm=confirm,
        loader=loader, file_exists=lambda p: True,
    )

    iface.run()

    assert iface._session is new_session
    assert confirm.questions == [LOAD_CONFIRM]


def test_u_5_load_missing_file():
    session = FakeSession([])
    reader = FakeReader(["load", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(
        session, reader=reader, writer=writer, confirm=confirm,
        loader=lambda p: FakeSession([]),
        file_exists=lambda p: False,
    )

    iface.run()

    assert LOAD_MISSING_MESSAGE in writer.outputs
    assert iface._session is session
    assert confirm.questions == []


def test_u_6_history_shows_final_texts():
    session = FakeSession([], history=["текст 1", "текст 2"])
    reader = FakeReader(["history", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(session, reader=reader, writer=writer, confirm=confirm)

    iface.run()

    assert any("1. текст 1" in out for out in writer.outputs)
    assert any("2. текст 2" in out for out in writer.outputs)
    assert session._history_calls >= 1


def test_u_6b_history_empty_shows_message():
    session = FakeSession([], history=[])
    reader = FakeReader(["history", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(session, reader=reader, writer=writer, confirm=confirm)

    iface.run()

    assert HISTORY_EMPTY_MESSAGE in writer.outputs


def test_u_7_quit_exits_without_step():
    session = FakeSession([])
    reader = FakeReader(["quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(session, reader=reader, writer=writer, confirm=confirm)

    iface.run()

    assert session._step_calls == []


def test_u_8_empty_string_goes_to_step():
    session = FakeSession([TurnResult("ответ", False, False)])
    reader = FakeReader(["", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(session, reader=reader, writer=writer, confirm=confirm)

    iface.run()

    assert session._step_calls == [""]


def test_u_9_game_finished_shows_message_no_exit():
    session = FakeSession([
        TurnResult("финальный текст", True, False),
    ])
    reader = FakeReader(["идти на север", "history", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(
        session, reader=reader, writer=writer, confirm=confirm,
        file_exists=lambda p: False,
    )

    iface.run()

    assert "финальный текст" in writer.outputs
    assert WIN_MESSAGE in writer.outputs
    # Цикл продолжился: history отработал, session.history вызван
    assert session._history_calls >= 1


def test_u_10_input_after_win_shows_hint():
    session = FakeSession([
        TurnResult("финальный текст", True, False),
    ])
    reader = FakeReader(["идти на север", "осмотреться", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(session, reader=reader, writer=writer, confirm=confirm)

    iface.run()

    # step вызван только для первого ввода, второй заблокирован.
    assert session._step_calls == ["идти на север"]
    assert WIN_INPUT_HINT in writer.outputs


def test_u_11_aborted_shows_message_and_exits():
    session = FakeSession([TurnResult("", False, True, "integrity_error")])
    reader = FakeReader(["взять", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(session, reader=reader, writer=writer, confirm=confirm)

    iface.run()

    assert ABORTED_MESSAGE in writer.outputs
    # Цикл завершился после aborted: quit не достигнут.
    assert session._step_calls == ["взять"]


@pytest.mark.skip(reason="интерфейс не знает rung; проверяется визуально")
def test_u_12_rung_3_ignored():
    pass


def test_u_13_typo_goes_to_step():
    session = FakeSession([TurnResult("ответ", False, False)])
    reader = FakeReader(["sav", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(session, reader=reader, writer=writer, confirm=confirm)

    iface.run()

    assert session._step_calls == ["sav"]


def test_u_14_save_uppercase_recognized():
    session = FakeSession([])
    reader = FakeReader(["SAVE", "quit"])
    writer = FakeWriter()
    confirm = FakeConfirm([])
    iface = make_interface(
        session, reader=reader, writer=writer, confirm=confirm,
        file_exists=lambda p: False,
    )

    iface.run()

    assert session._save_calls == [SAVE_PATH]
    assert session._step_calls == []