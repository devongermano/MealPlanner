"""M1 Phase 1 regression tests.

- M1.0 deferred-minor sweep (PHASE5_FIXNOTES.md "Deferred minors"):
  structural-failure miss SIGNS (short is negative), ineligible-pin
  structured warnings, tolerance range validation, doctor-on-demand CLI,
  founder-number residue lint, comment-proof dead-config gate helper.
- M1.7 per-person serve_g scaling (PRD §8.1 + Appendix B item 2,
  PROVISIONAL): scale = person_kcal / SCALING["reference_kcal"], clamped;
  effective bounds re-aligned to the unit grid; inversion falls back to the
  unscaled bounds with a structured warning; carb headroom / volume floor /
  score_menu consume scaled maxima.
- M1.8 pantry aging + cooked leftovers (PRD §8.1 + Appendix B item 3):
  plan_date is an explicit engine input; stock deducts only what survives to
  a cook session (remaining life = keeps_days - age); cooked leftovers join
  build_week availability and are consumed before fresh batches in
  session_plan (economy: leftovers are already paid for).
"""

import datetime
from pathlib import Path

import pytest
import yaml

from mealplan import cli, costing, engine, io_yaml, model
from mealplan.costing import age_pantry, cooked_leftovers, session_plan
from mealplan.engine import (SCALING, effective_serve_bounds, person_scale,
                             plate)
from mealplan.io_yaml import validate_people_doc
from mealplan.units import MACROS, kcal_of

from _shared import strip_comments_and_docstrings

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"
PLAN_DATE = datetime.date(2026, 8, 9)      # fixed — determinism by construction


# --------------------------------------------------------------------------- #
#  synthetic fixtures — plain dicts, engine reads them dict-style
# --------------------------------------------------------------------------- #
def _comp(cid, per100, role="main", serve=(100, 400), keeps=5, unit=None,
          yield_g=500, ingredients=None):
    d = dict(id=cid, name=cid, cuisine="test", role=role, yield_g=yield_g,
             serve_g={"min": serve[0], "max": serve[1]}, keeps_days=keeps,
             active_min=20, ingredients=ingredients or {"stuff": 100},
             per100=per100, tags=[])
    if unit:
        d["unit_g"] = unit
    return d


def _person(targets, tol=0.05, exclude=None):
    return dict(targets=targets, tolerance=tol, exclude=exclude or [],
                dislikes=[])


# a person at EXACTLY the reference kcal (4*150 + 9*100 + 4*250 = 2500):
# the golden-continuity anchor — scale 1.0, bounds untouched.
REF_TARGETS = {"protein": 150, "fat": 100, "carb": 250}

ING = {"stuff": dict(p=10.0, f=5.0, c=10.0, perishable=False, keeps_days=999,
                     pack_g=500, cost=4.0, tags=[])}

SET = model.Settings.from_raw(
    dict(days=7, active_min_budget=600, batch_time_factor=0.5,
         max_days_same_component=7, cook_days=[0, 4], shop_days=[0]),
    {"mode": "off"})


# =========================================================================== #
#  M1.0 (1) — structural-failure fallbacks report SHORT (negative) misses
# =========================================================================== #
def test_no_usable_components_misses_are_negative_short():
    """A person who can eat NOTHING is short of every target — the fallback
    must report negative (SHORT) misses, not positive full targets (which
    read as 'forced OVER' when the person got zero grams)."""
    comps = {"meat": _comp("meat", {"kcal": 145.0, "protein": 25.0,
                                    "fat": 5.0, "carb": 0.0})}
    comps["meat"]["tags"] = ["soy"]
    p = _person(REF_TARGETS, exclude=["soy"])
    res = plate(p, comps, list(comps))
    assert res.ok is False and res.items_g == {}
    for m in MACROS:
        assert res.miss[m] == -p["targets"][m], res.miss


def test_binding_macro_on_no_usable_library_reports_short():
    """Consumer re-check: binding_macro over the fallback miss dict must say
    direction 'short' with a negative signed miss."""
    comps = {"meat": _comp("meat", {"kcal": 145.0, "protein": 25.0,
                                    "fat": 5.0, "carb": 0.0})}
    comps["meat"]["tags"] = ["soy"]
    p = _person(REF_TARGETS, exclude=["soy"])
    bm = engine.binding_macro(p, comps)
    assert bm is not None
    assert bm["direction"] == "short" and bm["signed_miss_g"] < 0


# =========================================================================== #
#  M1.0 (2) — ineligible/unknown pins dropped WITH a structured warning
# =========================================================================== #
def test_pin_on_ineligible_component_warns_and_drops():
    comps = {
        "meat": _comp("meat", {"kcal": 145.0, "protein": 25.0, "fat": 5.0,
                               "carb": 0.0}),
        "soyloaf": _comp("soyloaf", {"kcal": 145.0, "protein": 25.0,
                                     "fat": 5.0, "carb": 0.0}),
    }
    comps["soyloaf"]["tags"] = ["soy"]
    p = _person({"protein": 60, "fat": 15, "carb": 0}, tol=0.3,
                exclude=["soy"])
    res = plate(p, comps, list(comps), locked={"soyloaf": 200})
    assert "soyloaf" not in res.items_g            # dropped, never served
    assert any(w["code"] == "pin_ineligible" and w["component"] == "soyloaf"
               and w["pinned_g"] == 200.0 for w in res.warnings), res.warnings


def test_pin_on_unknown_component_warns_and_drops():
    comps = {"meat": _comp("meat", {"kcal": 145.0, "protein": 25.0,
                                    "fat": 5.0, "carb": 0.0})}
    p = _person({"protein": 60, "fat": 15, "carb": 0}, tol=0.3)
    res = plate(p, comps, list(comps), locked={"ghost_dish": 100})
    assert "ghost_dish" not in res.items_g
    assert any(w["code"] == "pin_unknown" and w["component"] == "ghost_dish"
               for w in res.warnings), res.warnings


# =========================================================================== #
#  M1.0 (3) — tolerance validation: type + range (0 < t <= 0.5)
# =========================================================================== #
def _ppl_doc(tolerance):
    return {"schema_version": 1,
            "people": {"p1": {"targets": {"protein": 100, "fat": 60,
                                          "carb": 200},
                              "tolerance": tolerance}},
            "settings": {"days": 7, "active_min_budget": 180,
                         "cook_days": [0], "max_days_same_component": 4}}


@pytest.mark.parametrize("bad", [-0.5, 0, 0.51, 1, "5%", True, [0.05]])
def test_bad_tolerance_is_structured_error(bad):
    issues = validate_people_doc(_ppl_doc(bad))
    assert any(i.code == "bad_tolerance" and i.severity == "error"
               for i in issues), (bad, [str(i) for i in issues])


@pytest.mark.parametrize("good", [0.01, 0.05, 0.2, 0.5])
def test_valid_tolerance_is_accepted(good):
    issues = validate_people_doc(_ppl_doc(good))
    assert not [i for i in issues if i.severity == "error"], good


# =========================================================================== #
#  M1.0 (4) — doctor runs on demand only (doctor command / --diagnose)
# =========================================================================== #
def _tiny_lib(tmp_path):
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
    # 945-kcal person: serve scale clamps to 0.6 (M1.7) -> 300g effective
    # maxes; these targets stay comfortably feasible inside that band
    ppl_doc = {"schema_version": 1,
               "people": {"p1": {"targets": {"protein": 80, "fat": 45,
                                             "carb": 55},
                                 "tolerance": 0.2}},
               "settings": {"days": 7, "active_min_budget": 600,
                            "max_days_same_component": 7, "cook_days": [0]}}
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(ing_doc))
    (lib / "components.yaml").write_text(yaml.safe_dump(comp_doc))
    (lib / "people.yaml").write_text(yaml.safe_dump(ppl_doc))
    return lib


def test_week_skips_doctor_by_default(tmp_path, capsys):
    lib = _tiny_lib(tmp_path)
    out = tmp_path / "plan.md"
    cli.main(["week", "--library", str(lib), "--menu", "stew,rice",
              "--out", str(out)])
    capsys.readouterr()
    counts = engine.solve_counts()
    assert counts, "pipeline ran no solves"
    assert not any(stage.startswith("doctor") for stage in counts), counts
    text = out.read_text()
    assert "## Binding macro" not in text
    assert "## Feasibility" not in text


def test_week_diagnose_flag_runs_doctor_and_includes_report(tmp_path, capsys):
    lib = _tiny_lib(tmp_path)
    out = tmp_path / "plan.md"
    cli.main(["week", "--library", str(lib), "--menu", "stew,rice",
              "--diagnose", "--out", str(out)])
    capsys.readouterr()
    counts = engine.solve_counts()
    assert any(stage.startswith("doctor") for stage in counts), counts
    text = out.read_text()
    assert "## Binding macro" in text and "## Feasibility" in text


def test_doctor_command_still_runs_doctor(tmp_path, capsys):
    lib = _tiny_lib(tmp_path)
    cli.main(["doctor", "--library", str(lib)])
    out = capsys.readouterr().out
    assert "## Feasibility" in out and "## Binding macro" in out
    assert any(stage.startswith("doctor")
               for stage in engine.solve_counts())


def test_shop_skips_doctor_by_default(tmp_path, capsys):
    lib = _tiny_lib(tmp_path)
    cli.main(["shop", "--library", str(lib), "--menu", "stew,rice",
              "--out", str(tmp_path / "plan.md")])
    capsys.readouterr()
    assert not any(stage.startswith("doctor")
                   for stage in engine.solve_counts())


# =========================================================================== #
#  M1.0 (5) — P1 residue: founder-derived numbers out of engine comments
# =========================================================================== #
def test_no_founder_derived_numbers_in_package_source():
    """The engine's rationale comments must be generic (P1/P9): no
    founder-household numbers quoted as design justification."""
    pkg = Path(engine.__file__).resolve().parent
    residue = ("588", "4700", "4,700", "2,121", "2121", "4.7 lb",
               "jimbo", "devon")
    hits = []
    for py in sorted(pkg.glob("*.py")):
        text = py.read_text()
        for tok in residue:
            if tok in text:
                hits.append((py.name, tok))
    assert not hits, hits


# =========================================================================== #
#  M1.0 (6) — the dead-config gate cannot be satisfied by prose
# =========================================================================== #
def test_strip_helper_removes_comments_and_docstrings():
    src = (
        '"""module docstring mentions ghost_field_a"""\n'
        "def f(x):\n"
        '    """docstring mentions ghost_field_b"""\n'
        "    # comment mentions ghost_field_c\n"
        '    return x["real_field"]\n')
    out = strip_comments_and_docstrings(src)
    for ghost in ("ghost_field_a", "ghost_field_b", "ghost_field_c"):
        assert ghost not in out
    assert "'real_field'" in out or '"real_field"' in out


# =========================================================================== #
#  M1.7 — serve_g scaling: REVOKED (owner, 2026-08-09 — the lard-beans
#  incident, PRD Appendix B item 2). Authored serve_g bounds are per-dish
#  palatability ABSOLUTES. The mechanism stays dormant (identity defaults);
#  these tests pin the default AND keep the dormant machinery honest under
#  an explicit monkeypatch.
# =========================================================================== #
def _enable_scaling(monkeypatch, lo=0.6, hi=1.8):
    """Wake the dormant mechanism for a single test."""
    monkeypatch.setitem(engine.SCALING, "scale_min", lo)
    monkeypatch.setitem(engine.SCALING, "scale_max", hi)


def test_scaling_default_is_identity_revoked():
    """The shipped default is identity: authored bounds are absolute for
    EVERY eater, regardless of kcal (owner revocation after the real-plan
    test that emitted 720g of refried beans)."""
    assert SCALING["scale_min"] == 1.0 and SCALING["scale_max"] == 1.0
    for t in (REF_TARGETS,
              {k: 2 * v for k, v in REF_TARGETS.items()},
              {"protein": 75, "fat": 50, "carb": 125}):
        assert person_scale(_person(t)) == 1.0


def test_beans_regression_authored_bounds_absolute():
    """THE regression: a 4500-kcal eater still may not exceed a dish's
    authored serve_g max. Reaching protein through one main is impossible
    past the cap — the plate caps at 400g and reports the shortfall loudly
    (negative miss), instead of legalizing a bucket of one dish."""
    comps = {"meat": _comp("meat", {"kcal": 145.0, "protein": 25.0,
                                    "fat": 5.0, "carb": 0.0},
                           serve=(100, 400))}
    big = _person({"protein": 150, "fat": 300, "carb": 300})     # 4500 kcal
    res = plate(big, comps, list(comps))
    assert res.items_g.get("meat", 0) <= 400, res.items_g
    assert res.ok is False and res.miss["protein"] < 0           # SHORT, loud


def test_effective_bounds_identity_by_default():
    c = _comp("c", {"kcal": 100.0, "protein": 10.0, "fat": 2.0,
                    "carb": 10.0}, serve=(100, 400))
    for t in (REF_TARGETS, {"protein": 150, "fat": 100, "carb": 375}):
        lo, hi, w = effective_serve_bounds(c, _person(t))
        assert (lo, hi) == (100, 400) and w is None


def test_scaling_mechanism_person_scale(monkeypatch):
    _enable_scaling(monkeypatch)
    assert person_scale(_person(REF_TARGETS)) == 1.0
    double = {k: 2 * v for k, v in REF_TARGETS.items()}
    assert person_scale(_person(double)) == SCALING["scale_max"]
    half = {"protein": 75, "fat": 50, "carb": 125}
    assert kcal_of(half) == 1250
    assert person_scale(_person(half)) == SCALING["scale_min"]
    mid = {"protein": 150, "fat": 100, "carb": 375}
    assert kcal_of(mid) == 3000
    assert person_scale(_person(mid)) == pytest.approx(1.2)


def test_scaling_mechanism_bounds_proportional(monkeypatch):
    _enable_scaling(monkeypatch)
    c = _comp("c", {"kcal": 100.0, "protein": 10.0, "fat": 2.0,
                    "carb": 10.0}, serve=(100, 400))
    mid = _person({"protein": 150, "fat": 100, "carb": 375})     # scale 1.2
    lo, hi, w = effective_serve_bounds(c, mid)
    assert (lo, hi) == (pytest.approx(120), pytest.approx(480))
    assert w is None
    lo0, hi0, w0 = effective_serve_bounds(c, _person(REF_TARGETS))
    assert (lo0, hi0) == (100, 400) and w0 is None


def test_scaling_mechanism_unit_realigned_to_grid(monkeypatch):
    _enable_scaling(monkeypatch)
    c = _comp("c", {"kcal": 100.0, "protein": 10.0, "fat": 2.0,
                    "carb": 10.0}, serve=(80, 400), unit=40)
    mid = _person({"protein": 150, "fat": 100, "carb": 375})     # scale 1.2
    lo, hi, w = effective_serve_bounds(c, mid)
    assert (lo, hi) == (120, 480)
    assert lo % 40 == 0 and hi % 40 == 0 and w is None
    assert lo >= 80 * 1.2 and hi <= 400 * 1.2


def test_scaling_mechanism_inversion_warning(monkeypatch):
    _enable_scaling(monkeypatch)
    c = _comp("c", {"kcal": 100.0, "protein": 10.0, "fat": 2.0,
                    "carb": 10.0}, serve=(100, 100), unit=100)
    low = _person({"protein": 75, "fat": 50, "carb": 125})       # scale 0.6
    lo, hi, w = effective_serve_bounds(c, low)
    assert (lo, hi) == (100, 100)
    assert w is not None and w["code"] == "serve_scale_unaligned"
    assert w["component"] == "c" and w["unit_g"] == 100


def test_scaling_mechanism_plate_exceeds_raw_max(monkeypatch):
    _enable_scaling(monkeypatch)
    comps = {"meat": _comp("meat", {"kcal": 145.0, "protein": 25.0,
                                    "fat": 5.0, "carb": 0.0},
                           serve=(100, 400))}
    big = _person({"protein": 150, "fat": 300, "carb": 300})     # 4500 kcal
    assert person_scale(big) == SCALING["scale_max"]
    res = plate(big, comps, list(comps))
    assert res.items_g.get("meat", 0) > 400, res.items_g
    assert 570 <= res.items_g["meat"] <= 630, res.items_g


def test_scaling_mechanism_plate_low_capped_short_negative(monkeypatch):
    _enable_scaling(monkeypatch)
    comps = {"meat": _comp("meat", {"kcal": 145.0, "protein": 25.0,
                                    "fat": 5.0, "carb": 0.0},
                           serve=(100, 400))}
    small = _person({"protein": 150, "fat": 10, "carb": 30})     # 810 kcal
    assert person_scale(small) == SCALING["scale_min"]
    res = plate(small, comps, list(comps))
    assert res.items_g.get("meat") == 240                        # 400 * 0.6
    assert res.ok is False and res.miss["protein"] < 0           # SHORT


def test_plate_unit_alignment_preserved():
    comps = {
        "balls": _comp("balls", {"kcal": 180.0, "protein": 15.0, "fat": 9.0,
                                 "carb": 9.0}, serve=(80, 400), unit=40),
        "rice": _comp("rice", {"kcal": 112.5, "protein": 2.0, "fat": 0.5,
                               "carb": 25.0}, role="starch", serve=(50, 500)),
    }
    mid = _person({"protein": 60, "fat": 36, "carb": 379}, tol=0.2)
    res = plate(mid, comps, list(comps))
    g = res.items_g.get("balls", 0)
    if g:
        lo, hi, _ = effective_serve_bounds(comps["balls"], mid)
        assert lo <= g <= hi and g % 40 == 0, (g, lo, hi)


def test_plate_attaches_inversion_warning(monkeypatch):
    _enable_scaling(monkeypatch)
    comps = {
        "brick": _comp("brick", {"kcal": 180.0, "protein": 15.0, "fat": 9.0,
                                 "carb": 9.0}, serve=(100, 100), unit=100),
        "rice": _comp("rice", {"kcal": 112.5, "protein": 2.0, "fat": 0.5,
                               "carb": 25.0}, role="starch", serve=(50, 500)),
    }
    low = _person({"protein": 75, "fat": 50, "carb": 125}, tol=0.3)  # 0.6
    res = plate(low, comps, list(comps))
    assert any(w["code"] == "serve_scale_unaligned"
               and w["component"] == "brick" for w in res.warnings), \
        res.warnings


def test_carb_headroom_identity_by_default_scaled_when_enabled(monkeypatch):
    comps = {"rice": _comp("rice", {"kcal": 120.0, "protein": 2.0,
                                    "fat": 0.5, "carb": 27.0},
                           role="starch", serve=(100, 400), keeps=7)}
    ref = _person(REF_TARGETS)
    mid = _person({"protein": 150, "fat": 100, "carb": 375})
    ch_ref = engine.carb_headroom(ref, comps, SET)
    ch_mid = engine.carb_headroom(mid, comps, SET)
    assert ch_ref["worst_headroom_g"] == pytest.approx(400 * 0.27)
    assert ch_mid["worst_headroom_g"] == pytest.approx(400 * 0.27)  # identity
    _enable_scaling(monkeypatch)
    ch_mid2 = engine.carb_headroom(mid, comps, SET)
    assert ch_mid2["worst_headroom_g"] == pytest.approx(480 * 0.27)


def test_score_menu_carb_check_uses_scaled_max(monkeypatch):
    """score_menu's cheap worst-day carb check consumes the (possibly
    scaled) serve max through the same effective-bounds path — verified by
    waking the mechanism with an aggressive scale."""
    comps = {
        "meat": _comp("meat", {"kcal": 145.0, "protein": 25.0, "fat": 5.0,
                               "carb": 0.0}, keeps=7),
        "rice": _comp("rice", {"kcal": 120.0, "protein": 2.0, "fat": 0.5,
                               "carb": 27.0}, role="starch", serve=(100, 400),
                      keeps=7),
    }
    people = {"p1": _person(REF_TARGETS, tol=0.2)}   # carb 250 target
    # identity: headroom 400*0.27 = 108 < 250 -> shortfall penalty
    s_short, _ = engine.score_menu(comps, ING, sorted(comps), SET, people)
    monkeypatch.setitem(engine.SCALING, "reference_kcal", 1000)
    monkeypatch.setitem(engine.SCALING, "scale_max", 2.5)
    # scale = 2500/1000 = 2.5 -> headroom 1000*0.27 = 270 >= 250 -> clear
    s_clear, _ = engine.score_menu(comps, ING, sorted(comps), SET, people)
    assert s_short - s_clear == \
        engine.SCORE_WEIGHTS["carb_headroom_shortfall"], (s_short, s_clear)


def test_volume_floor_identity_default_responds_when_enabled(monkeypatch):
    """Under identity defaults the 4500-kcal person cannot reach 150g
    protein past the 400g authored cap: floor is None (loudly infeasible).
    Waking the mechanism (cap 720) makes the floor real — the floor runs
    through the same effective-bounds path."""
    comps = {
        "meat": _comp("meat", {"kcal": 145.0, "protein": 25.0, "fat": 5.0,
                               "carb": 0.0}, serve=(100, 400)),
        "oil": _comp("oil", {"kcal": 900.0, "protein": 0.0, "fat": 100.0,
                             "carb": 0.0}, role="accent", serve=(5, 500)),
        "rice": _comp("rice", {"kcal": 108.0, "protein": 2.0, "fat": 0.0,
                               "carb": 25.0}, role="starch",
                      serve=(50, 2000)),
    }
    big = _person({"protein": 150, "fat": 300, "carb": 300}, tol=0.4)
    vf_id = engine.volume_floor(big, comps)
    assert vf_id["floor_g"] is not None, vf_id      # identity: band reachable
    _enable_scaling(monkeypatch, lo=0.6, hi=0.6)    # shrink caps: 400 -> 240
    vf_low = engine.volume_floor(big, comps)
    assert vf_low["floor_g"] is None, vf_low        # protein band unreachable


# =========================================================================== #
#  M1.8 — pantry aging (stock 'acquired' consumed at last)
# =========================================================================== #
AGING_ING = {
    "chicken": dict(p=22.5, f=2.6, c=0.0, perishable=True, keeps_days=3,
                    pack_g=907, cost=9.0, tags=[]),
    "white_rice_dry": dict(p=7.1, f=0.7, c=80.0, perishable=False,
                           keeps_days=999, pack_g=907, cost=3.5, tags=[]),
    "fish": dict(p=20.0, f=5.0, c=0.0, perishable=True, keeps_days=2,
                 pack_g=500, cost=8.0, tags=[], freezable=True),
}
AGING_COMPS = {
    "chx": {"ingredients": {"chicken": 450}},
    "rice": {"ingredients": {"white_rice_dry": 500}},
}


def _stock(ingredient, grams, acquired):
    return {"ingredient": ingredient, "grams": grams, "acquired": acquired}


def test_aged_out_stock_not_deducted_and_warned():
    """Chicken keeps 3 raw days; stock acquired 8 days before plan start has
    -5 remaining days: it cannot survive to ANY cook session — it must NOT
    be deducted, and the warning must name it as expiring unused."""
    pantry = {"stock": [_stock("chicken", 500, datetime.date(2026, 8, 1))]}
    eff, warnings = age_pantry(pantry, AGING_ING, SET, PLAN_DATE)
    assert eff["stock"] == []
    assert len(warnings) == 1
    w = warnings[0]
    assert w["code"] == "stock_expiring_unused"
    assert w["ingredient"] == "chicken" and w["grams"] == 500
    assert w["age_days"] == 8 and w["remaining_days"] == -5
    # purchasing with the effective pantry buys the full need
    rows, _, _ = costing.purchase(AGING_COMPS, AGING_ING, ["chx"],
                                  {"chx": 2}, pantry=eff)
    by = {r[0]: r for r in rows}
    assert by["chicken"][1] == 900                  # nothing deducted


def test_partially_aged_stock_deducts_only_what_survives():
    """Two chicken entries: acquired 2 days ago (1 remaining day — survives
    to the day-0 session) and 4 days ago (already dead). Only the surviving
    entry's grams deduct."""
    pantry = {"stock": [
        _stock("chicken", 300, datetime.date(2026, 8, 7)),   # age 2 -> keeps
        _stock("chicken", 400, datetime.date(2026, 8, 5)),   # age 4 -> dead
    ]}
    eff, warnings = age_pantry(pantry, AGING_ING, SET, PLAN_DATE)
    assert [r["grams"] for r in eff["stock"]] == [300]
    assert [w["grams"] for w in warnings] == [400]
    rows, _, _ = costing.purchase(AGING_COMPS, AGING_ING, ["chx"],
                                  {"chx": 2}, pantry=eff)
    by = {r[0]: r for r in rows}
    assert by["chicken"][1] == 900 - 300


def test_fresh_and_nonperishable_stock_survive():
    pantry = {"stock": [
        _stock("chicken", 200, datetime.date(2026, 8, 9)),        # age 0
        _stock("white_rice_dry", 800, datetime.date(2025, 1, 1)),  # shelf
    ]}
    eff, warnings = age_pantry(pantry, AGING_ING, SET, PLAN_DATE)
    assert warnings == []
    assert len(eff["stock"]) == 2


def test_freezable_stock_past_raw_life_survives():
    """Freezable mirrors the raw-freshness escape hatch: fish past raw
    keeps_days went into the freezer on arrival — still deductible."""
    pantry = {"stock": [_stock("fish", 250, datetime.date(2026, 8, 1))]}
    eff, warnings = age_pantry(pantry, AGING_ING, SET, PLAN_DATE)
    assert warnings == []
    assert [r["grams"] for r in eff["stock"]] == [250]


def test_stock_acquired_after_plan_date_is_an_error():
    pantry = {"stock": [_stock("chicken", 100, datetime.date(2026, 8, 10))]}
    with pytest.raises(ValueError) as ei:
        age_pantry(pantry, AGING_ING, SET, PLAN_DATE)
    msg = str(ei.value)
    assert "chicken" in msg and "2026-08-10" in msg and "2026-08-09" in msg


def test_iso_string_dates_accepted_in_aging():
    pantry = {"stock": [_stock("chicken", 200, "2026-08-08")]}
    eff, warnings = age_pantry(pantry, AGING_ING, SET, PLAN_DATE)
    assert warnings == [] and len(eff["stock"]) == 1


# =========================================================================== #
#  M1.8 — cooked leftovers join availability and are eaten FIRST
# =========================================================================== #
LEFT_COMPS = {
    "stew": dict(id="stew", name="stew", cuisine="test", role="main",
                 yield_g=500, serve_g={"min": 100, "max": 400}, keeps_days=3,
                 active_min=30, ingredients={"stuff": 500},
                 per100={"kcal": 145.0, "protein": 25.0, "fat": 5.0,
                         "carb": 0.0}, tags=[]),
}


def test_cooked_leftover_windows_follow_the_strict_convention():
    """Available on day d iff 0 <= (plan_date + d) - cooked < keeps_days:
    cooked yesterday with keeps 3 covers plan days 0 and 1 only."""
    pantry = {"cooked": [{"component": "stew", "grams": 600,
                          "cooked": datetime.date(2026, 8, 8)}]}
    entries, warnings = cooked_leftovers(pantry, LEFT_COMPS, SET, PLAN_DATE)
    assert warnings == []
    assert len(entries) == 1
    assert entries[0]["component"] == "stew"
    assert entries[0]["grams"] == 600
    assert entries[0]["days"] == [0, 1]


def test_leftover_past_its_life_is_ignored_with_warning():
    pantry = {"cooked": [{"component": "stew", "grams": 600,
                          "cooked": datetime.date(2026, 8, 5)}]}   # age 4 >= 3
    entries, warnings = cooked_leftovers(pantry, LEFT_COMPS, SET, PLAN_DATE)
    assert entries == []
    assert len(warnings) == 1
    w = warnings[0]
    assert w["code"] == "leftover_expired" and w["component"] == "stew"


def test_leftovers_feed_early_days_and_reduce_session_batches():
    """600g of demanded stew on days 0-1; a 600g leftover covers it all:
    session batches drop from 2 to none — leftovers are already paid for,
    eat them first (economy rule, extended)."""
    weeks = {"p1": [{"stew": 300}, {"stew": 300}, {}, {}, {}, {}, {}]}
    base = session_plan(LEFT_COMPS, ING, SET, weeks)
    assert base["batches"] == {"stew": 2}

    entries, _ = cooked_leftovers(
        {"cooked": [{"component": "stew", "grams": 600,
                     "cooked": datetime.date(2026, 8, 8)}]},
        LEFT_COMPS, SET, PLAN_DATE)
    sp = session_plan(LEFT_COMPS, ING, SET, weeks, leftovers=entries)
    assert sp["batches"] == {}
    assert sp["unattributed"] == []
    served = {(r["component"], r["day"]): r["grams"] for r in sp["leftover"]}
    assert served == {("stew", 0): 300, ("stew", 1): 300}


def test_partial_leftover_remainder_goes_to_sessions():
    weeks = {"p1": [{"stew": 300}, {"stew": 300}, {}, {}, {}, {}, {}]}
    entries, _ = cooked_leftovers(
        {"cooked": [{"component": "stew", "grams": 400,
                     "cooked": datetime.date(2026, 8, 8)}]},
        LEFT_COMPS, SET, PLAN_DATE)
    sp = session_plan(LEFT_COMPS, ING, SET, weeks, leftovers=entries)
    # day 0 fully from leftover (300), day 1 gets the remaining 100 + a
    # 200g session remainder -> 1 batch instead of 2
    assert sp["batches"] == {"stew": 1}
    served = {(r["component"], r["day"]): r["grams"] for r in sp["leftover"]}
    assert served == {("stew", 0): 300, ("stew", 1): 100}
    assert sp["sessions"][0]["demand_g"] == {"stew": 200}


def test_leftover_outside_its_window_is_not_consumed():
    """Demand on a day the leftover no longer covers must come from a
    session, not the leftover."""
    weeks = {"p1": [{}, {}, {"stew": 300}, {}, {}, {}, {}]}
    entries, _ = cooked_leftovers(
        {"cooked": [{"component": "stew", "grams": 600,
                     "cooked": datetime.date(2026, 8, 8)}]},   # covers 0-1
        LEFT_COMPS, SET, PLAN_DATE)
    sp = session_plan(LEFT_COMPS, ING, SET, weeks, leftovers=entries)
    assert sp["leftover"] == []
    assert sp["batches"] == {"stew": 1}


def test_build_week_availability_joined_by_leftovers():
    """cook_days [4] only: without the leftover, days 0-3 have no stew (an
    empty-day hole for a stew-only library); the leftover feeds day 0."""
    st = model.Settings.from_raw(
        dict(days=7, active_min_budget=600, batch_time_factor=0.5,
             max_days_same_component=7, cook_days=[4], shop_days=[4]),
        {"mode": "off"})
    people = {"p1": _person({"protein": 60, "fat": 12, "carb": 0}, tol=0.3)}
    comps = {k: dict(v) for k, v in LEFT_COMPS.items()}
    assert not engine.available_on(comps["stew"], 0, st)

    weeks0, _ = engine.build_week(comps, people, st, list(comps), seed=0)
    assert weeks0["p1"][0] == {}                    # hole without leftovers

    entries = [dict(component="stew", grams=5000, days=[0, 1],
                    cooked="2026-08-08")]
    weeks1, _ = engine.build_week(comps, people, st, list(comps), seed=0,
                                  leftovers=entries)
    assert weeks1["p1"][0], "leftover must make day 0 servable"
    assert "stew" in weeks1["p1"][0]


def test_leftover_determinism_fixed_dates():
    weeks = {"p1": [{"stew": 300}, {"stew": 300}, {}, {}, {}, {}, {}]}
    entries, _ = cooked_leftovers(
        {"cooked": [{"component": "stew", "grams": 400,
                     "cooked": "2026-08-08"}]}, LEFT_COMPS, SET, PLAN_DATE)
    a = session_plan(LEFT_COMPS, ING, SET, weeks, leftovers=entries)
    b = session_plan(LEFT_COMPS, ING, SET, weeks, leftovers=entries)
    assert a == b


# =========================================================================== #
#  M1.8 — CLI: --date required iff the pantry is non-empty
# =========================================================================== #
def _lib_with_pantry(tmp_path, pantry_doc):
    lib = _tiny_lib(tmp_path)
    p = tmp_path / "pantry.yaml"
    p.write_text(yaml.safe_dump(pantry_doc, sort_keys=False))
    return lib, p


def test_cli_date_required_when_pantry_nonempty(tmp_path):
    lib, pan = _lib_with_pantry(tmp_path, {
        "schema_version": 1,
        "stock": [{"ingredient": "stuff", "grams": 100,
                   "acquired": "2026-08-01"}]})
    with pytest.raises(SystemExit) as ei:
        cli.main(["shop", "--library", str(lib), "--menu", "stew,rice",
                  "--pantry", str(pan), "--out", str(tmp_path / "p.md")])
    msg = str(ei.value)
    assert "--date" in msg
    assert "plan start date" in msg          # names WHY


def test_cli_date_optional_when_pantry_empty(tmp_path, capsys):
    lib, pan = _lib_with_pantry(tmp_path, {"schema_version": 1,
                                           "stock": [], "cooked": []})
    cli.main(["shop", "--library", str(lib), "--menu", "stew,rice",
              "--pantry", str(pan), "--out", str(tmp_path / "p.md")])
    # shop prints the section BODY (after the '## Shopping list' header)
    assert "| ingredient | need | buy |" in capsys.readouterr().out


def test_cli_bad_date_is_a_clean_error(tmp_path):
    lib, pan = _lib_with_pantry(tmp_path, {
        "schema_version": 1,
        "stock": [{"ingredient": "stuff", "grams": 100,
                   "acquired": "2026-08-01"}]})
    with pytest.raises(SystemExit) as ei:
        cli.main(["shop", "--library", str(lib), "--menu", "stew,rice",
                  "--pantry", str(pan), "--date", "08/09/2026",
                  "--out", str(tmp_path / "p.md")])
    assert "YYYY-MM-DD" in str(ei.value)


def test_cli_stock_acquired_in_future_is_a_clean_error(tmp_path):
    lib, pan = _lib_with_pantry(tmp_path, {
        "schema_version": 1,
        "stock": [{"ingredient": "stuff", "grams": 100,
                   "acquired": "2026-08-15"}]})
    with pytest.raises(SystemExit) as ei:
        cli.main(["shop", "--library", str(lib), "--menu", "stew,rice",
                  "--pantry", str(pan), "--date", "2026-08-09",
                  "--out", str(tmp_path / "p.md")])
    assert "2026-08-15" in str(ei.value)


def test_cli_week_with_dated_pantry_runs_and_warns(tmp_path, capsys):
    """End to end: expired chicken-style stock warns on stderr; the fresh
    leftover shows up as a LEFTOVER note in the cook plan."""
    lib, pan = _lib_with_pantry(tmp_path, {
        "schema_version": 1,
        "stock": [{"ingredient": "stuff", "grams": 500,
                   "acquired": "2026-08-01"}],
        "cooked": [{"component": "stew", "grams": 400,
                    "cooked": "2026-08-08"}]})
    out = tmp_path / "plan.md"
    cli.main(["week", "--library", str(lib), "--menu", "stew,rice",
              "--pantry", str(pan), "--date", "2026-08-09",
              "--out", str(out)])
    err = capsys.readouterr().err
    text = out.read_text()
    # 'stuff' is non-perishable in the tiny lib -> no stock warning; the
    # leftover is within life (cooked 8/8, keeps 7) -> LEFTOVER note appears
    assert "LEFTOVER" in text, text[:2000]
    assert "stew" in text


def test_dead_config_gate_covers_cooked_as_live():
    """M1.8 makes Pantry.cooked LIVE: it must no longer be reserved, and
    costing must reference it."""
    assert "cooked" not in model.RESERVED_FIELDS
    import inspect
    src = strip_comments_and_docstrings(inspect.getsource(costing))
    assert "'cooked'" in src or '"cooked"' in src
