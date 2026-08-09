"""M0.11 doctor-diagnostics regression tests (PRD §8.3).

- binding_macro: tighten tolerance until the plate LP first misses; the
  first-missed macro (signed direction) is the binding constraint per person;
- volume_floor: bisect max_daily_mass_g in [500, 8000] at 25g resolution —
  the generic form of the reproduced ~2,121 g/day (~4.7 lb) finding — plus
  which macro binds at the floor;
- lean_coverage: the REAL shelf-life stagger check — per-day lean-anchor
  availability from cook_days + keeps_days (+freezer bridging when enabled);
  score_menu's boolean no-op (prototype plan.py:397-398, a len(set-of-bools)
  expression) is replaced by an actual uncovered-day penalty;
- carb_headroom: per-person per-day carb availability vs target; score_menu
  uses worst-day headroom — the flat 1.45x whole-library fudge is gone;
- doctor returns (rendered_text, structured_data).
"""

from pathlib import Path

import pytest

from mealplan import engine, io_yaml

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"


# --------------------------------------------------------------------------- #
#  synthetic fixtures — plain dicts, engine reads them dict-style
# --------------------------------------------------------------------------- #
def _comp(cid, per100, role="main", serve=(100, 400), keeps=5, anchor=None,
          yield_g=500):
    d = dict(id=cid, name=cid, cuisine="test", role=role, yield_g=yield_g,
             serve_g={"min": serve[0], "max": serve[1]}, keeps_days=keeps,
             active_min=20, ingredients={"stuff": 100}, per100=per100,
             tags=[])
    if anchor:
        d["anchor"] = anchor
    return d


ING = {"stuff": dict(p=10.0, f=5.0, c=10.0, perishable=False, keeps_days=999,
                     pack_g=500, cost=4.0, tags=[])}


def _person(targets, tol=0.05):
    return dict(targets=targets, tolerance=tol, exclude=[], dislikes=[])


SET = dict(days=7, active_min_budget=600, batch_time_factor=0.5,
           max_days_same_component=7, cook_days=[0, 4], shop_days=[0])


# --------------------------------------------------------------------------- #
#  1. binding-macro identification
# --------------------------------------------------------------------------- #
def test_binding_macro_outsized_carb_target_binds_carb():
    """A person whose carb target dwarfs what the library can serve: carbs
    are the binding macro, direction SHORT (negative signed miss)."""
    comps = {
        "meat": _comp("meat", {"kcal": 145.0, "protein": 25.0, "fat": 5.0,
                               "carb": 0.0}, serve=(100, 500)),
        "rice": _comp("rice", {"kcal": 112.5, "protein": 2.0, "fat": 0.5,
                               "carb": 25.0}, role="starch", serve=(100, 400)),
    }
    person = _person({"protein": 100, "fat": 20, "carb": 400})
    bm = engine.binding_macro(person, comps)
    assert bm is not None
    assert bm["macro"] == "carb"
    assert bm["direction"] == "short"
    assert bm["signed_miss_g"] < 0


# --------------------------------------------------------------------------- #
#  2. volume floor
# --------------------------------------------------------------------------- #
def test_volume_floor_jimbo_examples_reproduces_2121_finding():
    """The reproduced prototype finding: jimbo's targets need ~2,121g/day
    (~4.7 lb) on the examples library. The generic bisect must land in
    [2050, 2200] and name carbs as what binds at the floor."""
    ing, comps, people, settings = io_yaml.load(EXAMPLES)
    vf = engine.volume_floor(people["jimbo"], comps)
    assert vf["floor_g"] is not None
    assert 2050 <= vf["floor_g"] <= 2200, vf
    assert vf["binding"] and vf["binding"]["macro"] == "carb"
    assert vf["binding"]["direction"] == "short"


def test_volume_floor_watery_carbs_higher_than_dense():
    """Same carb target: a watery carb library (10 c/100g) needs far more
    daily mass than a dense one (70 c/100g)."""
    person = _person({"protein": 0, "fat": 0, "carb": 300})
    watery = {"wet": _comp("wet", {"kcal": 40.0, "protein": 0.0, "fat": 0.0,
                                   "carb": 10.0},
                           role="starch", serve=(100, 4000))}
    dense = {"dry": _comp("dry", {"kcal": 280.0, "protein": 0.0, "fat": 0.0,
                                  "carb": 70.0},
                          role="starch", serve=(100, 1000))}
    fw = engine.volume_floor(person, watery)
    fd = engine.volume_floor(person, dense)
    assert fw["floor_g"] is not None and fd["floor_g"] is not None
    assert fw["floor_g"] > fd["floor_g"], (fw, fd)


# --------------------------------------------------------------------------- #
#  3. real stagger check
# --------------------------------------------------------------------------- #
def _lean_lib(keeps_a, keeps_b):
    per_lean = {"kcal": 98.0, "protein": 20.0, "fat": 2.0, "carb": 0.0}
    return {
        "leanA": _comp("leanA", dict(per_lean), keeps=keeps_a, anchor="lean"),
        "leanB": _comp("leanB", dict(per_lean), keeps=keeps_b, anchor="lean"),
        "spuds": _comp("spuds", {"kcal": 137.0, "protein": 2.0, "fat": 1.0,
                                 "carb": 30.0}, role="starch", keeps=7,
                       serve=(100, 600)),
        "slaw": _comp("slaw", {"kcal": 60.0, "protein": 1.0, "fat": 4.0,
                               "carb": 5.0}, role="accent", keeps=7),
        "dip": _comp("dip", {"kcal": 60.0, "protein": 1.0, "fat": 4.0,
                             "carb": 5.0}, role="accent", keeps=7),
    }


def test_lean_coverage_expiring_together_has_hole_staggered_clean():
    """cook_days [0,4]: two lean anchors both keeping 3 days leave day 3 with
    ZERO lean availability; staggering one to 5 days closes the hole."""
    lc = engine.lean_coverage(_lean_lib(3, 3), SET)
    assert lc["uncovered_days"] == [3]
    lc2 = engine.lean_coverage(_lean_lib(3, 5), SET)
    assert lc2["uncovered_days"] == []


def test_score_menu_penalizes_actual_uncovered_lean_days():
    """The prototype no-op scored keeps-4 anchors as 'staggered' regardless
    of the cook schedule. With a single cook day 0, keeps-4 anchors leave
    days 4-6 with zero lean availability — the score must penalize that;
    keeps-7 anchors cover the week. (Both pass the old any(keeps>=4) check,
    so the old expression scored them identically.)"""
    settings = dict(SET, cook_days=[0])
    people = {"p1": _person({"protein": 100, "fat": 50, "carb": 150},
                            tol=0.2)}
    hole = _lean_lib(4, 4)
    covered = _lean_lib(7, 7)
    s_hole, _ = engine.score_menu(hole, ING, sorted(hole), settings, people)
    s_cov, _ = engine.score_menu(covered, ING, sorted(covered), settings,
                                 people)
    assert s_hole > s_cov, (s_hole, s_cov)


def test_score_menu_expiring_together_worse_than_staggered():
    people = {"p1": _person({"protein": 100, "fat": 50, "carb": 150},
                            tol=0.2)}
    together = _lean_lib(3, 3)
    staggered = _lean_lib(3, 5)
    s_t, _ = engine.score_menu(together, ING, sorted(together), SET, people)
    s_s, _ = engine.score_menu(staggered, ING, sorted(staggered), SET, people)
    assert s_t > s_s, (s_t, s_s)


# --------------------------------------------------------------------------- #
#  4. carb headroom from availability
# --------------------------------------------------------------------------- #
def _starch_lib(short_keeps):
    per_st = {"kcal": 132.5, "protein": 2.0, "fat": 0.5, "carb": 30.0}
    return {
        "meat": _comp("meat", {"kcal": 145.0, "protein": 25.0, "fat": 5.0,
                               "carb": 0.0}, keeps=7),
        "shortst": _comp("shortst", dict(per_st), role="starch",
                         keeps=short_keeps),
        "longst": _comp("longst", dict(per_st), role="starch", keeps=7),
    }


def test_carb_headroom_short_keeping_starch_flags_day_3():
    """cook_days [0,4]: a keeps-3 starch is gone on day 3, leaving only the
    long keeper — 120g of carb headroom against a 150g target. The worst
    day must be day 3 and flagged; an all-long-keeping library is clean."""
    person = _person({"protein": 80, "fat": 30, "carb": 150})
    ch = engine.carb_headroom(person, _starch_lib(3), SET)
    assert not ch["ok"]
    assert ch["worst_day"] == 3
    assert ch["worst_headroom_g"] < 150
    ch2 = engine.carb_headroom(person, _starch_lib(7), SET)
    assert ch2["ok"], ch2


def test_score_menu_uses_worst_day_headroom_not_flat_145():
    """Whole-library carb ceiling is 240g >= 150*1.45 in BOTH libraries, so
    the old flat check scored them identically. Day-correct headroom sees
    day 3 at 120g < 150g in the short library and penalizes exactly it."""
    people = {"p1": _person({"protein": 80, "fat": 30, "carb": 150},
                            tol=0.2)}
    short = _starch_lib(3)
    ok = _starch_lib(7)
    s_short, _ = engine.score_menu(short, ING, sorted(short), SET, people)
    s_ok, _ = engine.score_menu(ok, ING, sorted(ok), SET, people)
    assert s_short - s_ok == 15000, (s_short, s_ok)
    # the unexplained flat 1.45x multiplier is really gone from the code
    # (comments may still name it as the removed prototype fudge)
    assert "* 1.45" not in Path(engine.__file__).read_text()


# --------------------------------------------------------------------------- #
#  5. doctor: structured data + rendered text
# --------------------------------------------------------------------------- #
def test_doctor_returns_structured_data_and_new_sections():
    ing, comps, people, settings = io_yaml.load(EXAMPLES)
    text, data = engine.doctor(comps, people, settings, ing=ing)
    for sec in ("## Raw freshness", "## Feasibility", "## Binding macro",
                "## Volume floor", "## Structural check",
                "## Lean-anchor coverage", "## Carb headroom"):
        assert sec in text, sec
    assert isinstance(data, dict)
    for key in ("raw_freshness", "feasibility", "binding_macro",
                "volume_floor", "structural", "lean_coverage",
                "carb_headroom"):
        assert key in data, key
    for pname in people:
        assert pname in data["feasibility"]
        assert pname in data["binding_macro"]
        assert pname in data["volume_floor"]
        assert pname in data["carb_headroom"]
    assert data["volume_floor"]["jimbo"]["floor_g"] == pytest.approx(2121,
                                                                     abs=100)
    assert data["lean_coverage"]["lean_anchors"]
