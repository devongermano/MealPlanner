"""M0.1 extraction smoke tests.

Scope: prove the extracted package behaves like the prototype on the examples
corpus. The full capability suite (synthetic fixtures, PRD §9) is Phase 3 —
these tests only guard the extraction itself.

The prototype (`<repo>/mealplan/plan.py`) is imported directly via a sys.path
trick to COMPUTE the expected values — acceptable in tests only.
"""

import sys
from pathlib import Path

import pytest

from mealplan import engine, io_yaml
from mealplan.costing import purchase

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"
PROTOTYPE_DIR = REPO / "mealplan"


@pytest.fixture(scope="module")
def lib():
    return io_yaml.load(EXAMPLES)


@pytest.fixture(scope="module")
def proto_plan():
    """The v1 prototype, imported as a module (tests only).

    Retirement-safe: when the frozen prototype directory is gone, the parity
    test SKIPS instead of blocking `mealplan/` deletion — the parity it
    proves was recorded at extraction time and the derived values are pinned
    by the determinism golden from then on. The sys.path entry is removed
    immediately after import (and the module dropped from sys.modules at
    teardown) so the prototype's top-level modules (plan.py, serve.py) can
    never shadow later imports for the rest of the pytest session."""
    if not (PROTOTYPE_DIR / "plan.py").exists():
        pytest.skip("v1 prototype (mealplan/plan.py) retired — extraction "
                    "parity was proven before retirement")
    sys.path.insert(0, str(PROTOTYPE_DIR))
    try:
        import plan
    finally:
        sys.path.remove(str(PROTOTYPE_DIR))
    yield plan
    sys.modules.pop("plan", None)


def test_examples_corpus_loads(lib):
    ing, comps, people, settings = lib
    assert len(ing) > 50
    assert len(comps) > 20
    assert set(people) == {"jimbo", "devon"}
    assert settings["days"] == 7
    for cid, c in comps.items():
        assert set(c["per100"]) == {"kcal", "protein", "fat", "carb"}, cid
        assert isinstance(c["tags"], list), cid


def test_derived_per100_matches_prototype(lib, proto_plan):
    """Derived macros for cilantro_lime_rice and birria_chuck must match the
    prototype's derivation to 3 decimals (expected values COMPUTED by running
    the prototype's own load()).

    kcal is deliberately excluded from prototype parity: M0.9 made kcal
    Atwater-only (4/9/4 from the derived macros) while the prototype summed
    label kcal — that two-accounting defect is the thing M0.9 removed. kcal is
    instead asserted to be exactly Atwater over the same per100 row."""
    _, comps, _, _ = lib
    _, pcomps, _, _ = proto_plan.load()
    for cid in ("cilantro_lime_rice", "turkey_meatballs", "birria_chuck"):
        for mac in ("protein", "fat", "carb"):
            got = comps[cid]["per100"][mac]
            assert got == pytest.approx(pcomps[cid]["per100"][mac],
                                        abs=1e-3), (cid, mac)
        pc = comps[cid]["per100"]
        assert pc["kcal"] == pytest.approx(
            4 * pc["protein"] + 9 * pc["fat"] + 4 * pc["carb"], abs=1e-3), cid
        assert comps[cid]["tags"] == pcomps[cid]["tags"], cid


def test_doctor_produces_report(lib):
    ing, comps, people, settings = lib
    docmsg, data = engine.doctor(comps, people, settings)
    assert "## Feasibility" in docmsg
    assert "## Structural check" in docmsg
    for pname in people:
        assert pname in docmsg
    # M0.11: the second return value is the structured mirror of the report
    assert isinstance(data, dict) and data


def test_plate_feasible_for_each_person(lib):
    ing, comps, people, settings = lib
    ids = list(comps)
    for pname, p in people.items():
        ok, pl, miss = engine.plate(p, comps, ids)
        assert ok, f"{pname} infeasible on full library: {miss}"
        assert pl, f"{pname} got an empty plate"
        # excluded tags never appear on the plate
        excl = set(p.get("exclude") or [])
        for cid in pl:
            assert not (set(comps[cid]["tags"]) & excl), (pname, cid)


def test_build_week_returns_seven_days_per_person(lib):
    ing, comps, people, settings = lib
    menu = list(comps)
    weeks, demand = engine.build_week(comps, people, settings, menu)
    assert set(weeks) == set(people)
    for pname, wk in weeks.items():
        assert len(wk) == 7, f"{pname} week has {len(wk)} days"
        assert any(pl for pl in wk), f"{pname} got an all-empty week"
    assert demand, "no demand accumulated"
    for cid, g in demand.items():
        assert cid in comps and g > 0


def test_purchase_rows_cover_ingredient_need(lib):
    ing, comps, people, settings = lib
    chosen = ["picadillo", "cilantro_lime_rice", "birria_chuck",
              "turkey_meatballs", "guacamole"]
    rows, wp, wt = purchase(comps, ing, chosen)
    # every ingredient the chosen components need appears in the rows
    needed = set()
    for cid in chosen:
        needed |= set(comps[cid]["ingredients"])
    assert {r[0] for r in rows} == needed
    # and the purchased units cover the gram need
    for name, need, units, pack, left, per, keeps in rows:
        assert units * pack >= need, name
        assert left == units * pack - need, name
    assert wt >= wp >= 0
