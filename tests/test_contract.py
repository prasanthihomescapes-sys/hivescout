from pathlib import Path

import yaml

from hivescout import contract

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"


def test_check_without_contract_is_high(tmp_path):
    fs = contract.check(tmp_path / "none", "t")
    assert [(f.rule, f.severity) for f in fs] == [("CT000", "high")]


def test_init_scaffolds_routes_and_check_flags_todos(tmp_path):
    written = contract.init(EX / "broken-next", tmp_path, "broken-next")
    assert [p.name for p in written] == ["web.yml"]
    doc = yaml.safe_load(written[0].read_text(encoding="utf-8"))
    assert sorted(f["route"] for f in doc["flows"]) == ["/", "/dashboard", "/settings"]
    fs = contract.check(tmp_path, "broken-next", EX / "broken-next")
    assert {f.rule for f in fs} == {"CT003"}  # schema-valid, complete, but TODOs remain
    assert contract.init(EX / "broken-next", tmp_path, "broken-next") == []  # never overwrites


def test_complete_contract_passes_and_missing_state_fails(tmp_path):
    state = {"shows": "A short message", "recovery": "retry", "copy": "We could not load this. Try again."}
    doc = {"version": 1, "surface": "web", "flows": [{
        "id": "home", "route": "/", "goal": "See orders",
        "states": {"loading": state, "empty": {"not_applicable": "home always has static content"},
                   "error": state, "offline": state}}]}
    (tmp_path / "web.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert contract.check(tmp_path, "t") == []

    del doc["flows"][0]["states"]["offline"]
    doc["flows"][0]["states"]["error"] = {"shows": "Oops", "recovery": "none"}
    (tmp_path / "web.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert sorted(f.rule for f in contract.check(tmp_path, "t")) == ["CT002", "CT004"]


def test_schema_errors_reported(tmp_path):
    (tmp_path / "web.yml").write_text("version: 1\nsurface: web\nflows: [{id: Bad Id, states: {}}]\n")
    assert {f.rule for f in contract.check(tmp_path, "t")} == {"CT001"}


def test_update_appends_new_routes_only(tmp_path):
    contract.init(EX / "broken-next", tmp_path, "b")
    f = tmp_path / "web.yml"
    doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    doc["flows"] = [fl for fl in doc["flows"] if fl["route"] != "/settings"]
    doc["flows"][0]["goal"] = "kept"
    f.write_text(yaml.safe_dump(doc), encoding="utf-8")
    contract.init(EX / "broken-next", tmp_path, "b", update=True)
    doc2 = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert sorted(fl["route"] for fl in doc2["flows"]) == ["/", "/dashboard", "/settings"]
    assert doc2["flows"][0]["goal"] == "kept"


def test_python_route_discovery():
    assert contract.discover_routes("python", EX / "broken-api") == ["GET /debug", "GET /price", "GET /report"]
