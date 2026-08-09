"""M0.8 + M0.13 regression tests.

M0.8 — plate() bound discipline:
- property: over >=20 seeds, with random weights and random (possibly
  out-of-bounds) pins, no emitted portion violates serve bounds or unit
  multiples;
- serve_g bounds not aligned to unit_g are a validation ERROR (was a warning);
- pins are clamped into bounds and snapped onto the unit grid by default;
- allow_out_of_bounds=True honors the raw pin and attaches a structured
  warning to the result.

M0.13 — replate() day-correctness:
- the menu is filtered by available_on BEFORE solving (v1's replate ignored
  availability entirely);
- a lock on an unavailable component is dropped with a structured warning;
- replate on a day where everything is available behaves exactly like plate
  on the available subset.
"""

import random
from pathlib import Path

import pytest
import yaml

from mealplan import engine, io_yaml
from mealplan.engine import available_on
from mealplan.io_yaml import ValidationError

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"


@pytest.fixture(scope="module")
def lib():
    return io_yaml.load(EXAMPLES)


# --------------------------------------------------------------------------- #
#  M0.8 — property: emitted portions always in bounds and on the unit grid
# --------------------------------------------------------------------------- #
def test_portions_within_bounds_and_on_grid_property(lib):
    """>=20 seeds x random weights x random raw pins (often out of bounds /
    off grid): every emitted portion must satisfy serve_g min/max and, for
    discrete components, land on a whole-unit multiple."""
    _, comps, people, _ = lib
    ids = list(comps)
    persons = list(people.values())
    for seed in range(20):
        rng = random.Random(seed)
        p = persons[seed % len(persons)]
        w = {i: rng.uniform(0.35, 2.4) for i in ids}
        # pin one random component at a random raw gram value — sometimes
        # below min, above max, or off the unit grid. Default policy clamps.
        pin_id = rng.choice(ids)
        pins = {pin_id: rng.uniform(0, 700)}
        ok, pl, miss = engine.plate(p, comps, ids, weights=w, locked=pins)
        for cid, g in pl.items():
            lo, hi = comps[cid]["serve_g"]["min"], comps[cid]["serve_g"]["max"]
            assert lo <= g <= hi, (seed, cid, g, lo, hi)
            u = comps[cid].get("unit_g")
            if u:
                assert g % u == 0, (seed, cid, g, u)


# --------------------------------------------------------------------------- #
#  M0.8 — unaligned serve bounds are a validation ERROR
# --------------------------------------------------------------------------- #
ING_DOC = {
    "schema_version": 1,
    "ingredients": {
        "chicken": {"p": 22.5, "f": 2.6, "c": 0.0, "perishable": True,
                    "pack_g": 907, "keeps_days": 3, "tags": [], "cost": 9.0},
        "rice": {"p": 7.1, "f": 0.7, "c": 80.0, "perishable": False,
                 "pack_g": 907, "keeps_days": 999, "tags": [], "cost": 3.5},
    },
}

PPL_DOC = {
    "schema_version": 1,
    "people": {
        "p1": {"targets": {"protein": 150, "fat": 80, "carb": 300},
               "tolerance": 0.05},
    },
    "settings": {"days": 7, "active_min_budget": 180,
                 "max_days_same_component": 4, "cook_days": [0, 4]},
}


def test_unaligned_bounds_library_fails_load(tmp_path):
    comp_doc = {"schema_version": 1, "components": [
        {"id": "wings", "name": "wings", "cuisine": "test", "role": "main",
         "yield_g": 1000, "unit_g": 45, "serve_g": {"min": 90, "max": 500},
         "keeps_days": 4, "active_min": 10,
         "ingredients": {"chicken": 800, "rice": 200}},
    ]}
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(ING_DOC))
    (lib / "components.yaml").write_text(yaml.safe_dump(comp_doc))
    (lib / "people.yaml").write_text(yaml.safe_dump(PPL_DOC))
    with pytest.raises(ValidationError) as ei:
        io_yaml.load(lib)
    assert "serve_bounds_not_unit_aligned" in ei.value.codes()
    assert any(i.severity == "error"
               and i.code == "serve_bounds_not_unit_aligned"
               for i in ei.value.issues)


def test_examples_corpus_is_unit_aligned(lib):
    """The corpus fix: mango_jalapeno_wings max 500 -> 495 (11 x 45g wings).
    Every discrete component's bounds sit on its own unit grid."""
    _, comps, _, _ = lib
    assert comps["mango_jalapeno_wings"]["serve_g"]["max"] == 495
    for cid, c in comps.items():
        u = c.get("unit_g")
        if u:
            assert c["serve_g"]["min"] % u == 0, cid
            assert c["serve_g"]["max"] % u == 0, cid


# --------------------------------------------------------------------------- #
#  M0.8 — pinned portions
# --------------------------------------------------------------------------- #
def test_pin_below_min_clamps_up(lib):
    _, comps, people, _ = lib
    p = people["devon"]
    ids = list(comps)
    # turkey_meatballs: unit_g 40, serve_g {min: 80, max: 400}
    ok, pl, miss = engine.plate(p, comps, ids, locked={"turkey_meatballs": 30})
    assert pl.get("turkey_meatballs") == 80  # clamped up to serve_min


def test_pin_above_max_clamps_down(lib):
    _, comps, people, _ = lib
    p = people["devon"]
    ids = list(comps)
    ok, pl, miss = engine.plate(p, comps, ids, locked={"turkey_meatballs": 900})
    assert pl.get("turkey_meatballs") == 400  # clamped down to serve_max


def test_pin_off_grid_snaps_to_unit(lib):
    _, comps, people, _ = lib
    p = people["devon"]
    ids = list(comps)
    ok, pl, miss = engine.plate(p, comps, ids, locked={"turkey_meatballs": 130})
    assert pl.get("turkey_meatballs") == 120  # round(130/40)=3 units


def test_allow_out_of_bounds_honors_pin_and_warns(lib):
    _, comps, people, _ = lib
    p = people["devon"]
    ids = list(comps)
    res = engine.plate(p, comps, ids, locked={"turkey_meatballs": 30},
                       allow_out_of_bounds=True)
    ok, pl, miss = res           # still unpacks like the classic 3-tuple
    assert pl.get("turkey_meatballs") == 30  # raw pin honored
    assert any(w["code"] == "pin_out_of_bounds"
               and w["component"] == "turkey_meatballs"
               for w in res.warnings)


def test_in_bounds_pin_produces_no_warning(lib):
    _, comps, people, _ = lib
    p = people["devon"]
    ids = list(comps)
    res = engine.plate(p, comps, ids, locked={"turkey_meatballs": 160},
                       allow_out_of_bounds=True)
    assert res[1].get("turkey_meatballs") == 160
    assert not res.warnings


# --------------------------------------------------------------------------- #
#  M0.13 — replate day-correctness
# --------------------------------------------------------------------------- #
def test_replate_excludes_expired_component(lib):
    """guacamole keeps 2 days. With cook sessions on days 0 and 3, day 5 is
    beyond both (5-0=5, 5-3=2 — both >= keeps_days): a day-5 replate must not
    serve it, even though the v1 prototype's replate would have."""
    _, comps, people, _ = lib
    settings = {"cook_days": [0, 3], "days": 7}
    assert comps["guacamole"]["keeps_days"] == 2
    assert not available_on(comps["guacamole"], 5, settings)
    p = people["devon"]
    menu = list(comps)
    res = engine.replate(p, comps, menu, day=5, settings=settings)
    ok, pl, miss = res
    assert "guacamole" not in pl
    for cid in pl:
        assert available_on(comps[cid], 5, settings), cid
    # a menu of ONLY the expired component yields an empty, infeasible plate
    res2 = engine.replate(p, comps, ["guacamole"], day=5, settings=settings)
    assert res2[0] is False and res2[1] == {}


def test_replate_locked_unavailable_warns_and_drops(lib):
    _, comps, people, _ = lib
    settings = {"cook_days": [0, 3], "days": 7}
    p = people["devon"]
    menu = list(comps)
    res = engine.replate(p, comps, menu, day=5, settings=settings,
                         locked={"guacamole": 100})
    ok, pl, miss = res
    assert "guacamole" not in pl                     # dropped, not honored
    assert any(w["code"] == "locked_unavailable"
               and w["component"] == "guacamole" and w["day"] == 5
               for w in res.warnings)                # structured, not a crash


def test_replate_day1_matches_plate_on_available_subset(lib):
    _, comps, people, settings = lib
    p = people["devon"]
    menu = list(comps)
    avail = [i for i in menu if available_on(comps[i], 1, settings)]
    assert avail and len(avail) < len(menu) or avail  # sanity: non-empty
    got = engine.replate(p, comps, menu, day=1, settings=settings)
    want = engine.plate(p, comps, avail)
    assert got[0] == want[0]
    assert got[1] == want[1]
    assert got[2] == want[2]
    assert got.warnings == []
