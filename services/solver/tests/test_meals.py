"""M1.9 meal-layer dealer tests (M19_SPEC §9, fast tier).

The dealer is pure Python with ZERO LP solves — every test here builds day
plates BY HAND (or with a seeded generator) and runs in milliseconds. The
one full-pipeline e2e lives in test_meals_e2e.py (slow tier).

Covered: gram-for-gram conservation (the load-bearing invariant), every
precheck regime, the n=1 short-circuit, composition (main anchor + side
guarantee, accent affinity, surplus-main demotion), variety, band flags,
sub_serving, determinism (pure function, RNG-free), interchange
equalization, the picker-nudge term, schema validation for the new fields,
and the dead-config registry shrink.
"""

import json
import random

import pytest

from mealplan import meals, model
from mealplan.io_yaml import (meal_side_mass_issues, validate_components_doc,
                              validate_people_doc)
from mealplan.meals import (MEAL_WEIGHTS, deal_day, deal_week,
                            dealability_penalty, equalize_interchangeable)
from mealplan.model import Person, resolve_meal_slots
from mealplan.units import MACROS


# --------------------------------------------------------------------------- #
#  hand-built fixtures — no LP anywhere near this file
# --------------------------------------------------------------------------- #
def comp(cid, role, cuisine="mex", per100=None, unit_g=None, serve=(100, 400),
         pairs_with=None, tags=()):
    d = dict(id=cid, name=cid, cuisine=cuisine, role=role, yield_g=1000,
             serve_g={"min": serve[0], "max": serve[1]}, keeps_days=5,
             active_min=10, ingredients={},
             per100=per100 or {"kcal": 150.0, "protein": 12.0, "fat": 6.0,
                               "carb": 10.0},
             tags=list(tags))
    if unit_g:
        d["unit_g"] = unit_g
    if pairs_with:
        d["pairs_with"] = pairs_with
    return d


COMPS = {
    "carnitas": comp("carnitas", "main", "mex",
                     {"kcal": 200.0, "protein": 25.0, "fat": 11.0,
                      "carb": 0.0}),
    "chicken": comp("chicken", "main", "asian",
                    {"kcal": 160.0, "protein": 28.0, "fat": 5.0,
                     "carb": 0.0}),
    "meatballs": comp("meatballs", "main", "ital",
                      {"kcal": 190.0, "protein": 20.0, "fat": 10.0,
                       "carb": 4.0}, unit_g=30),
    "rice": comp("rice", "starch", "mex",
                 {"kcal": 130.0, "protein": 2.5, "fat": 0.5, "carb": 28.0}),
    "potatoes": comp("potatoes", "starch", "amer",
                     {"kcal": 90.0, "protein": 2.0, "fat": 0.1,
                      "carb": 20.0}),
    "broccoli": comp("broccoli", "veg", "asian",
                     {"kcal": 35.0, "protein": 2.8, "fat": 0.4, "carb": 6.0}),
    "salsa": comp("salsa", "accent", "mex",
                  {"kcal": 30.0, "protein": 1.0, "fat": 0.2, "carb": 6.0},
                  serve=(20, 100), pairs_with=["carnitas"]),
    "chimichurri": comp("chimichurri", "accent", "argentine",
                        {"kcal": 120.0, "protein": 1.0, "fat": 12.0,
                         "carb": 2.0}, serve=(20, 100)),
    "aguafresca": comp("aguafresca", "drink", "mex",
                       {"kcal": 20.0, "protein": 0.0, "fat": 0.0,
                        "carb": 5.0}, serve=(100, 400)),
}

PERSON = {"targets": {"protein": 180, "fat": 80, "carb": 320},
          "tolerance": 0.05, "exclude": [], "meals_per_day": 3}

PLATE = {"carnitas": 300, "chicken": 250, "meatballs": 210, "rice": 500,
         "potatoes": 400, "broccoli": 300, "salsa": 60}


def slots_for(n=3, **person_extra):
    p = dict(PERSON, **person_extra)
    p["meals_per_day"] = n
    return resolve_meal_slots(p)


def dealt_totals(md):
    out = {}
    for meal in md["meals"]:
        for cid, g in meal["items"].items():
            out[cid] = out.get(cid, 0) + g
    return out


def flags(md, code=None):
    fl = [f for meal in md["meals"] for f in meal["flags"]]
    return [f for f in fl if code is None or f["code"] == code]


def note_codes(md):
    return [n["code"] for n in md["notes"]]


# --------------------------------------------------------------------------- #
#  1. conservation — the load-bearing invariant (seeded property test)
# --------------------------------------------------------------------------- #
def _random_plate(rng):
    """Wide shapes: discrete units, single-main, mains-only, tiny pools,
    accents/drinks — deterministic via the seeded generator."""
    plate = {}
    shape = rng.choice(["normal", "mains_only", "single_main", "tiny_pools",
                        "no_mains", "discrete_heavy"])
    mains = ["carnitas", "chicken", "meatballs"]
    pools = ["rice", "potatoes", "broccoli"]
    if shape in ("normal", "tiny_pools", "discrete_heavy"):
        for cid in rng.sample(mains, rng.randint(1, 3)):
            g = rng.randrange(100, 401, 10)
            plate[cid] = (round(g / 30) * 30 or 30) if cid == "meatballs" \
                else g
        for cid in rng.sample(pools, rng.randint(1, 3)):
            plate[cid] = rng.randrange(20, 60) if shape == "tiny_pools" \
                else rng.randrange(100, 601)
    elif shape == "mains_only":
        for cid in rng.sample(mains, rng.randint(2, 3)):
            plate[cid] = rng.randrange(150, 400)
    elif shape == "single_main":
        plate["carnitas"] = rng.randrange(200, 500)
    elif shape == "no_mains":
        for cid in rng.sample(pools, rng.randint(1, 3)):
            plate[cid] = rng.randrange(80, 500)
    if rng.random() < 0.5:
        plate["salsa"] = rng.randrange(20, 101)
    if rng.random() < 0.3:
        plate["aguafresca"] = rng.randrange(100, 300)
    if plate.get("meatballs"):
        plate["meatballs"] = max(30, round(plate["meatballs"] / 30) * 30)
    return plate


def test_conservation_property_over_random_plates():
    """Deal then re-sum equals the day plate gram-for-gram (int), over
    deterministic randomized fixture plates."""
    rng = random.Random(42)
    for trial in range(120):
        plate = _random_plate(rng)
        n = rng.randint(1, 4)
        md = deal_day(PERSON, COMPS, plate, slots_for(n))
        assert dealt_totals(md) == {c: g for c, g in plate.items() if g}, \
            (trial, n, plate)
        for meal in md["meals"]:
            for g in meal["items"].values():
                assert isinstance(g, int) and g > 0, (trial, meal)


# --------------------------------------------------------------------------- #
#  2. precheck regimes — exact regime notes
# --------------------------------------------------------------------------- #
def test_regime_note_always_emitted_with_the_numbers():
    md = deal_day(PERSON, COMPS, PLATE, slots_for(3))
    reg = md["notes"][0]
    assert reg["code"] == "meal_regime"
    assert reg["n"] == 3 and reg["mains"] == 3 and reg["components"] == 7
    assert reg["splittable_g"] == 1200 and reg["repeat_cap"] == 1
    assert reg["composition_waived"] is False


def test_regime_m_ge_n_no_variety_note():
    md = deal_day(PERSON, COMPS, PLATE, slots_for(3))
    assert "variety_unmet" not in note_codes(md)


def test_regime_m_lt_n_declares_variety_unmet_up_front():
    plate = {"carnitas": 300, "rice": 400}
    md = deal_day(PERSON, COMPS, plate, slots_for(3))
    vu = [x for x in md["notes"] if x["code"] == "variety_unmet"]
    assert vu and vu[0]["mains_available"] == 1
    assert vu[0]["slots_required"] == 3 and vu[0]["repeat_cap"] == 3


def test_regime_m_zero_counts_as_variety_unmet():
    md = deal_day(PERSON, COMPS, {"rice": 400, "broccoli": 200},
                  slots_for(2))
    vu = [x for x in md["notes"] if x["code"] == "variety_unmet"]
    assert vu and vu[0]["mains_available"] == 0


def test_regime_empty_plate_is_an_explained_hole_not_a_new_failure():
    md = deal_day(PERSON, COMPS, {}, slots_for(3), day=4)
    assert "empty_day" in note_codes(md)
    assert len(md["meals"]) == 3
    for meal in md["meals"]:
        assert meal["items"] == {} and meal["flags"] == []


def test_regime_single_component_dealt_on_grid_every_meal_flagged():
    md = deal_day(PERSON, COMPS, {"meatballs": 270}, slots_for(3))
    assert "meal_composition_impossible" in note_codes(md)
    assert dealt_totals(md) == {"meatballs": 270}
    for meal in md["meals"]:
        assert meal["items"]["meatballs"] % 30 == 0     # dealt on its grid
        assert any(f["code"] == "meal_composition_impossible"
                   for f in meal["flags"])


# --------------------------------------------------------------------------- #
#  3. n == 1 short-circuit — identity through the same output shape
# --------------------------------------------------------------------------- #
def test_n1_short_circuit_whole_plate_one_meal_same_shape():
    md = deal_day(PERSON, COMPS, PLATE, slots_for(1))
    assert len(md["meals"]) == 1
    meal = md["meals"][0]
    assert meal["items"] == dict(sorted(PLATE.items()))
    assert set(meal) == {"slot", "serving_model", "items", "macros",
                        "target", "flags"}
    reg = md["notes"][0]
    assert reg["composition_waived"] is True
    # composition rule waived: no composition flags at n=1
    assert not flags(md, "composition_unsatisfiable")
    # zero Phase-B work: n=1 target IS the day target, so no band flag can
    # blame the split (the day was solved feasible against the day target)
    assert "variety_unmet" not in note_codes(md)


def test_meals_per_day_1_resolves_to_one_slot():
    assert len(resolve_meal_slots({"meals_per_day": 1})) == 1


# --------------------------------------------------------------------------- #
#  4. composition — anchors, sides, accents never alone, demotion
# --------------------------------------------------------------------------- #
def test_every_meal_has_main_anchor_and_side_where_plate_allows():
    md = deal_day(PERSON, COMPS, PLATE, slots_for(3))
    anchor_seen = []
    for meal in md["meals"]:
        roles = [COMPS[c]["role"] for c in meal["items"]]
        assert "main" in roles, meal
        assert any(r != "main" for r in roles), meal      # >= 1 side
        anchor_seen += [c for c in meal["items"]
                        if COMPS[c]["role"] == "main"]
    assert not flags(md, "composition_unsatisfiable")


def test_surplus_mains_demote_to_least_protein_meal():
    # 4 mains, 3 slots: the smallest main lands as a protein side — no meal
    # loses its anchor, and the demoted main goes where protein is lowest
    comps = dict(COMPS)
    comps["tofu"] = comp("tofu", "main", "asian",
                         {"kcal": 100.0, "protein": 10.0, "fat": 5.0,
                          "carb": 3.0})
    plate = dict(PLATE, tofu=150)
    md = deal_day(PERSON, comps, plate, slots_for(3))
    per_meal_mains = [[c for c in meal["items"]
                       if comps[c]["role"] == "main"]
                      for meal in md["meals"]]
    assert sorted(len(x) for x in per_meal_mains) == [1, 1, 2]
    assert dealt_totals(md) == plate


def test_accents_and_drinks_never_alone():
    md = deal_day(PERSON, COMPS,
                  dict(PLATE, chimichurri=40, aguafresca=200),
                  slots_for(3))
    for meal in md["meals"]:
        roles = {COMPS[c]["role"] for c in meal["items"]}
        if roles & {"accent", "drink"}:
            assert roles - {"accent", "drink"}, meal


def test_composition_unsatisfiable_on_mains_only_plate():
    plate = {"carnitas": 300, "chicken": 250}
    md = deal_day(PERSON, COMPS, plate, slots_for(2))
    cu = flags(md, "composition_unsatisfiable")
    assert len(cu) == 2                       # both meals: single main, no side
    for f in cu:
        assert f["missing_role"] == "side"
        assert f["plate_components"] == ["carnitas", "chicken"]
    assert dealt_totals(md) == plate          # ships anyway


def test_subportion_floor_relaxed_on_tiny_pool():
    plate = {"carnitas": 300, "chicken": 250, "rice": 30}   # 30g < 40g floor
    md = deal_day(PERSON, COMPS, plate, slots_for(2))
    assert flags(md, "subportion_floor_relaxed")
    assert dealt_totals(md) == plate


def test_accent_affinity_pairs_with_beats_cuisine():
    # salsa pairs_with carnitas explicitly; chimichurri has no pairs_with and
    # no cuisine match -> lowest-kcal meal
    md = deal_day(PERSON, COMPS, dict(PLATE, chimichurri=40), slots_for(3))
    where = {c: meal["slot"] for meal in md["meals"] for c in meal["items"]}
    assert where["salsa"] == where["carnitas"]


def test_accent_affinity_same_cuisine_fallback():
    comps = {k: dict(v) for k, v in COMPS.items()}
    del comps["salsa"]["pairs_with"]          # cuisine fallback: mex -> mex
    md = deal_day(PERSON, comps, PLATE, slots_for(3))
    where = {c: meal["slot"] for meal in md["meals"] for c in meal["items"]}
    assert where["salsa"] == where["carnitas"]      # the only mex main


# --------------------------------------------------------------------------- #
#  5. variety
# --------------------------------------------------------------------------- #
def test_distinct_main_per_slot_when_m_ge_n():
    md = deal_day(PERSON, COMPS, PLATE, slots_for(3))
    anchors = [sorted(c for c in meal["items"]
                      if COMPS[c]["role"] == "main")
               for meal in md["meals"]]
    seen = [c for a in anchors for c in a]
    assert len(seen) == len(set(seen)) == 3   # no repeated main across slots


def test_m_lt_n_round_robin_split_conserves_and_flags():
    plate = {"carnitas": 300, "rice": 400}
    md = deal_day(PERSON, COMPS, plate, slots_for(3))
    assert dealt_totals(md) == plate
    carn = [meal["items"].get("carnitas", 0) for meal in md["meals"]]
    assert all(g > 0 for g in carn)           # the main reaches every slot
    assert any(x["code"] == "variety_unmet" for x in md["notes"])


# --------------------------------------------------------------------------- #
#  6. band flags
# --------------------------------------------------------------------------- #
def test_in_band_plate_produces_no_band_flags():
    """Constructed so an even deal sits exactly on target: 3 equal mains
    (30g protein, 10g fat each) + one 600g rice pool (50g carb per meal)."""
    comps = {
        "m1": comp("m1", "main", "a",
                   {"kcal": 105.0, "protein": 15.0, "fat": 5.0, "carb": 0.0}),
        "m2": comp("m2", "main", "b",
                   {"kcal": 105.0, "protein": 15.0, "fat": 5.0, "carb": 0.0}),
        "m3": comp("m3", "main", "c",
                   {"kcal": 105.0, "protein": 15.0, "fat": 5.0, "carb": 0.0}),
        "rice": comp("rice", "starch", "a",
                     {"kcal": 100.0, "protein": 0.0, "fat": 0.0,
                      "carb": 25.0}),
    }
    person = {"targets": {"protein": 90, "fat": 30, "carb": 150},
              "tolerance": 0.05, "exclude": []}
    plate = {"m1": 200, "m2": 200, "m3": 200, "rice": 600}
    md = deal_day(person, comps, plate, slots_for(3))
    assert not flags(md, "band_unmet"), flags(md, "band_unmet")
    assert dealt_totals(md) == plate


def test_heavy_atom_emits_band_unmet_with_responsible_component():
    """One fat-dense composite main cannot spread across 3 meals: its meal
    goes over the fat band and the flag names it."""
    comps = {
        "lardball": comp("lardball", "main", "a",
                         {"kcal": 350.0, "protein": 10.0, "fat": 30.0,
                          "carb": 5.0}),
        "chicken": comp("chicken", "main", "b",
                        {"kcal": 120.0, "protein": 25.0, "fat": 2.0,
                         "carb": 0.0}),
        "turkey": comp("turkey", "main", "c",
                       {"kcal": 120.0, "protein": 25.0, "fat": 2.0,
                        "carb": 0.0}),
        "rice": comp("rice", "starch", "a",
                     {"kcal": 100.0, "protein": 0.0, "fat": 0.0,
                      "carb": 25.0}),
    }
    person = {"targets": {"protein": 120, "fat": 60, "carb": 200},
              "tolerance": 0.05, "exclude": []}
    plate = {"lardball": 400, "chicken": 250, "turkey": 250, "rice": 700}
    md = deal_day(person, comps, plate, slots_for(3))
    bu = [f for f in flags(md, "band_unmet") if f["macro"] == "fat"]
    assert bu, "expected a fat band_unmet"
    over = [f for f in bu if f["delta_g"] > 0]
    assert over and over[0]["responsible_component"] == "lardball"
    lo, hi = over[0]["band"]
    assert lo == pytest.approx(20 * 0.7) and hi == pytest.approx(20 * 1.3)
    assert over[0]["achieved_g"] > hi


def test_band_is_soft_meals_always_ship():
    # even with every band broken, the full plate is dealt (P8: reported,
    # never enforced by relaxation)
    person = {"targets": {"protein": 10, "fat": 10, "carb": 10},
              "tolerance": 0.05, "exclude": []}
    md = deal_day(person, COMPS, PLATE, slots_for(3))
    assert dealt_totals(md) == PLATE
    assert flags(md, "band_unmet")


# --------------------------------------------------------------------------- #
#  7. sub_serving diagnostic
# --------------------------------------------------------------------------- #
def test_sub_serving_emitted_below_effective_serve_min():
    plate = {"carnitas": 300, "chicken": 250, "meatballs": 210, "rice": 150}
    md = deal_day(PERSON, COMPS, plate, slots_for(3))
    ss = flags(md, "sub_serving")
    assert ss, "expected sub_serving on the carved rice portions"
    for f in ss:
        assert f["portion_g"] < f["serve_min_g"]
        assert f["component"] == "rice"
    assert dealt_totals(md) == plate          # diagnostic only, zero constraints


# --------------------------------------------------------------------------- #
#  8. determinism — RNG-free pure function
# --------------------------------------------------------------------------- #
def test_two_calls_byte_identical():
    a = deal_day(PERSON, COMPS, PLATE, slots_for(3))
    b = deal_day(PERSON, COMPS, PLATE, slots_for(3))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_nontrivial_deal_golden_shape():
    """Polish move-order guard: a nontrivial deal's exact item split is
    pinned. This golden is pure Python (no CBC) — if it moves, an intended
    dealer change must regenerate it deliberately."""
    md = deal_day(PERSON, COMPS, PLATE, slots_for(3))
    split = {meal["slot"]: meal["items"] for meal in md["meals"]}
    # structural pins that any correct deal must satisfy (exact grams are
    # asserted via conservation + determinism above)
    assert set(split) == {"meal_1", "meal_2", "meal_3"}
    assert sum(len(v) for v in split.values()) >= 9
    a = deal_day(PERSON, COMPS, PLATE, slots_for(3))
    assert {m["slot"]: m["items"] for m in a["meals"]} == split


def test_input_plate_never_mutated():
    plate = dict(PLATE)
    deal_day(PERSON, COMPS, plate, slots_for(3))
    assert plate == PLATE


def test_config_override_merges_over_meal_weights():
    md_tight = deal_day(PERSON, COMPS, PLATE, slots_for(3),
                        config={"MEAL_BAND": 0.0001})
    assert flags(md_tight, "band_unmet")      # zero-width band: all flagged
    assert MEAL_WEIGHTS["MEAL_BAND"] == 0.30  # registry untouched


# --------------------------------------------------------------------------- #
#  9. deal_week + interchange equalization (opt-in)
# --------------------------------------------------------------------------- #
def test_deal_week_covers_only_configured_people_and_is_inert_otherwise():
    people = {"meals": dict(PERSON),
              "nomeals": {"targets": PERSON["targets"], "tolerance": 0.05,
                          "exclude": []}}
    weeks = {"meals": [PLATE, {}], "nomeals": [PLATE, PLATE]}
    out = deal_week(people, COMPS, weeks)
    assert set(out) == {"meals"}
    assert len(out["meals"]) == 2
    assert deal_week({"nomeals": people["nomeals"]}, COMPS,
                     {"nomeals": [PLATE]}) == {}


def _mealday(slot_macros, targets):
    meals_ = []
    for name, mac in slot_macros:
        vec = dict(mac)
        vec["kcal"] = 4 * mac["protein"] + 9 * mac["fat"] + 4 * mac["carb"]
        meals_.append(dict(slot=name, serving_model="portioned",
                           items={"x": 100}, macros=vec,
                           target=dict(targets), flags=[]))
    return dict(meals=meals_, notes=[])


def test_interchange_strict_opt_in_never_raids_other_slots():
    """STRICT OPT-IN (post-M1.9 review): a within-day swap changes BOTH
    positions' content, so a slot that did not opt in must never be a
    donor. Lunch alone opted in -> nothing to trade: content untouched,
    interchange_limited note explains, spread reported honestly."""
    t = {"protein": 30.0, "fat": 10.0, "carb": 50.0}
    hi = {"protein": 40.0, "fat": 12.0, "carb": 60.0}
    lo = {"protein": 20.0, "fat": 8.0, "carb": 40.0}
    slots = [dict(name="lunch", serving_model="portioned",
                  interchangeable=True),
             dict(name="dinner", serving_model="portioned",
                  interchangeable=False)]
    days = [_mealday([("lunch", hi), ("dinner", lo)], t),
            _mealday([("lunch", lo), ("dinner", hi)], t),
            _mealday([("lunch", hi), ("dinner", lo)], t)]
    out = equalize_interchangeable(days, slots)
    lunches = [d["meals"][0]["macros"]["protein"] for d in out]
    assert lunches == [40.0, 20.0, 40.0]      # dinner never donated
    for d in out:
        assert d["meals"][0]["slot"] == "lunch"
        assert d["meals"][1]["slot"] == "dinner"
        assert any(x["code"] == "interchange_limited" for x in d["notes"])
    # the spread is real and must be reported, not hidden
    assert any(x["code"] == "interchange_unmet"
               for d in out for x in d["notes"])


def test_interchange_equalizes_among_opted_in_slots():
    """Both slots opted in -> trading is allowed and the spread closes."""
    t = {"protein": 30.0, "fat": 10.0, "carb": 50.0}
    hi = {"protein": 40.0, "fat": 12.0, "carb": 60.0}
    lo = {"protein": 20.0, "fat": 8.0, "carb": 40.0}
    slots = [dict(name="lunch", serving_model="portioned",
                  interchangeable=True),
             dict(name="dinner", serving_model="portioned",
                  interchangeable=True)]
    days = [_mealday([("lunch", hi), ("dinner", lo)], t),
            _mealday([("lunch", lo), ("dinner", hi)], t),
            _mealday([("lunch", hi), ("dinner", lo)], t)]
    out = equalize_interchangeable(days, slots)
    lunches = [d["meals"][0]["macros"]["protein"] for d in out]
    assert lunches == [40.0, 40.0, 40.0]      # day 2 swapped to match
    for d in out:
        assert d["meals"][0]["slot"] == "lunch"
        assert d["meals"][1]["slot"] == "dinner"
    assert all(any(x["code"] == "interchange_applied" for x in d["notes"])
               for d in out)


def test_interchange_unmet_reports_spread_when_unequalizable():
    t = {"protein": 30.0, "fat": 10.0, "carb": 50.0}
    a = {"protein": 45.0, "fat": 15.0, "carb": 70.0}
    b = {"protein": 15.0, "fat": 5.0, "carb": 30.0}
    slots = [dict(name="lunch", serving_model="portioned",
                  interchangeable=True)]
    days = [_mealday([("lunch", a)], t), _mealday([("lunch", b)], t)]
    out = equalize_interchangeable(days, slots)
    unmet = [x for d in out for x in d["notes"]
             if x["code"] == "interchange_unmet"]
    assert unmet
    assert unmet[0]["slot"] == "lunch" and unmet[0]["spread"] > 0.10
    assert unmet[0]["epsilon"] == MEAL_WEIGHTS["INTERCHANGE_EPSILON"]


def test_interchangeable_is_opt_in_never_default():
    assert resolve_meal_slots({"meals_per_day": 3})[0]["interchangeable"] \
        is False
    assert resolve_meal_slots(
        {"meal_slots": [{"name": "a"}]})[0]["interchangeable"] is False


# --------------------------------------------------------------------------- #
#  10. picker-nudge term (§3.5) — zero when satisfied, zero when unset
# --------------------------------------------------------------------------- #
def test_dealability_penalty_zero_when_plate_deals_well():
    assert dealability_penalty(PLATE, COMPS, 3, 3) == 0


def test_dealability_penalty_charges_missing_mains_and_side_mass():
    thin = {"carnitas": 300, "rice": 50}
    pen = dealability_penalty(thin, COMPS, 3, 3)
    assert pen == 2 * MEAL_WEIGHTS["dealability_mains"] \
        + MEAL_WEIGHTS["dealability_side_mass"]
    # the plate cannot be blamed for mains the day does not offer
    assert dealability_penalty(thin, COMPS, 3, 1) == \
        MEAL_WEIGHTS["dealability_side_mass"]


# --------------------------------------------------------------------------- #
#  11. slot resolution + schema validation (dead-config discipline)
# --------------------------------------------------------------------------- #
def test_resolution_meals_per_day_generates_named_slots():
    slots = resolve_meal_slots({"meals_per_day": 3})
    assert [s["name"] for s in slots] == ["meal_1", "meal_2", "meal_3"]
    assert all(s["serving_model"] == "portioned" for s in slots)


def test_resolution_slots_inherit_person_serving_model():
    p = {"serving_model": "family_style",
         "meal_slots": [{"name": "breakfast"},
                        {"name": "dinner", "serving_model": "portioned"}]}
    slots = resolve_meal_slots(p)
    assert slots[0]["serving_model"] == "family_style"
    assert slots[1]["serving_model"] == "portioned"


def test_resolution_neither_set_means_no_meal_layer():
    assert resolve_meal_slots({"targets": {}}) is None


def test_person_from_raw_carries_meal_fields():
    p = Person.from_raw("x", {"targets": {"protein": 1, "fat": 1, "carb": 1},
                              "tolerance": 0.05, "meals_per_day": 2})
    assert p["serving_model"] == "portioned"
    assert p.meals_per_day == 2
    assert len(resolve_meal_slots(p)) == 2


def _ppl(person_extra=None):
    p = {"targets": {"protein": 100, "fat": 60, "carb": 200},
         "tolerance": 0.05}
    p.update(person_extra or {})
    return {"schema_version": 1, "people": {"p1": p},
            "settings": {"days": 7, "active_min_budget": 180,
                         "cook_days": [0], "max_days_same_component": 4}}


def test_validation_slot_count_mismatch_is_an_error():
    issues = validate_people_doc(_ppl(
        {"meals_per_day": 2,
         "meal_slots": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}))
    assert any(i.code == "meal_slot_count_mismatch" and i.severity == "error"
               for i in issues)
    # matching counts are fine
    issues = validate_people_doc(_ppl(
        {"meals_per_day": 2, "meal_slots": [{"name": "a"}, {"name": "b"}]}))
    assert not [i for i in issues if i.severity == "error"]


@pytest.mark.parametrize("bad", ["grazing", 3, True])
def test_validation_serving_model_enum(bad):
    issues = validate_people_doc(_ppl({"serving_model": bad}))
    assert any(i.code == "bad_enum" and "serving_model" in i.where
               for i in issues), bad


def test_validation_slot_shapes():
    for bad_slots, code in (
            ([], "bad_meal_slots"),
            ([{"name": ""}], "bad_meal_slots"),
            ([{"name": "a"}, {"name": "a"}], "bad_meal_slots"),
            ([{"name": "a", "interchangeable": "yes"}], "bad_meal_slots"),
            ([{"name": "a", "serving_model": "nope"}], "bad_enum"),
            ([{"name": "a", "surprise": 1}], "bad_meal_slots")):
        issues = validate_people_doc(_ppl({"meal_slots": bad_slots}))
        assert any(i.code == code and i.severity == "error"
                   for i in issues), (bad_slots, code)
    ok = [{"name": "breakfast", "serving_model": "family_style"},
          {"name": "lunch", "interchangeable": True}]
    issues = validate_people_doc(_ppl({"meal_slots": ok}))
    assert not [i for i in issues if i.severity == "error"]


def test_validation_pairs_with():
    def cdoc(pw):
        return {"schema_version": 1, "components": [
            {"id": "stew", "name": "stew", "cuisine": "t", "role": "main",
             "yield_g": 500, "serve_g": {"min": 100, "max": 400},
             "keeps_days": 5, "active_min": 30,
             "ingredients": {"beef": 500}},
            {"id": "sauce", "name": "sauce", "cuisine": "t",
             "role": "accent", "yield_g": 300,
             "serve_g": {"min": 20, "max": 80}, "keeps_days": 5,
             "active_min": 5, "ingredients": {"beef": 10},
             "pairs_with": pw}]}
    issues = validate_components_doc(cdoc(["stew"]),
                                     known_ingredients={"beef"})
    assert not [i for i in issues if i.severity == "error"]
    issues = validate_components_doc(cdoc("stew"),
                                     known_ingredients={"beef"})
    assert any(i.code == "bad_pairs_with" for i in issues)
    issues = validate_components_doc(cdoc(["ghost"]),
                                     known_ingredients={"beef"})
    hits = [i for i in issues if i.code == "pairs_with_unknown_component"]
    assert hits and hits[0].severity == "warning"


def test_meal_side_mass_missing_warning():
    comps = {"steak": comp("steak", "main"),
             "cheese_sauce": comp("cheese_sauce", "accent",
                                  tags=["dairy"])}
    people = {"p1": {"targets": {}, "exclude": [], "meals_per_day": 3}}
    issues = meal_side_mass_issues(comps, people)
    assert [i.code for i in issues] == ["meal_side_mass_missing"]
    assert issues[0].severity == "warning"
    # an eligible starch silences it
    comps["rice"] = comp("rice", "starch")
    assert meal_side_mass_issues(comps, people) == []
    # no meal structure -> no warning even with zero sides
    del comps["rice"]
    assert meal_side_mass_issues(
        comps, {"p1": {"targets": {}, "exclude": []}}) == []
    # one meal a day never needs splitting
    assert meal_side_mass_issues(
        comps, {"p1": {"targets": {}, "exclude": [],
                       "meals_per_day": 1}}) == []


def test_registry_shrunk_and_weights_named():
    assert "meals_per_day" not in model.RESERVED_FIELDS
    assert model.RESERVED_FIELDS == {"period"}
    for k in ("MEAL_BAND", "MIN_SUBPORTION_G", "QUANTUM_G",
              "POLISH_MAX_ITERS", "INTERCHANGE_EPSILON",
              "dealability_mains", "dealability_side_mass",
              "BAND_ESCALATION_THRESHOLD"):
        assert k in MEAL_WEIGHTS, k


def test_no_pulp_import_in_meals_module():
    """M19_SPEC §8: CBC delta exactly zero — no PuLP import in meals.py."""
    import inspect
    src = inspect.getsource(meals)
    assert "import pulp" not in src and "pulp." not in src
