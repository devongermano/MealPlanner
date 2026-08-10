"""M1.4 — CLI --json contract (PRD §8.4), fast tier.

Contract under test, per command: with --json, stdout is EXACTLY ONE
parseable JSON document::

    {"schema": "mealplan/v2", "command": <name>, "ok": bool,
     "result": {...}}   # or "error": {...}

with all human logs on stderr, and the §8.4 exit codes:
0 ok · 2 computed-but-infeasible (document still emitted, feasible flags +
directional misses inside) · 3 validation/structured error (all-errors
issues list in the document) · 4 bad arguments.

This module stays in the fast tier: commands here either solve nothing
(explicit --menu scoring, errors) or a handful of tiny plates (the
infeasible choose_menu). The solve-heavy commands (week/shop/all/lock/
verify-plan/doctor/frontier) have their contract tests in
test_json_contract_solve.py (slow tier).

Queued minor A lives here too: --n larger than the eligible library is a
friendly structured error naming both numbers — never a random.sample
traceback.
"""

import json

import pytest
import yaml

from mealplan import cli

pytestmark = []


# --------------------------------------------------------------------------- #
#  fixtures — tiny purpose-built libraries, written under tmp_path only
# --------------------------------------------------------------------------- #
def _write_lib(tmp_path, ppl_doc):
    ing_doc = {"schema_version": 1, "ingredients": {
        "stuff": {"p": 20.0, "f": 10.0, "c": 12.0, "perishable": False,
                  "pack_g": 500, "keeps_days": 999, "cost": 4.0, "tags": []},
        # perishable + short-keeping: pantry stock of this ages out fast,
        # which is how the stderr-purity test provokes a human warning
        "fresh": {"p": 2.0, "f": 0.5, "c": 5.0, "perishable": True,
                  "pack_g": 400, "keeps_days": 2, "cost": 3.0, "tags": []}}}
    comp_doc = {"schema_version": 1, "components": [
        {"id": "stew", "name": "stew", "cuisine": "test", "role": "main",
         "yield_g": 2000, "serve_g": {"min": 100, "max": 500},
         "keeps_days": 7, "active_min": 30, "ingredients": {"stuff": 2000}},
        {"id": "rice", "name": "rice", "cuisine": "test", "role": "starch",
         "yield_g": 2000, "serve_g": {"min": 50, "max": 500},
         "keeps_days": 7, "active_min": 10, "ingredients": {"stuff": 2000}}]}
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(ing_doc))
    (lib / "components.yaml").write_text(yaml.safe_dump(comp_doc))
    (lib / "people.yaml").write_text(yaml.safe_dump(ppl_doc))
    return lib


def _feasible_lib(tmp_path):
    return _write_lib(tmp_path, {
        "schema_version": 1,
        "people": {"p1": {"targets": {"protein": 80, "fat": 45, "carb": 55},
                          "tolerance": 0.2}},
        "settings": {"days": 7, "active_min_budget": 600,
                     "max_days_same_component": 7, "cook_days": [0]}})


def _infeasible_lib(tmp_path):
    # 400g protein/day from a library maxing ~500g servings of 20%-protein
    # stew: structurally impossible at any tolerance the schema allows
    return _write_lib(tmp_path, {
        "schema_version": 1,
        "people": {"p1": {"targets": {"protein": 400, "fat": 20, "carb": 20},
                          "tolerance": 0.05}},
        "settings": {"days": 7, "active_min_budget": 600,
                     "max_days_same_component": 7, "cook_days": [0]}})


def run_json(capsys, argv):
    """Run the CLI in-process; return (exit_code, parsed_doc, stderr).
    Asserts the FULL contract invariant: stdout is exactly one JSON doc."""
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    cap = capsys.readouterr()
    doc = json.loads(cap.out)          # parses ⇒ one well-formed document
    # nothing but JSON on stdout: re-serializing what json.loads consumed
    # must account for every non-whitespace byte
    assert cap.out.strip().startswith("{") and cap.out.strip().endswith("}")
    return code, doc, cap.err


def check_envelope(doc, command, ok):
    assert doc["schema"] == "mealplan/v2"
    assert doc["command"] == command
    assert doc["ok"] is ok
    assert ("result" in doc) != ("error" in doc)


# --------------------------------------------------------------------------- #
#  ok path (no LP solves: explicit --menu is scored, not solved)
# --------------------------------------------------------------------------- #
def test_menu_json_envelope_and_result(tmp_path, capsys):
    lib = _feasible_lib(tmp_path)
    code, doc, err = run_json(
        capsys, ["menu", "--library", str(lib), "--menu", "stew,rice",
                 "--json"])
    assert code == 0
    check_envelope(doc, "menu", True)
    r = doc["result"]
    assert r["menu"] == ["stew", "rice"]
    # menu_info is score_menu's info dict, converted not reshaped (P10)
    for key in ("active_min", "waste_perishable", "cuisines"):
        assert key in r["menu_info"]
    assert r["feasible"] is None       # explicit menu: feasibility not solved
    assert r["misses"] == {}


def test_json_mode_keeps_stdout_pure_of_human_logs(tmp_path, capsys):
    """A pantry warning (stderr channel) must not pollute the JSON stdout."""
    lib = _feasible_lib(tmp_path)
    pan = tmp_path / "pantry.yaml"
    pan.write_text(yaml.safe_dump({
        "schema_version": 1,
        "stock": [{"ingredient": "fresh", "grams": 100,
                   "acquired": "2026-07-01"}]}))   # long-expired -> warning
    code, doc, err = run_json(
        capsys, ["menu", "--library", str(lib), "--menu", "stew,rice",
                 "--pantry", str(pan), "--date", "2026-08-09", "--json"])
    assert code == 0
    check_envelope(doc, "menu", True)
    assert "[warning:" in err          # the human log went to stderr


# --------------------------------------------------------------------------- #
#  exit 2 — computed but infeasible IS a result (PRD §8.4)
# --------------------------------------------------------------------------- #
def test_infeasible_menu_exits_2_with_misses_in_json(tmp_path, capsys):
    lib = _infeasible_lib(tmp_path)
    code, doc, err = run_json(
        capsys, ["menu", "--library", str(lib), "--n", "2", "--json"])
    assert code == 2
    check_envelope(doc, "menu", False)
    r = doc["result"]
    assert r["feasible"] is False
    # directional misses per person: protein is SHORT (negative)
    assert r["misses"]["p1"]["protein"] < 0
    assert r["menu"]                       # the best menu is still reported
    # the §8.3 structural-fix-first advice is a human log -> stderr
    assert "not feasible" in err


# --------------------------------------------------------------------------- #
#  exit 3 — validation / structured errors carry the all-errors list
# --------------------------------------------------------------------------- #
def test_broken_library_exits_3_with_issues_array(tmp_path, capsys):
    lib = _feasible_lib(tmp_path)
    # break two documents at once: the all-errors contract means BOTH appear
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(
        {"schema_version": 1, "ingredients": {
            "stuff": {"p": 20.0}}}))       # missing required fields
    code, doc, err = run_json(
        capsys, ["doctor", "--library", str(lib), "--json"])
    assert code == 3
    check_envelope(doc, "doctor", False)
    e = doc["error"]
    assert e["code"] == "invalid_library"
    assert isinstance(e["issues"], list) and e["issues"]
    issue = e["issues"][0]
    for key in ("code", "where", "message", "severity"):
        assert key in issue
    assert any(i["code"] == "missing_field" for i in e["issues"])


def test_invalid_pantry_exits_3_with_issues(tmp_path, capsys):
    lib = _feasible_lib(tmp_path)
    pan = tmp_path / "pantry.yaml"
    pan.write_text(yaml.safe_dump({
        "schema_version": 1,
        "stock": [{"ingredient": "ghost", "grams": -1, "acquired": "nope"}]}))
    code, doc, err = run_json(
        capsys, ["menu", "--library", str(lib), "--menu", "stew,rice",
                 "--pantry", str(pan), "--json"])
    assert code == 3
    check_envelope(doc, "menu", False)
    codes = {i["code"] for i in doc["error"]["issues"]}
    # every problem reported, not first-error-wins
    assert {"unknown_ingredient", "nonpositive_grams", "bad_date"} <= codes


# --------------------------------------------------------------------------- #
#  exit 4 — bad arguments
# --------------------------------------------------------------------------- #
def test_bad_date_exits_4(tmp_path, capsys):
    lib = _feasible_lib(tmp_path)
    code, doc, err = run_json(
        capsys, ["menu", "--library", str(lib), "--menu", "stew,rice",
                 "--date", "08/09/2026", "--json"])
    assert code == 4
    check_envelope(doc, "menu", False)
    assert "YYYY-MM-DD" in doc["error"]["message"]


def test_unknown_forced_component_exits_4(tmp_path, capsys):
    lib = _feasible_lib(tmp_path)
    code, doc, err = run_json(
        capsys, ["menu", "--library", str(lib), "--menu", "stew,rice",
                 "--force", "no_such_dish", "--json"])
    assert code == 4
    assert "no_such_dish" in doc["error"]["message"]


def test_argparse_bad_command_exits_4_not_2(capsys):
    """argparse's stock exit code for bad argv is 2 — which §8.4 reserves
    for computed-but-infeasible. The parser is rewired to 4."""
    with pytest.raises(SystemExit) as ei:
        cli.main(["definitely-not-a-command"])
    assert ei.value.code == 4


def test_verify_plan_requires_path_exits_4(capsys):
    code = 0
    try:
        cli.main(["verify-plan", "--json"])
    except SystemExit as e:
        code = e.code
    doc = json.loads(capsys.readouterr().out)
    assert code == 4
    assert doc["error"]["code"] == "missing_argument"


def test_verify_plan_missing_file_exits_3(tmp_path, capsys):
    code = 0
    try:
        cli.main(["verify-plan", str(tmp_path / "nope.yaml"), "--json"])
    except SystemExit as e:
        code = e.code
    doc = json.loads(capsys.readouterr().out)
    assert code == 3
    assert doc["error"]["code"] == "missing_file"


# --------------------------------------------------------------------------- #
#  queued minor A — --n vs eligible library size
# --------------------------------------------------------------------------- #
def test_n_larger_than_library_is_friendly_error_naming_both(tmp_path,
                                                             capsys):
    lib = _feasible_lib(tmp_path)                # 2 eligible components
    code, doc, err = run_json(
        capsys, ["menu", "--library", str(lib), "--n", "12", "--json"])
    assert code == 4
    e = doc["error"]
    assert e["code"] == "n_exceeds_library"
    assert "12" in e["message"] and "2" in e["message"]   # both numbers named


def test_n_larger_than_library_non_json_names_both_numbers(tmp_path):
    lib = _feasible_lib(tmp_path)
    with pytest.raises(SystemExit) as ei:
        cli.main(["menu", "--library", str(lib), "--n", "12"])
    msg = str(ei.value)
    assert "12" in msg and "2" in msg
    assert "random" not in msg.lower()     # never a random.sample traceback


def test_n_check_counts_post_exclude_eligibility(tmp_path):
    """--exclude shrinks the eligible pool the check is made against."""
    lib = _feasible_lib(tmp_path)
    with pytest.raises(SystemExit) as ei:
        cli.main(["menu", "--library", str(lib), "--n", "2",
                  "--exclude", "stew"])
    msg = str(ei.value)
    assert "--n 2" in msg and "1" in msg
