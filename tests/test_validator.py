from types import MappingProxyType

import pytest

from anvil_and_ink.models import Canon, GameState, Manifest, RenderedText
from anvil_and_ink.validator import apply_fallback, validate


@pytest.fixture
def canon() -> Canon:
    return Canon(
        meta={"start_location": "loc_cell"},
        initial_state={"flags": {}},
        flags={},
        locations={
            "loc_cell": {
                "id": "loc_cell", "name": "Камера",
                "entities": ["item_key"],
            },
            "loc_market": {
                "id": "loc_market", "name": "Рынок",
                "entities": ["npc_trader", "item_sword"],
            },
        },
        npcs={
            "npc_trader": {"id": "npc_trader", "name": "Торговец"},
        },
        items={
            "item_key": {"id": "item_key", "name": "Ключ"},
            "item_sword": {"id": "item_sword", "name": "Меч"},
        },
        use_targets={},
        verbs={},
        directions={},
        reason_codes={"ok", "unknown", "denied", "not_applicable"},
        win_condition=[],
    )


def _state(location="loc_cell", inventory=()):
    return GameState(
        turn=1, location=location, inventory=tuple(sorted(inventory)),
        flags=MappingProxyType({}), won=False, game_over=False,
    )


def _manifest(inventory_added=(), inventory_removed=(),
              location_before="loc_cell", location_after="loc_cell"):
    return Manifest(
        turn=1, verb="TAKE", reason_code="ok", applied=True,
        location_before=location_before, location_after=location_after,
        inventory_added=inventory_added,
        inventory_removed=inventory_removed,
        flags_changed=MappingProxyType({}),
        won=False, game_over=False,
        rendered_source="specific",
    )


def _rendered(text="Вы берёте Ключ."):
    return RenderedText(text=text, source="specific", template=text)


# ---------- VD-1..VD-3: длина ----------

def test_VD1_too_short(canon):
    r = validate("Хм.", _rendered(), _manifest(), canon, _state())
    assert not r.ok
    assert any(i.code == "too_short" for i in r.issues)


def test_VD2_too_long(canon):
    text = "а" * 900
    r = validate(text, _rendered(), _manifest(), canon, _state())
    assert not r.ok
    assert any(i.code == "too_long" for i in r.issues)


def test_VD3_too_many_sentences(canon):
    text = ". ".join(["Мысль"] * 8) + "."
    r = validate(text, _rendered(), _manifest(), canon, _state())
    assert not r.ok
    assert any(i.code == "too_long" for i in r.issues)


# ---------- VD-4..VD-5: утечки служебных меток ----------

def test_VD4_slot_leak(canon):
    r = validate("Вы берёте {item}.",
                 _rendered(), _manifest(), canon, _state())
    assert not r.ok
    assert any(i.code == "leaked_marker" for i in r.issues)


def test_VD5_bookkeeping_marker(canon):
    r = validate("Вы берёте Ключ. [Манифест: ...]",
                 _rendered(), _manifest(), canon, _state())
    assert not r.ok
    assert any(i.code == "leaked_marker" for i in r.issues)


# ---------- VD-6: мета-комментарии ----------

def test_VD6_meta_commentary(canon):
    r = validate("Вы берёте Ключ. Как ассистент, я бы сказал...",
                 _rendered(), _manifest(), canon, _state())
    assert not r.ok
    assert any(i.code == "meta_commentary" for i in r.issues)


# ---------- VD-7..VD-9: покрытие манифеста ----------

def test_VD7_missing_added_item(canon):
    m = _manifest(inventory_added=("item_key",))
    r = validate("Вы что-то делаете в темноте.",
                 _rendered(), m, canon,
                 _state(inventory=("item_key",)))
    assert not r.ok
    assert any(i.code == "missing_change_fact" for i in r.issues)


def test_VD8_added_item_mentioned(canon):
    m = _manifest(inventory_added=("item_key",))
    r = validate("Вы берёте Ключ и кладёте в карман.",
                 _rendered(), m, canon,
                 _state(inventory=("item_key",)))
    assert r.ok, r.issues


def test_VD9_added_item_inflected(canon):
    m = _manifest(inventory_added=("item_key",))
    r = validate("Вы играете Ключом.",
                 _rendered(), m, canon,
                 _state(inventory=("item_key",)))
    assert r.ok, r.issues


# ---------- VD-10..VD-11: сущности вне зоны (warning) ----------

def test_VD10_invented_npc_warning(canon):
    # Торговец в loc_market, играем в loc_cell → warning, не error
    r = validate("Вы вспоминаете Торговца.",
                 _rendered(), _manifest(), canon, _state("loc_cell"))
    assert r.ok
    assert any(
        i.code == "invented_entity" and i.severity == "warning"
        for i in r.issues
    )


def test_VD11_entity_in_scope_ok(canon):
    # Торговец в loc_market, играем там же → всё чисто
    r = validate("Торговец кивает.",
                 _rendered(), _manifest(), canon, _state("loc_market"))
    assert r.ok
    assert not any(i.code == "invented_entity" for i in r.issues)


# ---------- VD-12: валидный текст ----------

def test_VD12_valid_text(canon):
    m = _manifest(inventory_added=("item_key",))
    r = validate("Вы поднимаете Ключ с земли.",
                 _rendered(), m, canon,
                 _state(inventory=("item_key",)))
    assert r.ok
    assert r.issues == ()
    assert r.fallback_used is False


# ---------- VD-13..VD-14: fallback ----------

def test_VD13_apply_fallback(canon):
    m = _manifest(inventory_added=("item_key",))
    orig = "Хм."
    rendered = _rendered("Вы берёте Ключ.")
    r = validate(orig, rendered, m, canon,
                 _state(inventory=("item_key",)))
    assert not r.ok
    assert r.fallback_used is False      # ещё не применяли

    text, r2 = apply_fallback(orig, rendered, r)
    assert text == "Вы берёте Ключ."
    assert r2.fallback_used is True
    assert r2.ok is False                # вердикт не переворачивается


def test_VD14_apply_fallback_passthrough(canon):
    m = _manifest(inventory_added=("item_key",))
    orig = "Вы поднимаете Ключ."
    r = validate(orig, _rendered("draft"), m, canon,
                 _state(inventory=("item_key",)))
    assert r.ok
    text, r2 = apply_fallback(orig, _rendered("draft"), r)
    assert text == orig
    assert r2 == r


# ---------- VD-15: детерминизм ----------

def test_VD15_determinism(canon):
    args = (
        "Вы поднимаете Ключ.",
        _rendered(),
        _manifest(inventory_added=("item_key",)),
        canon,
        _state(inventory=("item_key",)),
    )
    assert validate(*args) == validate(*args)


# ---------- VD-16: только warning — всё равно ok ----------

def test_VD16_warning_only_still_ok(canon):
    r = validate("Вы вспоминаете Торговца.",
                 _rendered(), _manifest(), canon, _state("loc_cell"))
    assert r.ok
    assert r.fallback_used is False
    assert all(i.severity == "warning" for i in r.issues)


# ---------- VD-17: удалённый предмет ----------

def test_VD17_missing_removed_item(canon):
    m = _manifest(inventory_removed=("item_key",))
    # Манифест говорит, что Ключ убран, но текст не упоминает
    r = validate("Вы кланяетесь.",
                 _rendered(), m, canon, _state())
    assert not r.ok
    assert any(i.code == "missing_change_fact" for i in r.issues)


def test_VD18_removed_item_mentioned(canon):
    m = _manifest(inventory_removed=("item_key",))
    r = validate("Вы теряете Ключ.",
                 _rendered(), m, canon, _state())
    assert r.ok, r.issues