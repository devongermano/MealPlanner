"""M1.4 — CLI --json contract for the solve-carrying commands (slow tier).

Companion to test_json_contract.py (which owns the no-solve contract
cases): here every command genuinely solves — week/all/shop assemble a
week, doctor runs diagnostics, frontier sweeps choose_menu. Same
invariants: stdout is exactly one mealplan/v2 JSON document; result
carries the SAME engine/costing structures the renderer consumes (P10:
JSON-safe conversion only, no reshaping); logs on stderr; §8.4 exit codes.

The library is tiny and the week short (days=2) — slow tier because of the
LP solves, not because anything here is big.
"""

import json

import yaml

from mealplan import cli


def _lib(tmp_path):
    ing_doc = {"schema_version": 1, "ingredients": {
        "stuff": {"p": 20.0, "f": 10.0, "c": 12.0, "perishable": False,
                  "pack_g": 500, "keeps_days": 999, "cost": 4.0, "tags": []}}}
    comp_doc = {"schema_version": 1, "components": [
        {"id": "stew", "name": "stew", "cuisine": "test", "role": "main",
         "yield_g": 2000, "serve_g": {"min": 100, "max": 500},
         "keeps_days": 7, "active_min": 30, "ingredients": {"stuff": 2000}},
        {"id": "rice", "name": "rice", "cuisine": "test", "role": "starch",
         "yield_g": 2000, "serve_g": {"min": 50, "max": 500},
         "keeps_days": 7, "active_min": 10, "ingredients": {"stuff": 2000}}]}
    ppl_doc = {"schema_version": 1,
               "people": {"p1": {"targets": {"protein": 80, "fat": 45,
                                             "carb": 55},
                                 "tolerance": 0.2}},
               "settings": {"days": 2, "active_min_budget": 600,
                            "max_days_same_component": 7, "cook_days": [0],
                            "shop_days": [0]}}
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(ing_doc))
    (lib / "components.yaml").write_text(yaml.safe_dump(comp_doc))
    (lib / "people.yaml").write_text(yaml.safe_dump(ppl_doc))
    return lib


def run_json(capsys, argv):
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    cap = capsys.readouterr()
    return code, json.loads(cap.out), cap.err


def check_envelope(doc, command, ok=True):
    assert doc["schema"] == "mealplan/v2"
    assert doc["command"] == command
    assert doc["ok"] is ok
    assert ("result" in doc) != ("error" in doc)


def test_week_json_carries_renderer_structures(tmp_path, capsys):
    lib = _lib(tmp_path)
    code, doc, err = run_json(
        capsys, ["week", "--library", str(lib), "--n", "2", "--seed", "0",
                 "--json"])
    assert code == 0
    check_envelope(doc, "week")
    r = doc["result"]
    assert r["feasible"] is True and r["misses"] == {}
    assert sorted(r["menu"]) == ["rice", "stew"]
    # weeks: the engine.build_week shape — {person: [{cid: g} per day]}
    assert list(r["weeks"]) == ["p1"]
    assert len(r["weeks"]["p1"]) == 2                 # days=2
    assert all(isinstance(pl, dict) for pl in r["weeks"]["p1"])
    # session_plan: costing.session_plan verbatim (P10)
    sp = r["session_plan"]
    for key in ("sessions", "batches", "minutes", "unattributed"):
        assert key in sp
    assert sp["sessions"][0]["start"] == 0
    # purchase rows ride as rows, not reshaped objects
    assert isinstance(r["purchase_rows"], list)
    assert isinstance(r["total_cost"], (int, float))
    # json mode: NO plan.md side effect (stdout IS the output)
    assert not (tmp_path / "plan.md").exists()


def test_week_json_and_non_json_solve_identically(tmp_path, capsys):
    """--json is a serialization, not a different solve: same seed, same
    portions as the rendered plan run."""
    lib = _lib(tmp_path)
    code, doc, _ = run_json(
        capsys, ["week", "--library", str(lib), "--n", "2", "--seed", "3",
                 "--json"])
    assert code == 0
    out = tmp_path / "plan.md"
    cli.main(["week", "--library", str(lib), "--n", "2", "--seed", "3",
              "--out", str(out)])
    capsys.readouterr()
    rendered = out.read_text()
    for pl in doc["result"]["weeks"]["p1"]:
        for cid, g in pl.items():
            assert f"**{g:g}g**" in rendered


def test_shop_json(tmp_path, capsys):
    lib = _lib(tmp_path)
    code, doc, err = run_json(
        capsys, ["shop", "--library", str(lib), "--n", "2", "--json"])
    assert code == 0
    check_envelope(doc, "shop")
    r = doc["result"]
    assert r["purchase_rows"]
    # a purchase row is costing.purchase's tuple, JSON-converted to a list:
    # (name, need, units, pack, left, perishable, keeps)
    row = r["purchase_rows"][0]
    assert row[0] == "stuff" and len(row) == 7
    assert "waste_perishable_g" in r


def test_all_json_with_artifacts_writes_deliverables(tmp_path, capsys):
    lib = _lib(tmp_path)
    art = tmp_path / "artifacts"
    code, doc, err = run_json(
        capsys, ["all", "--library", str(lib), "--n", "2", "--json",
                 "--artifacts", str(art)])
    assert code == 0
    check_envelope(doc, "all")
    assert (art / "shopping_list.md").exists()
    assert (art / "cook_plan.md").exists()
    assert (art / "eat_p1.md").exists()
    assert "artifacts written" in err          # the log stayed on stderr


def test_doctor_json_report_and_data(tmp_path, capsys):
    lib = _lib(tmp_path)
    code, doc, err = run_json(
        capsys, ["doctor", "--library", str(lib), "--json"])
    assert code == 0
    check_envelope(doc, "doctor")
    r = doc["result"]
    assert "## Feasibility" in r["report"]     # the human report rides along
    assert r["data"]["feasibility"]["p1"]["ok"] is True


def test_frontier_json_points(tmp_path, capsys):
    lib = _lib(tmp_path)
    code, doc, err = run_json(
        capsys, ["frontier", "--library", str(lib), "--n", "2",
                 "--range", "500:550:50", "--json"])
    assert code == 0
    check_envelope(doc, "frontier")
    pts = doc["result"]["points"]
    assert len(pts) == 2
    for p in pts:
        for key in ("budget", "spend", "dishes", "cuisines",
                    "waste_perishable_g", "feasible", "menu"):
            assert key in p
