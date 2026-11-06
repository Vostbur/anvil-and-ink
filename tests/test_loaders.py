import pytest
from pathlib import Path

from anvil_and_ink.loaders.canon import load_canon, CanonError
from anvil_and_ink.loaders.templates import load_templates, TemplatesError

DATA = Path(__file__).parent / "data"  # скопируйте canon.yaml и templates.yaml сюда

# ---------- LD-1, LD-2 ----------
def test_LD1_canon_loads():
    canon = load_canon(DATA / "canon.yaml")
    assert canon.meta["start_location"] == "loc_shelter"
    assert "loc_market" in canon.locations

def test_LD2_templates_loads():
    canon = load_canon(DATA / "canon.yaml")
    templates = load_templates(DATA / "templates.yaml", canon.reason_codes)
    assert "ok" in templates.generic
    assert "GO" in templates.specific

# ---------- LD-3 … LD-13: ошибки канона ----------
@pytest.mark.parametrize("mutator,expected_substr", [
    # LD-3
    (lambda c: c["locations"][0]["exits"][0].update({"to": "loc_void"}), "unknown location"),
    # LD-4
    (lambda c: c["locations"][1]["entities"].append("npc_wounded"), "exactly one location"),
    # LD-5
    (lambda c: [loc["entities"].remove("npc_trader") for loc in c["locations"] if "npc_trader" in loc.get("entities", [])] and None, "exactly one location"),
    # LD-6
    (lambda c: c["initial_state"]["flags"].update({"ghost_flag": True}), "flag not declared"),
    # LD-7
    (lambda c: c["win_condition"].append({"type": "flag", "flag": "unset_flag"}), "never set to true"),
    # LD-8
    (lambda c: c["items"].append({"id": "item_ghost", "name": "Ghost"}), "no instance and no producer"),
    # LD-9
    (lambda c: c["use_targets"][0]["exit"].update({"direction": "nowhere"}), "invalid location or direction"),
    # LD-10, LD-11, LD-12, LD-13 — см. полный набор в репозитории
], ids=["LD-3", "LD-4", "LD-5", "LD-6", "LD-7", "LD-8", "LD-9"])
def test_canon_errors(tmp_path, mutator, expected_substr):
    raw = (DATA / "canon.yaml").read_text(encoding="utf-8")
    import yaml
    data = yaml.safe_load(raw)
    mutator(data)
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump(data), encoding="utf-8")
    with pytest.raises(CanonError) as exc:
        load_canon(bad)
    assert any(expected_substr in e for e in exc.value.errors)

# ---------- LD-14 … LD-19: ошибки шаблонов ----------
def test_LD14_missing_generic(tmp_path):
    raw = (DATA / "templates.yaml").read_text(encoding="utf-8")
    import yaml
    data = yaml.safe_load(raw)
    del data["generic"]["ok"]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump(data), encoding="utf-8")
    canon = load_canon(DATA / "canon.yaml")
    with pytest.raises(TemplatesError) as exc:
        load_templates(bad, canon.reason_codes)
    assert "generic.ok" in str(exc.value)

def test_LD15_missing_specific_take_ok(tmp_path):
    raw = (DATA / "templates.yaml").read_text(encoding="utf-8")
    import yaml
    data = yaml.safe_load(raw)
    del data["specific"]["TAKE"]["ok"]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump(data), encoding="utf-8")
    canon = load_canon(DATA / "canon.yaml")
    with pytest.raises(TemplatesError) as exc:
        load_templates(bad, canon.reason_codes)
    assert "specific.TAKE.ok" in str(exc.value)

def test_LD16_scene_in_go_ok(tmp_path):
    raw = (DATA / "templates.yaml").read_text(encoding="utf-8")
    import yaml
    data = yaml.safe_load(raw)
    data["specific"]["GO"]["ok"].append("Вы идёте. {scene}")
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump(data), encoding="utf-8")
    canon = load_canon(DATA / "canon.yaml")
    with pytest.raises(TemplatesError) as exc:
        load_templates(bad, canon.reason_codes)
    assert "{scene}" in str(exc.value)

def test_LD17_unknown_slot(tmp_path):
    raw = (DATA / "templates.yaml").read_text(encoding="utf-8")
    import yaml
    data = yaml.safe_load(raw)
    data["specific"]["TAKE"]["ok"].append("Вы берёте {item} и {unknown}.")
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump(data), encoding="utf-8")
    canon = load_canon(DATA / "canon.yaml")
    with pytest.raises(TemplatesError) as exc:
        load_templates(bad, canon.reason_codes)
    assert "unknown slot" in str(exc.value)

def test_LD18_generic_with_slot(tmp_path):
    raw = (DATA / "templates.yaml").read_text(encoding="utf-8")
    import yaml
    data = yaml.safe_load(raw)
    data["generic"]["ok"].append("Вы делаете это с {item}.")
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump(data), encoding="utf-8")
    canon = load_canon(DATA / "canon.yaml")
    with pytest.raises(TemplatesError) as exc:
        load_templates(bad, canon.reason_codes)
    assert "not allowed in generic" in str(exc.value)

def test_LD19_canon_guarantee_covers_missing_specific(tmp_path):
    raw = (DATA / "templates.yaml").read_text(encoding="utf-8")
    import yaml
    data = yaml.safe_load(raw)
    del data["specific"]["TAKE"]["ok"]
    data["canon_guarantee"].append({"verb": "TAKE", "reason_code": "ok"})
    good = tmp_path / "good.yaml"
    good.write_text(yaml.dump(data), encoding="utf-8")
    canon = load_canon(DATA / "canon.yaml")
    # Не должно бросить исключение
    templates = load_templates(good, canon.reason_codes)
    assert templates is not None