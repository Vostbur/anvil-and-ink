import pytest

from anvil_and_ink.llm_ladder import UNIVERSAL_STUB, run
from anvil_and_ink.loaders import load_canon, load_templates
from anvil_and_ink.models import Command, Manifest, Outcome
from anvil_and_ink.projection import ProjectionLookup, build_initial
from anvil_and_ink.prompt_builder import BuildError, PromptBuilderConfig
from anvil_and_ink.validator import Reason, Verdict


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def canon():
    return load_canon("canon.yaml")


@pytest.fixture(scope="module")
def templates(canon):
    return load_templates(
        "templates.yaml",
        set(canon.reason_codes.keys()),
        set(canon.verbs.keys()),
    )


@pytest.fixture
def projection(canon):
    return ProjectionLookup(build_initial(canon), canon)


@pytest.fixture
def command():
    return Command(
        verb="TAKE",
        args={"item": "item_flask"},
        resolved={"item": True},
        raw="взять флягу",
    )


@pytest.fixture
def outcome():
    return Outcome(result="ok", reason_code="ok", applied=(), response_text=None)


@pytest.fixture
def manifest():
    return Manifest(
        allowed_names=set(),
        allowed_numbers=set(),
        mode="describe",
        verb="TAKE",
        reason_code="ok",
    )


@pytest.fixture
def config():
    return PromptBuilderConfig()


# ---------------------------------------------------------------------------
# Фейки
# ---------------------------------------------------------------------------


class FakeBuilder:
    def __init__(self, manifest, raise_build_error=False):
        self.manifest = manifest
        self.raise_build_error = raise_build_error
        self.calls = []

    def __call__(self, command, outcome, projection, canon, recent_events, turn, config, retry_note=None):
        if self.raise_build_error:
            raise BuildError("prompt overflow")
        self.calls.append({"retry_note": retry_note})
        prompt = "PROMPT"
        if retry_note:
            prompt = prompt + "\n" + retry_note
        return prompt, self.manifest


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, prompt, config):
        self.calls.append(prompt)
        idx = len(self.calls) - 1
        if idx < len(self.results):
            return self.results[idx]
        return self.results[-1]


class FakeValidator:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    def validate(self, response, manifest):
        self.calls.append(response)
        idx = len(self.calls) - 1
        if idx < len(self.verdicts):
            return self.verdicts[idx]
        return self.verdicts[-1]


class FakeRenderer:
    def __init__(self, text="ШАБЛОННЫЙ ТЕКСТ", should_raise=False):
        self.text = text
        self.should_raise = should_raise
        self.calls = []

    def __call__(self, verb, reason_code, outcome, snapshot, turn, templates):
        self.calls.append({"verb": verb, "reason_code": reason_code})
        if self.should_raise:
            raise RuntimeError("renderer crashed")
        return self.text


def ok_verdict():
    return Verdict(True, None)


def reject_verdict():
    return Verdict(
        False,
        Reason("unknown_name", "в ответе упомянуто имя «эльф», которого не было в контексте"),
    )


def ok_response(text="хороший ответ"):
    return {"ok": True, "text": text, "latency_ms": 120, "tokens": 42}


def error_response(error_class, detail=""):
    return {
        "ok": False,
        "error_class": error_class,
        "detail": detail,
        "latency_ms": 10000,
        "tokens": None,
    }


def run_ladder(canon, templates, projection, command, outcome, config,
               builder, client, validator, renderer):
    return run(
        command, outcome, projection, canon, [], 1, config,
        builder, client, validator, renderer, templates,
    )


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_l_1_valid_first_try(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([ok_response("Вы берёте флягу воды.")])
    validator = FakeValidator([ok_verdict()])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 1
    assert result.final_text == "Вы берёте флягу воды."
    assert len(client.calls) == 1


def test_l_2_first_rejected_second_ok(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([ok_response("плохой ответ"), ok_response("хороший ответ")])
    validator = FakeValidator([reject_verdict(), ok_verdict()])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 2
    assert result.final_text == "хороший ответ"
    assert len(client.calls) == 2
    # Во втором промпте есть заметка, в первом — нет.
    assert "Не повторяй эту ошибку" in client.calls[1]
    assert "Не повторяй эту ошибку" not in client.calls[0]


def test_l_3_both_rejected(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([ok_response("плохой 1"), ok_response("плохой 2")])
    validator = FakeValidator([reject_verdict(), reject_verdict()])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 3
    assert result.fallback_reason == "validation_failed_twice"
    assert len(client.calls) == 2


def test_l_4_first_timeout_second_ok_no_note(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([
        error_response("timeout_or_network"),
        ok_response("хороший ответ"),
    ])
    validator = FakeValidator([ok_verdict()])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 2
    assert result.final_text == "хороший ответ"
    assert len(client.calls) == 2
    # После таймаута повтор идёт без заметки.
    assert "Не повторяй эту ошибку" not in client.calls[1]


def test_l_5_both_timeout(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([
        error_response("timeout_or_network"),
        error_response("timeout_or_network"),
    ])
    validator = FakeValidator([])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 3
    assert result.fallback_reason == "timeout_twice"
    assert len(client.calls) == 2


def test_l_6_timeout_then_rejected(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([
        error_response("timeout_or_network"),
        ok_response("плохой ответ"),
    ])
    validator = FakeValidator([reject_verdict()])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 3
    assert result.fallback_reason == "mixed"


def test_l_7_config_error_first(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([error_response("config_error", "401 unauthorized")])
    validator = FakeValidator([])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 3
    assert result.skip_reason == "config_error"
    assert len(client.calls) == 1


def test_l_8_empty_response_goes_to_rung_two(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([
        error_response("empty_response"),
        ok_response("хороший ответ"),
    ])
    validator = FakeValidator([ok_verdict()])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 2
    assert len(client.calls) == 2


def test_l_9_unknown_bypasses_ladder(canon, templates, projection, config, manifest):
    unknown_command = Command(verb="UNKNOWN", args={}, resolved={}, raw="абракадабра")
    unknown_outcome = Outcome(result="fail", reason_code="unknown", applied=(), response_text=None)

    builder = FakeBuilder(manifest)
    client = FakeClient([])
    validator = FakeValidator([])
    renderer = FakeRenderer(text="Не понимаю. Попробуйте иначе.")

    result = run_ladder(canon, templates, projection, unknown_command, unknown_outcome, config,
                        builder, client, validator, renderer)

    assert result.rung is None
    assert result.skip_reason == "unknown_command"
    assert len(client.calls) == 0
    assert result.final_text == "Не понимаю. Попробуйте иначе."


def test_l_10_renderer_crash_gives_stub(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([error_response("config_error")])
    validator = FakeValidator([])
    renderer = FakeRenderer(should_raise=True)

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 4
    assert result.final_text == UNIVERSAL_STUB


def test_l_11_turn_logging(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([ok_response("Вы берёте флягу воды.")])
    validator = FakeValidator([ok_verdict()])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.diagnostics["rung"] == 1
    assert result.diagnostics["final_text"] == "Вы берёте флягу воды."


def test_l_12_call_logging_fields(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([ok_response("Вы берёте флягу воды.")])
    validator = FakeValidator([ok_verdict()])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    call = result.diagnostics["calls"][0]
    assert "prompt" in call
    assert "response" in call
    assert "latency_ms" in call
    assert "validator_result" in call
    assert call["validator_result"] == "ok"


def test_l_13_config_error_not_retried(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([error_response("config_error")])
    validator = FakeValidator([])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert len(client.calls) == 1
    assert result.rung == 3


def test_l_14_max_two_calls(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest)
    client = FakeClient([ok_response("плохой ответ")] * 5)
    validator = FakeValidator([reject_verdict()] * 5)
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 3
    assert len(client.calls) == 2


def test_l_15_build_error_skips_client(canon, templates, projection, command, outcome, config, manifest):
    builder = FakeBuilder(manifest, raise_build_error=True)
    client = FakeClient([])
    validator = FakeValidator([])
    renderer = FakeRenderer()

    result = run_ladder(canon, templates, projection, command, outcome, config,
                        builder, client, validator, renderer)

    assert result.rung == 3
    assert result.fallback_reason == "build_error"
    assert len(client.calls) == 0