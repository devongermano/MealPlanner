"""M1.13 dish layer — SLOW tier (CBC; M113_SPEC §14 pipeline tests).

Dish fixtures appear ONLY where the test targets the dish layer (M19 §9
rule reapplied). One end-to-end examples run (module-scoped, reused),
one lock → verify-plan round trip, and small synthetic person-day solves
for the band-conflict / big-eater / replate scenarios. The §13 pre-M1.6
escalation instrumentation (skeleton retry counts + dish_band_binding
rates) is recorded off the examples run and asserted to EXIST — the
values themselves are provisional evidence, never pinned.
"""

import copy
from pathlib import Path

import pytest

from mealplan import cli, dishes as D, engine, io_yaml
from mealplan.model import resolve_meal_slots

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"

# a fixed dish menu (no search — spec: budget the solves tightly)
MENU = ["breakfast_burrito", "bulgogi_rice_bowl", "carnitas_tacos",
        "salsa_verde_chicken_bowl", "sausage_fusilli_al_forno",
        "shrimp_al_pastor_tacos"]


# --------------------------------------------------------------------------- #
#  module-scoped examples solve — every examples assertion reuses it
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def solved():
    ing, comps, people, settings = io_yaml.load(EXAMPLES)
    dishes = io_yaml.load_dishes(EXAMPLES / "dishes.yaml", comps=comps,
                                 people=people)
    engine.reset_solve_counts()
    diag = {}
    weeks, demand, mealdays = D.build_week_dishes(
        comps, people, settings, dishes, MENU, seed=1, ing=ing, diag=diag)
    return dict(ing=ing, comps=comps, people=people, settings=settings,
                dishes=dishes, weeks=weeks, demand=demand,
                mealdays=mealdays, diag=diag,
                solves=engine.solve_counts())


def test_every_meal_is_one_dish_within_bands(solved):
    """The owner's bar, structurally: every meal's non-side items are
    exactly its assigned dish's members (plate_dishes hard-asserts the
    band ∩ cap membership internally; here we assert the emitted shape —
    dish named, items ⊆ members ∪ nominated sides, servings scalar in
    [DISH_T_MIN, t_max])."""
    comps, dishes = solved["comps"], solved["dishes"]
    for pname, mds in solved["mealdays"].items():
        person = solved["people"][pname]
        for md in mds:
            for meal in md["meals"]:
                if meal["dish"] is None:
                    continue
                allowed = set()
                for j in meal["dishes"]:
                    allowed |= set(dishes[j]["components"])
                # strict mode: sides come only from authored lists
                for j in meal["dishes"]:
                    allowed |= set(dishes[j]["compatible_sides"])
                assert set(meal["items"]) <= allowed, (pname, meal)
                for j, t in meal["servings"].items():
                    tm = max(D.t_max(dishes[j], comps, person),
                             D.DISH_WEIGHTS["DISH_T_MIN"])
                    # servings is a 2-dp DISPLAY value (round-half-up):
                    # a t solved exactly at its cap may read half a
                    # quantum (0.005) past it. The LP enforced the true
                    # bound; plate_dishes' structural asserts checked the
                    # exact grams.
                    q = 0.005
                    assert D.DISH_WEIGHTS["DISH_T_MIN"] - q <= t \
                        <= tm + q + 1e-9, (pname, j, t, tm)


def test_day_plate_is_sum_of_meals_and_demand_of_plates(solved):
    for pname, wk in solved["weeks"].items():
        mds = solved["mealdays"][pname]
        for plate, md in zip(wk, mds):
            dealt = {}
            for meal in md["meals"]:
                for cid, g in meal["items"].items():
                    dealt[cid] = dealt.get(cid, 0) + g
            assert dealt == plate
    total = {}
    for wk in solved["weeks"].values():
        for plate in wk:
            for cid, g in plate.items():
                total[cid] = total.get(cid, 0) + g
    assert total == solved["demand"]


def test_downstream_blindness_session_plan_runs_on_components(solved):
    """demand/batching never learn what a dish is: session_plan consumes
    the derived component demand exactly as in heritage mode."""
    from mealplan.costing import session_plan
    sp = session_plan(solved["comps"], solved["ing"], solved["settings"],
                      solved["weeks"])
    assert sp["batches"] and all(cid in solved["comps"]
                                 for cid in sp["batches"])
    assert sp["unattributed"] == []


def test_instrumentation_recorded_for_escalation_gate(solved):
    """§13 pre-M1.6 evidence: retry counts + flag rates + solve counts are
    RECORDED (values provisional, never pinned). Budget: ≤ 2 CBC solves ×
    (retries + 1) per person-day, spec §12."""
    diag = solved["diag"]
    days = solved["settings"]["days"]
    people_n = len(solved["people"])
    assert set(diag["dish_retries"]) == set(solved["people"])
    assert all(len(v) == days for v in diag["dish_retries"].values())
    assert "dish_flag_counts" in diag
    # dish_flag_days is §13's actual measure (fraction of person-days):
    # bounded by the person-day count and by the occurrence count per code
    person_days = days * people_n
    for code, nd in diag["dish_flag_days"].items():
        assert 1 <= nd <= person_days, (code, nd)
        assert nd <= diag["dish_flag_counts"][code], (code, nd)
    total_attempts = sum(r + 1 for v in diag["dish_retries"].values()
                         for r in v)
    assert solved["solves"].get("plate-dish", 0) <= 2 * total_attempts
    # and the whole week stayed inside the §12 bound vs heritage's 80/day
    assert solved["solves"]["plate-dish"] <= 4 * days * people_n


def test_meals_carry_dish_and_sheets_say_it(solved):
    from mealplan import artifacts
    pname = sorted(solved["people"])[0]
    person = solved["people"][pname]
    sheet = artifacts.render_eat_sheet(
        pname, person, solved["comps"], solved["weeks"][pname],
        solved["settings"], D.closure(solved["dishes"], MENU),
        ing=solved["ing"], meta={}, meal_days=solved["mealdays"][pname])
    named = [md for md in solved["mealdays"][pname]
             for meal in md["meals"] if meal.get("dish_name")]
    assert named, "no dish-named meals solved"
    a_dish = next(meal["dish_name"] for md in solved["mealdays"][pname]
                  for meal in md["meals"] if meal.get("dish_name"))
    assert a_dish in sheet
    assert "portioned within its ratio bands" in sheet


# --------------------------------------------------------------------------- #
#  lock → verify-plan round trip (dish mode; snapshot carries dishes.yaml)
# --------------------------------------------------------------------------- #
def test_lock_verify_roundtrip_dish_mode(tmp_path, capsys):
    plans = tmp_path / "plans"
    cli.main(["lock", "--library", str(EXAMPLES), "--seed", "1",
              "--date", "2026-08-10", "--menu", ",".join(MENU),
              "--plans", str(plans)])
    capsys.readouterr()
    plan_path = plans / "2026-08-10" / "plan.yaml"
    assert plan_path.exists()
    import yaml
    doc = yaml.safe_load(plan_path.read_text())
    assert doc["inputs"]["library"].get("dishes"), \
        "dish-mode snapshot must embed dishes.yaml (inputs hash covers it)"
    assert doc["menu"] == MENU
    assert doc.get("meals"), "dish-mode meals are plan content"
    a_meal = doc["meals"][sorted(doc["meals"])[0]][0]["meals"][0]
    assert "dish" in a_meal
    cli.main(["verify-plan", str(plan_path)])
    out = capsys.readouterr().out
    assert "verified" in out


# --------------------------------------------------------------------------- #
#  synthetic person-day fixtures: band conflict, big eater, replate
# --------------------------------------------------------------------------- #
def _comp(cid, role="main", serve=(80, 400), unit=None, keeps=7,
          per100=None, tags=()):
    d = dict(id=cid, name=cid, cuisine="test", role=role, yield_g=1000,
             serve_g={"min": serve[0], "max": serve[1]}, keeps_days=keeps,
             active_min=20, ingredients={"x": 100},
             per100=per100 or {"kcal": 150.0, "protein": 15.0, "fat": 5.0,
                               "carb": 10.0}, tags=list(tags))
    if unit:
        d["unit_g"] = unit
    return d


FCOMPS = {
    "meat": _comp("meat", serve=(80, 240),
                  per100={"kcal": 150.0, "protein": 28.0, "fat": 4.0,
                          "carb": 0.0}),
    "wrap": _comp("wrap", role="starch", serve=(60, 240), unit=60,
                  per100={"kcal": 300.0, "protein": 8.0, "fat": 8.0,
                          "carb": 48.0}),
    "rice": _comp("rice", role="starch", serve=(100, 500),
                  per100={"kcal": 160.0, "protein": 3.0, "fat": 1.0,
                          "carb": 35.0}),
    "slaw": _comp("slaw", role="veg", serve=(50, 300),
                  per100={"kcal": 30.0, "protein": 1.5, "fat": 0.5,
                          "carb": 5.0}),
}

FDISHES = {
    "wrap_plate": dict(
        id="wrap_plate", name="Wrap plate", source="t",
        reconstruction="from_source",
        components={"wrap": dict(base_g=120, min_g=60, max_g=240),
                    "meat": dict(base_g=140, min_g=80, max_g=280)},
        accents=[], compatible_sides=["slaw"],
        meal_affinity=[], cuisine=None, notes=None),
    "meat_bowl": dict(
        id="meat_bowl", name="Meat bowl", source="t",
        reconstruction="inferred",
        components={"rice": dict(base_g=200, min_g=100, max_g=400),
                    "meat": dict(base_g=150, min_g=80, max_g=240)},
        accents=[], compatible_sides=["slaw"],
        meal_affinity=[], cuisine=None, notes=None),
}

FSETTINGS = dict(days=7, active_min_budget=600, batch_time_factor=0.5,
                 max_days_same_component=4, cook_days=[0, 4], shop_days=[0],
                 min_lean_anchors=1, max_batches_per_component=3,
                 use_freezer=True, dish_layer="strict",
                 budget={"mode": "off"})


def _solve_day(person, dishes=None, menu=None, locked=None, veto=None,
               slots=None):
    dishes = dishes or FDISHES
    menu = menu or sorted(dishes)
    slots = slots if slots is not None else resolve_meal_slots(person)
    asn, notes = D.skeleton_day(person, dishes, FCOMPS, FSETTINGS, 0,
                                slots, menu, veto=veto)
    res = D.plate_dishes(person, FCOMPS, dishes, asn, FSETTINGS,
                         locked=locked)
    res["assignments"] = asn
    res["notes"] = notes
    return res


def test_band_conflict_day_miss_carries_receipts():
    """Dish bands force a protein short: the day ships flagged, with
    dish_band_binding naming the maxed dish + the t_max limiter, and
    band_unmet carrying responsible_dish (§6 — nothing silently widened)."""
    hulk = dict(name="hulk",
                targets={"protein": 300, "fat": 60, "carb": 200},
                tolerance=0.05, exclude=[], dislikes=[],
                meal_slots=[{"name": "lunch"}, {"name": "dinner"}])
    res = _solve_day(hulk)
    assert res["status"] == "miss"
    assert res["miss"].get("protein", 0) < 0
    bind = [f for f in res["day_flags"] if f["code"] == "dish_band_binding"]
    assert bind, res["day_flags"]
    assert bind[0]["macro"] == "protein"
    assert bind[0]["component"] in FCOMPS
    assert "servings" in bind[0]["message"]
    unmet = [f for meal in res["meals"] for f in meal["flags"]
             if f["code"] == "band_unmet"]
    assert unmet and all(f.get("responsible_dish") for f in unmet)


def test_big_eater_scales_t_then_flags_underfilled():
    """§7 ladder: t rides up toward t_max; when every cap binds and rung 3
    is off, meal_underfilled names the binding caps and the remedies."""
    jimbo = dict(name="jimbo",
                 targets={"protein": 250, "fat": 160, "carb": 600},
                 tolerance=0.05, exclude=[], dislikes=[],
                 meal_slots=[{"name": "lunch"}, {"name": "dinner"}])
    res = _solve_day(jimbo)
    assert res["status"] == "miss"
    assert any(t > 1.0 for meal in res["meals"]
               for t in meal["servings"].values())
    under = [f for f in res["day_flags"] if f["code"] == "meal_underfilled"]
    assert under
    assert under[0]["binding_caps"]
    assert "max_dishes_per_slot" in " ".join(under[0]["options"])


def test_second_dish_optin_adds_capacity_in_band():
    """max_dishes_per_slot: 2 → the skeleton adds a second dish
    (second_dish_added) and BOTH instances carry full band integrity
    (plate_dishes hard-asserts it); the day gets closer to target."""
    jimbo = dict(name="jimbo",
                 targets={"protein": 250, "fat": 160, "carb": 600},
                 tolerance=0.05, exclude=[], dislikes=[],
                 max_dishes_per_slot=2,
                 meal_slots=[{"name": "lunch"}, {"name": "dinner"}])
    res = _solve_day(jimbo)
    added = [x for x in res["notes"] if x["code"] == "second_dish_added"]
    assert added
    two = [meal for meal in res["meals"] if len(meal["dishes"]) == 2]
    assert two and len(two[0]["servings"]) == 2
    base = _solve_day(dict(jimbo, max_dishes_per_slot=1))
    # the capacity claim: the doubled slot carries MORE food than any
    # single-dish slot could (a second real dish, never a bigger mix)
    kcal2 = two[0]["macros"]["kcal"]
    kcal1 = max(meal["macros"]["kcal"] for meal in base["meals"])
    assert kcal2 > kcal1


def test_optin_shared_member_respects_per_meal_serve_caps():
    """§7 rung 3 + shared components (M113_SPEC §5/§6 rung 1): the
    per-MEAL serve cap binds the SLOT'S summed grams. Two opted-in dishes
    sharing 'meat' must not ship 2x its per-serving absolute, and a
    component that is one dish's MEMBER never doubles as the other
    dish's side (one component, one cap)."""
    comps = {
        "meat": _comp("meat", serve=(80, 250),
                      per100={"kcal": 200.0, "protein": 28.0, "fat": 8.0,
                              "carb": 0.0}),
        "grain": _comp("grain", role="starch", serve=(80, 280),
                       per100={"kcal": 180.0, "protein": 4.0, "fat": 2.0,
                               "carb": 38.0}),
        "beans": _comp("beans", role="starch", serve=(100, 400),
                       per100={"kcal": 150.0, "protein": 9.0, "fat": 1.0,
                               "carb": 27.0}),
        "greens": _comp("greens", role="veg", serve=(50, 200),
                        per100={"kcal": 40.0, "protein": 2.0, "fat": 0.5,
                                "carb": 6.0}),
    }
    dishes = {
        "dish_a": dict(
            id="dish_a", name="Dish A", source="t",
            reconstruction="from_source",
            components={"meat": dict(base_g=125, min_g=80, max_g=250),
                        "grain": dict(base_g=140, min_g=80, max_g=280)},
            accents=[], compatible_sides=["greens"],
            meal_affinity=[], cuisine=None, notes=None),
        "dish_b": dict(
            id="dish_b", name="Dish B", source="t",
            reconstruction="from_source",
            components={"meat": dict(base_g=125, min_g=80, max_g=250),
                        "beans": dict(base_g=200, min_g=100, max_g=400)},
            accents=[], compatible_sides=["grain", "greens"],
            meal_affinity=[], cuisine=None, notes=None),
    }
    glut = dict(name="glut",
                targets={"protein": 400, "fat": 120, "carb": 500},
                tolerance=0.05, exclude=[], dislikes=[],
                max_dishes_per_slot=2,
                meal_slots=[{"name": "lunch"}])
    slots = resolve_meal_slots(glut)
    asn, notes = D.skeleton_day(glut, dishes, comps, FSETTINGS, 0, slots,
                                sorted(dishes))
    assert [x["code"] for x in notes if x["code"] == "second_dish_added"]
    assert len(asn[0]["dishes"]) == 2
    # the second dish's member 'grain' must not stay a nominated side —
    # a member+side pair is two LP variables for one component in the slot
    a_members = set()
    for j in asn[0]["dishes"]:
        a_members |= set(dishes[j]["components"])
    assert not (set(asn[0]["sides"]) & a_members), asn[0]["sides"]
    res = D.plate_dishes(glut, comps, dishes, asn, FSETTINGS)
    assert res["status"] in ("ok", "miss")
    meal = res["meals"][0]
    assert len(meal["dishes"]) == 2
    # THE cap: summed slot grams per component ≤ the authored absolute
    for cid, g in meal["items"].items():
        cap = comps[cid]["serve_g"]["max"]
        assert g <= cap + 1, (cid, g, cap)
    assert meal["items"].get("meat", 0) <= 251
    # and the miss (protein target is unreachable under the caps) still
    # explains itself with receipts, not silence
    if res["miss"].get("protein", 0) < 0:
        assert any(f["code"] in ("dish_band_binding", "meal_underfilled")
                   for f in res["day_flags"]), res["day_flags"]


def test_replate_slot_pin_and_veto():
    """§8: slot-qualified pins land on x[c,s] (the M19 restriction lifts);
    a dish veto re-runs the skeleton; vetoing everything is an explained
    no_alternative_dish."""
    p = dict(name="p", targets={"protein": 120, "fat": 60, "carb": 220},
             tolerance=0.1, exclude=[], dislikes=[],
             meal_slots=[{"name": "lunch"}, {"name": "dinner"}])
    res0 = D.replate_dishes(p, FDISHES, FCOMPS, sorted(FDISHES), 0,
                            FSETTINGS)
    by_slot = {meal["slot"]: meal for meal in res0["meals"]}
    lunch_dish = by_slot["lunch"]["dish"]
    # pin the meat in lunch to 200g — honored (within band ∩ caps)
    res1 = D.replate_dishes(p, FDISHES, FCOMPS, sorted(FDISHES), 0,
                            FSETTINGS, locked={("lunch", "meat"): 200})
    lunch1 = {meal["slot"]: meal for meal in res1["meals"]}["lunch"]
    assert lunch1["items"]["meat"] == 200
    # veto the lunch dish — the skeleton must pick the other one
    res2 = D.replate_dishes(p, FDISHES, FCOMPS, sorted(FDISHES), 0,
                            FSETTINGS, veto=[lunch_dish])
    assert all(lunch_dish not in meal["dishes"] for meal in res2["meals"])
    # veto everything — explained, never silent
    res3 = D.replate_dishes(p, FDISHES, FCOMPS, sorted(FDISHES), 0,
                            FSETTINGS, veto=sorted(FDISHES))
    assert res3["status"] == "infeasible"
    assert any(f["code"] == "no_alternative_dish"
               for f in res3["day_flags"])


def test_pin_outside_band_is_clamped_with_warning():
    p = dict(name="p", targets={"protein": 120, "fat": 60, "carb": 220},
             tolerance=0.1, exclude=[], dislikes=[],
             meal_slots=[{"name": "lunch"}, {"name": "dinner"}])
    res = D.replate_dishes(p, FDISHES, FCOMPS, sorted(FDISHES), 0,
                           FSETTINGS, locked={("lunch", "meat"): 5000})
    warns = [w for w in res["warnings"] if w["code"] == "pin_adjusted"]
    assert warns and warns[0]["adjusted_g"] <= 280
