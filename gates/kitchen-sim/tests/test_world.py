"""Tests for the simulated world.

The gate is only as trustworthy as its instrument, so the instrument gets the
same treatment as the engine: real assertions with named failure modes, and
non-vacuity checks that fail if the thing under test is broken. A gate whose
own store cannot tell "we don't sell that" from "you didn't look" has no
business reporting on anyone else's software.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitchen_sim.errors import UnknownSku          # noqa: E402
from kitchen_sim.sheets import (_stem, parse_cook_plan,   # noqa: E402
                                parse_shopping_list, stated_total)
from kitchen_sim.world import Sheets, Store, World  # noqa: E402

CATALOG = Path(__file__).resolve().parents[1] / "kitchen_sim" / "catalog.yaml"
SHEETS = Path("/Users/devon/.claude/jobs/e7909e8e/tmp/mainsheets/artifacts")


@pytest.fixture(scope="module")
def store():
    return Store.load(CATALOG)


# --------------------------------------------------------------------------- #
#  the store
# --------------------------------------------------------------------------- #
def test_catalog_has_no_spices_product(store):
    """CATCHES the single most important way this gate could be rigged.

    'spices' is a solver-internal category, not a thing a shop sells. If the
    catalog had been generated from the planner's own ingredient data a
    `spices` SKU would exist, prediction P1 would be structurally
    unreachable, and the gate would silently lose its most quotable finding.
    This test asserts the catalog's independence where it is checkable.
    """
    assert store.search("spices") == []
    assert "spices" not in store.skus


def test_search_finds_real_products_and_misses_nonexistent(store):
    assert store.search("chicken breast"), "a supermarket staple must resolve"
    assert store.search("cilantro"), "produce must resolve"
    assert store.search("unobtainium flakes") == []


def test_search_is_deterministic(store):
    """The gate compares runs; a search that reorders between calls would
    make every finding non-reproducible."""
    for q in ("rice", "chicken", "tortilla"):
        assert [s.id for s in store.search(q)] == [s.id for s in
                                                   store.search(q)]


def test_count_and_bunch_items_are_modelled_as_such(store):
    """The unit_untranslatable finding only exists because the store knows
    limes are sold each and cilantro by the bunch. If this collapses to
    weight, a third of the real findings evaporate."""
    kinds = {s.sold_by for s in store.skus.values()}
    assert {"weight", "count", "bunch"} <= kinds


# --------------------------------------------------------------------------- #
#  parsing
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not SHEETS.exists(), reason="no generated sheets on disk")
def test_shopping_list_parses_every_checkbox_row():
    text = (SHEETS / "shopping_list.md").read_text()
    rows = parse_shopping_list(text)
    raw_count = sum(1 for ln in text.splitlines() if ln.startswith("- [ ] "))
    assert len(rows) == raw_count, "a checkbox row was silently dropped"
    assert all(r.need_g > 0 for r in rows if not r.buys_nothing)
    assert stated_total(text) is not None


@pytest.mark.skipif(not SHEETS.exists(), reason="no generated sheets on disk")
def test_cook_plan_parses_mise_and_steps():
    facts = parse_cook_plan((SHEETS / "cook_plan.md").read_text())
    assert facts.mise, "no batch-scaled ingredient lines found"
    assert facts.step_lines, "no annotated cook steps found"
    assert all(g > 0 for g in facts.mise.values())


@pytest.mark.parametrize("word,expect", [
    ("limes", "lime"), ("tomatoes", "tomato"), ("potatoes", "potato"),
    ("berries", "berry"), ("salt", "salt"), ("glass", "glass"),
])
def test_stemmer_folds_plurals_without_mangling_singulars(word, expect):
    """Two false-positive rounds came from plural mismatch; this pins the
    fix. 'glass' must survive intact — over-stemming would start matching
    unrelated foods."""
    assert _stem(word) == expect


# --------------------------------------------------------------------------- #
#  the world's refusals
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not SHEETS.exists(), reason="no generated sheets on disk")
def test_cannot_buy_what_you_have_not_seen(store):
    """The cook must interact with the world to learn it exists — no reading
    SKU ids off a list it was never shown."""
    w = World(store, Sheets(SHEETS), {})
    some = next(iter(store.skus))
    with pytest.raises(UnknownSku):
        w.add_to_cart(some, 1)
    w.inspect(some)
    assert "Added" in w.add_to_cart(some, 1)


@pytest.mark.skipif(not SHEETS.exists(), reason="no generated sheets on disk")
def test_assume_is_recorded_as_a_finding(store):
    """The assumption chokepoint is the countermeasure to the cook silently
    patching gaps with its own world knowledge. If assumptions stopped being
    recorded, the gate would look cleaner while measuring less."""
    w = World(store, Sheets(SHEETS), {})
    w.assume("vessel", "8-quart pot", "no pot size was stated")
    assert any(f.defect_class == "assumption_required" for f in w.findings)
    assert w.assumptions[0]["field"] == "vessel"


@pytest.mark.skipif(not SHEETS.exists(), reason="no generated sheets on disk")
def test_full_naive_trip_finds_the_must_hit_predictions(store):
    """The gate's own non-vacuity test, and the reason PREDICTIONS.md exists.

    CATCHES a gate that has quietly stopped working: if reconciliation
    breaks, this run goes clean and the failure looks like good news. Pinned
    to the four must-hit predictions plus the zero-false-positive bar.
    """
    from kitchen_sim.naive_shopper import shop
    w = World(store, Sheets(SHEETS), {})
    shop(w)

    classes = {f.defect_class for f in w.findings}
    assert "product_not_found" in classes, "P1: 'spices' must not be buyable"
    assert "unit_untranslatable" in classes, "P2: count/bunch items"

    prose = {f.state.get("ingredient") for f in w.findings
             if f.state.get("from_prose")}
    assert "salt" in prose, "P4: salt is cooked with but never bought"
    assert "water" in prose, "P5: water likewise"
    assert prose == {"salt", "water"}, (
        f"prose scan regressed to false positives: {sorted(prose)}")
