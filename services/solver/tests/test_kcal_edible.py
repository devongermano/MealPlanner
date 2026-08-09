"""M0.9 (Atwater-only kcal) + M0.7 (edible_fraction) regression tests.

M0.9: kcal is never stored on an ingredient — it is DERIVED from macros by
Atwater 4/9/4, always. Storing it is a validation error (two-accounting
defect). Intentionally-zero-macro ingredients must carry ``negligible: true``
or validation warns.

M0.7: optional ingredient ``edible_fraction`` (0 < f <= 1, default 1.0).
Component macro derivation multiplies each ingredient's contribution by its
edible_fraction; yield_g and portions remain GROSS weight (PRD §8.1).
"""

from pathlib import Path

import pytest
import yaml

from mealplan.io_yaml import ValidationError, validate_ingredients_doc
from mealplan.model import Ingredient, derive_component

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"

KCAL_MSG = "ingredient kcal is not stored; kcal derives from macros (Atwater 4/9/4)"


def ing(**over):
    d = {"p": 20.0, "f": 5.0, "c": 10.0, "perishable": True,
         "pack_g": 500, "keeps_days": 5, "tags": [], "cost": 4.0}
    d.update(over)
    return d


def ing_doc(**ings):
    return {"schema_version": 1, "ingredients": dict(ings)}


def errors_of(issues):
    return [i for i in issues if i.severity == "error"]


def warnings_of(issues):
    return [i for i in issues if i.severity == "warning"]


def build_comp(ingredients_map, grams, yield_g=1000):
    """Derive a one-off component from {iid: raw_ingredient_dict}."""
    ing_objs = {iid: Ingredient.from_raw(iid, d)
                for iid, d in ingredients_map.items()}
    c = {"id": "x", "name": "x", "cuisine": "test", "role": "main",
         "yield_g": yield_g, "serve_g": {"min": 50, "max": 400},
         "keeps_days": 5, "active_min": 10, "ingredients": grams}
    return derive_component(c, ing_objs)


# --------------------------------------------------------------------------- #
#  M0.9 — kcal is Atwater-only
# --------------------------------------------------------------------------- #
def test_stored_kcal_is_validation_error():
    doc = ing_doc(chicken=ing(kcal=120))
    errs = errors_of(validate_ingredients_doc(doc))
    assert len(errs) == 1
    assert "kcal" in errs[0].where
    assert errs[0].message == KCAL_MSG


def test_per100_kcal_equals_atwater_of_same_row():
    comp = build_comp({"a": ing(p=20.0, f=5.0, c=10.0),
                       "b": ing(p=3.0, f=1.0, c=60.0, perishable=False)},
                      {"a": 600, "b": 400})
    pc = comp["per100"]
    assert pc["kcal"] == pytest.approx(
        4 * pc["protein"] + 9 * pc["fat"] + 4 * pc["carb"], abs=1e-6)


def test_all_zero_macros_warns_without_negligible_flag():
    doc = ing_doc(mystery=ing(p=0.0, f=0.0, c=0.0))
    issues = validate_ingredients_doc(doc)
    assert not errors_of(issues)
    warns = warnings_of(issues)
    assert len(warns) == 1
    assert warns[0].code == "all_zero_macros"
    assert "mystery" in warns[0].where


def test_all_zero_macros_with_negligible_flag_does_not_warn():
    doc = ing_doc(spices=ing(p=0.0, f=0.0, c=0.0, negligible=True))
    assert not validate_ingredients_doc(doc)


def test_examples_corpus_is_atwater_clean():
    """Corpus LINT (schema-shape only, no value pins — PRD §9): no stored
    kcal anywhere, and every intentionally-zero-macro ingredient carries
    the negligible flag (implied by the absence of all_zero_macros
    warnings)."""
    doc = yaml.safe_load((EXAMPLES / "ingredients.yaml").read_text())
    issues = validate_ingredients_doc(doc)
    assert not errors_of(issues), [str(i) for i in issues]
    assert not [w for w in warnings_of(issues) if w.code == "all_zero_macros"]
    for iid, d in doc["ingredients"].items():
        assert "kcal" not in d, iid


# --------------------------------------------------------------------------- #
#  M0.7 — edible_fraction
# --------------------------------------------------------------------------- #
def test_edible_fraction_halves_macros_at_constant_yield():
    grams = {"meat": 800}
    full = build_comp({"meat": ing(p=20.0, f=10.0, c=0.0)}, grams)
    half = build_comp({"meat": ing(p=20.0, f=10.0, c=0.0,
                                   edible_fraction=0.5)}, grams)
    # yield (and therefore portion weights) stays GROSS…
    assert full["yield_g"] == half["yield_g"] == 1000
    # …but macros apply only to the edible share
    assert half["per100"]["protein"] == pytest.approx(
        full["per100"]["protein"] / 2, abs=1e-3)
    assert half["per100"]["fat"] == pytest.approx(
        full["per100"]["fat"] / 2, abs=1e-3)
    assert half["per100"]["kcal"] == pytest.approx(
        full["per100"]["kcal"] / 2, abs=1e-2)


def test_edible_fraction_defaults_to_one():
    explicit = build_comp({"meat": ing(edible_fraction=1.0)}, {"meat": 500})
    implicit = build_comp({"meat": ing()}, {"meat": 500})
    assert explicit["per100"] == implicit["per100"]
    assert Ingredient.from_raw("meat", ing()).edible_fraction == 1.0


@pytest.mark.parametrize("bad", [0, 0.0, -0.5, 1.001, 2, "most", True])
def test_edible_fraction_out_of_range_is_error(bad):
    doc = ing_doc(meat=ing(edible_fraction=bad))
    errs = errors_of(validate_ingredients_doc(doc))
    assert len(errs) == 1
    assert errs[0].code == "bad_edible_fraction"
    assert "meat" in errs[0].where


@pytest.mark.parametrize("ok", [0.001, 0.66, 1, 1.0])
def test_edible_fraction_in_range_is_accepted(ok):
    doc = ing_doc(meat=ing(edible_fraction=ok))
    assert not errors_of(validate_ingredients_doc(doc))


def test_examples_corpus_exercises_edible_fraction():
    """Corpus LINT: the live corpus actually uses the M0.7 feature — at
    least one bone-in ingredient declares a partial edible_fraction, and
    every declared value is in (0, 1]. Which ingredient and what value are
    corpus data, deliberately not pinned (PRD §9)."""
    doc = yaml.safe_load((EXAMPLES / "ingredients.yaml").read_text())
    declared = {iid: d["edible_fraction"]
                for iid, d in doc["ingredients"].items()
                if "edible_fraction" in d}
    assert declared, "no ingredient declares edible_fraction"
    assert all(0 < v <= 1 for v in declared.values()), declared
    assert any(v < 1 for v in declared.values()), \
        "expected at least one partial (bone-in) edible_fraction"
