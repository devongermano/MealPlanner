"""M0.12 pantry regression tests (schema + empty-state + purchase deduction).

Scope per PRD §8.1: optional pantry.yaml with on-hand ingredient stock and
cooked leftovers; full all-errors validation (known refs, grams > 0,
parseable dates); purchase() deducts stock grams from ingredient need BEFORE
rounding to packs; a missing/empty pantry is a no-op with identical results.
No UI, no planning integration of cooked leftovers — that is M1+.
"""

from pathlib import Path

import pytest
import yaml

from mealplan import cli, io_yaml
from mealplan.costing import menu_cost, purchase
from mealplan.io_yaml import (ValidationError, load_pantry,
                              validate_pantry_doc)
from mealplan.model import Pantry

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"

ING = {
    "white_rice_dry": {"p": 7.1, "f": 0.7, "c": 80.0, "perishable": False,
                       "pack_g": 907, "keeps_days": 999, "tags": [],
                       "cost": 3.50},
    "chicken": {"p": 22.5, "f": 2.6, "c": 0.0, "perishable": True,
                "pack_g": 907, "keeps_days": 3, "tags": [], "cost": 9.0},
}
COMPS = {
    "rice": {"ingredients": {"white_rice_dry": 500}},
    "chx": {"ingredients": {"chicken": 450}},
}


def rows_by_name(rows):
    return {r[0]: r for r in rows}


# --------------------------------------------------------------------------- #
#  empty-state semantics: no pantry == empty pantry, bit for bit
# --------------------------------------------------------------------------- #
def test_no_pantry_equals_empty_pantry_packs():
    batches = {"rice": 4, "chx": 3}
    base = purchase(COMPS, ING, ["rice", "chx"], batches)
    for empty in (
        {"schema_version": 1, "stock": [], "cooked": []},
        {"schema_version": 1},                       # keys absent entirely
        Pantry.from_raw({"schema_version": 1, "stock": [], "cooked": []}),
    ):
        got = purchase(COMPS, ING, ["rice", "chx"], batches, pantry=empty)
        assert got == base


# --------------------------------------------------------------------------- #
#  deduction happens BEFORE pack rounding — exact pack arithmetic
# --------------------------------------------------------------------------- #
def test_stock_deducts_before_pack_rounding():
    batches = {"rice": 4}                    # need 4 * 500 = 2000g dry rice
    rows0, _, waste0 = purchase(COMPS, ING, ["rice"], batches)
    name, need0, units0, pack, left0, per, keeps = rows_by_name(rows0)["white_rice_dry"]
    assert (need0, units0, pack) == (2000, 3, 907)   # ceil(2000/907) = 3
    assert left0 == 3 * 907 - 2000                   # 721

    pantry = {"schema_version": 1,
              "stock": [{"ingredient": "white_rice_dry", "grams": 1400,
                         "acquired": "2026-08-01"}]}
    rows1, _, waste1 = purchase(COMPS, ING, ["rice"], batches, pantry=pantry)
    name, need1, units1, pack1, left1, per1, keeps1 = rows_by_name(rows1)["white_rice_dry"]
    # the deduction lands on grams FIRST, then rounds: ceil((2000-1400)/907)
    assert need1 == 2000 - 1400 == 600
    assert units1 == 1
    assert left1 == 1 * 907 - 600                    # 307
    assert units0 - units1 == 2                      # packs drop by exactly 2
    assert waste0 - waste1 == left0 - left1


def test_stock_split_across_entries_aggregates():
    batches = {"rice": 4}
    pantry = {"schema_version": 1,
              "stock": [{"ingredient": "white_rice_dry", "grams": 1000,
                         "acquired": "2026-08-01"},
                        {"ingredient": "white_rice_dry", "grams": 400,
                         "acquired": "2026-08-05"}]}
    rows, _, _ = purchase(COMPS, ING, ["rice"], batches, pantry=pantry)
    _, need, units, *_ = rows_by_name(rows)["white_rice_dry"]
    assert (need, units) == (600, 1)


def test_stock_exceeding_need_floors_at_zero():
    pantry = {"schema_version": 1,
              "stock": [{"ingredient": "white_rice_dry", "grams": 99999,
                         "acquired": "2026-08-01"}]}
    rows, wp, wt = purchase(COMPS, ING, ["rice"], {"rice": 4}, pantry=pantry)
    _, need, units, pack, left, *_ = rows_by_name(rows)["white_rice_dry"]
    assert (need, units, left) == (0, 0, 0)          # never negative demand
    assert wt == 0


def test_menu_cost_reflects_pantry():
    batches = {"rice": 4}
    pantry = {"schema_version": 1,
              "stock": [{"ingredient": "white_rice_dry", "grams": 1400,
                         "acquired": "2026-08-01"}]}
    assert menu_cost(COMPS, ING, ["rice"], batches) == 3 * 3.50
    assert menu_cost(COMPS, ING, ["rice"], batches, pantry=pantry) == 1 * 3.50


# --------------------------------------------------------------------------- #
#  validation: all-errors, exact locations
# --------------------------------------------------------------------------- #
def write_pantry(tmp_path, doc):
    p = tmp_path / "pantry.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return p


def test_invalid_pantry_is_one_all_errors_validation_error(tmp_path):
    doc = {"schema_version": 1,
           "stock": [
               {"ingredient": "ghost_grain", "grams": 500,
                "acquired": "2026-08-01"},                    # unknown ref
               {"ingredient": "white_rice_dry", "grams": 500,
                "acquired": "not-a-date"},                    # bad date
               {"ingredient": "chicken", "grams": -50,
                "acquired": "2026-08-01"},                    # negative grams
           ]}
    path = write_pantry(tmp_path, doc)
    with pytest.raises(ValidationError) as ei:
        load_pantry(path, known_ingredients=set(ING),
                    known_components=set(COMPS))
    errs = ei.value.errors
    assert len(errs) == 3, [str(e) for e in errs]
    codes = {e.code for e in errs}
    assert codes == {"unknown_ingredient", "bad_date", "nonpositive_grams"}
    by_code = {e.code: e for e in errs}
    assert "ghost_grain" in by_code["unknown_ingredient"].where
    assert "stock[1]" in by_code["bad_date"].where
    assert "grams" in by_code["nonpositive_grams"].where


def test_cooked_entries_validated_too():
    doc = {"schema_version": 1,
           "cooked": [{"component": "no_such_dish", "grams": 0,
                       "cooked": "2026-99-99"}]}
    issues = validate_pantry_doc(doc, known_ingredients=set(ING),
                                 known_components=set(COMPS))
    codes = {i.code for i in issues if i.severity == "error"}
    assert {"unknown_component", "nonpositive_grams", "bad_date"} <= codes


def test_missing_schema_version_and_bad_shapes():
    issues = validate_pantry_doc({"stock": "not-a-list"})
    codes = {i.code for i in issues if i.severity == "error"}
    assert "missing_field" in codes          # schema_version
    assert "bad_document" in codes           # stock must be a list


def test_missing_fields_reported_per_entry():
    issues = validate_pantry_doc({"schema_version": 1, "stock": [{}]},
                                 known_ingredients=set(ING))
    missing = {i.where for i in issues if i.code == "missing_field"}
    assert any("'ingredient'" in w for w in missing)
    assert any("'grams'" in w for w in missing)
    assert any("'acquired'" in w for w in missing)


def test_yaml_native_dates_are_accepted(tmp_path):
    # yaml.safe_load turns `acquired: 2026-08-01` into datetime.date — both
    # the native date and the ISO string form must validate
    path = tmp_path / "pantry.yaml"
    path.write_text("schema_version: 1\n"
                    "stock:\n"
                    "  - {ingredient: white_rice_dry, grams: 100, "
                    "acquired: 2026-08-01}\n")
    pantry = load_pantry(path, known_ingredients=set(ING))
    assert pantry.stock[0]["ingredient"] == "white_rice_dry"


# --------------------------------------------------------------------------- #
#  CLI: --pantry PATH is optional; an invalid path is a clean exit
# --------------------------------------------------------------------------- #
def test_cli_rejects_missing_pantry_file(tmp_path):
    with pytest.raises(SystemExit) as ei:
        cli.main(["shop", "--library", str(EXAMPLES),
                  "--pantry", str(tmp_path / "nope.yaml")])
    assert "pantry" in str(ei.value).lower() or "nope.yaml" in str(ei.value)


def test_cli_rejects_invalid_pantry_all_errors(tmp_path):
    doc = {"schema_version": 1,
           "stock": [{"ingredient": "ghost_grain", "grams": -1,
                      "acquired": "nope"}]}
    path = write_pantry(tmp_path, doc)
    with pytest.raises(SystemExit) as ei:
        cli.main(["shop", "--library", str(EXAMPLES),
                  "--pantry", str(path)])
    msg = str(ei.value)
    assert "unknown_ingredient" in msg
    assert "bad_date" in msg
    assert "nonpositive_grams" in msg
