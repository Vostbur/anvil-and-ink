import pytest

from anvil_and_ink.loaders import load_canon
from anvil_and_ink.models import Manifest
from anvil_and_ink.validator import Validator


CANON_PATH = "canon.yaml"


@pytest.fixture(scope="module")
def canon():
    return load_canon(CANON_PATH)


@pytest.fixture(scope="module")
def validator(canon):
    return Validator(canon)


def make_manifest(names=None, numbers=None, mode="describe", verb="TAKE", reason_code="ok"):
    return Manifest(
        allowed_names=set(names or []),
        allowed_numbers=set(numbers or []),
        mode=mode,
        verb=verb,
        reason_code=reason_code,
    )


# ---------------------------------------------------------------------------
# 1. Пустота
# ---------------------------------------------------------------------------


def test_v_1_empty_response(validator):
    verdict = validator.validate("", make_manifest())
    assert not verdict.ok
    assert verdict.reason.code == "empty"


def test_v_2_whitespace_only(validator):
    verdict = validator.validate("   \n\t  ", make_manifest())
    assert not verdict.ok
    assert verdict.reason.code == "empty"


# ---------------------------------------------------------------------------
# 2. Длина
# ---------------------------------------------------------------------------


def test_v_3_too_short(validator):
    verdict = validator.validate("Ок.", make_manifest())
    assert not verdict.ok
    assert verdict.reason.code == "too_short"


def test_v_4_short_but_valid(validator):
    verdict = validator.validate("Заперто.", make_manifest())
    assert verdict.ok


def test_v_5_too_long(validator):
    verdict = validator.validate("а" * 1200, make_manifest())
    assert not verdict.ok
    assert verdict.reason.code == "too_long"


# ---------------------------------------------------------------------------
# 3. Числа
# ---------------------------------------------------------------------------


def test_v_6_number_from_manifest(validator):
    manifest = make_manifest(names=["фильтр"], numbers=[2])
    verdict = validator.validate("Вы видите два фильтра.", manifest)
    assert verdict.ok


def test_v_7_number_not_from_manifest(validator):
    manifest = make_manifest(numbers=[1])
    verdict = validator.validate("Вы находите 5 патронов.", manifest)
    assert not verdict.ok
    assert verdict.reason.code == "unknown_number"


def test_v_8_written_numeral_not_allowed(validator):
    manifest = make_manifest(numbers=[])
    verdict = validator.validate("У вас пять золота.", manifest)
    assert not verdict.ok
    assert verdict.reason.code == "unknown_number"


def test_v_9_written_numeral_above_ten_ignored(validator):
    manifest = make_manifest(numbers=[])
    verdict = validator.validate("Пятнадцать эльфов.", manifest)
    assert verdict.ok


# ---------------------------------------------------------------------------
# 4. Имена
# ---------------------------------------------------------------------------


def test_v_10_name_from_manifest_exact_form(validator):
    manifest = make_manifest(names=["фильтр"])
    verdict = validator.validate("Вы берёте фильтр.", manifest)
    assert verdict.ok


def test_v_11_name_from_manifest_other_word_form(validator):
    manifest = make_manifest(names=["фильтр"])
    verdict = validator.validate("Вы берёте фильтра.", manifest)
    assert verdict.ok


def test_v_12_canon_name_not_in_manifest(validator):
    manifest = make_manifest(names=[])
    verdict = validator.validate("Вы находите код.", manifest)
    assert not verdict.ok
    assert verdict.reason.code == "unknown_name"


def test_v_13_name_outside_canon(validator):
    manifest = make_manifest(names=[])
    verdict = validator.validate("Вы видите эльфа.", manifest)
    assert verdict.ok


def test_v_14_multiword_name_in_manifest(validator):
    manifest = make_manifest(names=["дверь в бункер"])
    verdict = validator.validate("Вы видите дверь в бункер.", manifest)
    assert verdict.ok


def test_v_15_multiword_name_not_in_manifest(validator):
    manifest = make_manifest(names=[])
    verdict = validator.validate("Вы видите дверь в бункер.", manifest)
    assert not verdict.ok
    assert verdict.reason.code == "unknown_name"


def test_v_16_common_npc_name_without_npc(validator):
    # Известное ложное срабатывание (лечится полем name_is_common, не в MVP).
    manifest = make_manifest(names=[])
    verdict = validator.validate("Торговец смотрит на вас.", manifest)
    assert not verdict.ok
    assert verdict.reason.code == "unknown_name"


# ---------------------------------------------------------------------------
# 5. Утечка механики
# ---------------------------------------------------------------------------


def test_v_17_flag_mention(validator):
    verdict = validator.validate("Значение bunker_door_open изменилось.", make_manifest())
    assert not verdict.ok
    assert verdict.reason.code == "mechanics_leak"


def test_v_18_reason_code_mention(validator):
    verdict = validator.validate("Получен condition_not_met.", make_manifest())
    assert not verdict.ok
    assert verdict.reason.code == "mechanics_leak"


# ---------------------------------------------------------------------------
# Порядок и формат
# ---------------------------------------------------------------------------


def test_v_19_empty_checked_before_numbers(validator):
    # Даже если бы в ответе было число, пустота проверяется первой.
    manifest = make_manifest(numbers=[5])
    verdict = validator.validate("", manifest)
    assert not verdict.ok
    assert verdict.reason.code == "empty"


def test_v_20_message_is_human_phrase(validator):
    manifest = make_manifest(names=[])
    verdict = validator.validate("Вы находите код.", manifest)
    assert not verdict.ok
    assert verdict.reason.code == "unknown_name"
    assert verdict.reason.message
    assert verdict.reason.message != verdict.reason.code


# ---------------------------------------------------------------------------
# LOOK-режим
# ---------------------------------------------------------------------------


def test_v_21_look_mode_names_from_snapshot(validator):
    manifest = make_manifest(
        names=["рынок", "торговец", "фильтр"],
        mode="look",
        verb="LOOK",
        reason_code="ok",
    )
    verdict = validator.validate("Рынок. Торговец держит фильтр.", manifest)
    assert verdict.ok


# ---------------------------------------------------------------------------
# Шаблоны не проходят валидатор (проверяется в session.md, D-42)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="проверяется в session.md (D-42)")
def test_v_22_templates_bypass_validator(validator):
    pass