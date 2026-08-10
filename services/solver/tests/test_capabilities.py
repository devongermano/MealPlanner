"""M0.16 — capability test suite (PRD §9).

Capability tests, not household regressions: each PRD §9 named capability runs
against a purpose-built synthetic fixture library in tests/fixtures/ (M0.15).
The founder household (examples/) is NOT a fixture and is never imported here.

Fixtures (each a complete loadable library — ingredients/components/people):
- solo_lifter            n=1, moderate targets, feasible end to end
- family_four            n=4, high/low-kcal spread, dairy+nut exclusions,
                         raw-freshness pair (blocked shrimp / freezable fish)
- conflicting_exclusions two people, disjoint tags, shared base + accents
- extreme_carb           carb is the binding macro (fat-binding sibling person
                         constructed in-test on the same library)
- all_fatty              no lean-anchor mains -> composite-dish fat-forcing
- valley                 cook_days [0,3], keeps-3 non-freezing main -> day-6 hole
- discrete_edges         unit_g components with unit-aligned tight bounds
- watery_vs_dense        two starch libraries, same carbs, different water
- dominant_main          one complete-meal main vs 3-component ensembles;
                         the main variety cap provably BINDS (uncapped greedy
                         would serve the solo main past the cap)

Runtime discipline: LP solves (CBC subprocess, ~50ms each) dominate; the
expensive solved objects (solo pipeline, family/conflict/valley weeks) are
built ONCE in module-scoped fixtures and shared across asserts.

Golden policy (PRD §9): the full-pipeline golden is byte-stable only on the
pinned reference environment — see tests/golden/README.md.
"""

import json
import random
from pathlib import Path

import pytest

from mealplan import costing, engine, io_yaml

import refenv

# The frozen golden parameter set lives in tests/_shared.py (M1.0: test
# modules never import each other — importlib-mode safe).
from _shared import GOLDEN_DISH_MENU_KW, GOLDEN_MENU_KW, GOLDEN_SEED

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = Path(__file__).resolve().parent / "golden"


def load(name):
    """Load a fixture library through the real io_yaml.load path."""
    return io_yaml.load(FIXTURES / name)


# --------------------------------------------------------------------------- #
#  shared solved objects (LP time is the budget — solve once, assert many)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def solo_pipeline():
    """Full pipeline on solo_lifter: load -> choose_menu -> build_week ->
    session_plan -> purchase. Shared by the determinism golden and the
    variety-cap capability."""
    ing, comps, people, settings = load("solo_lifter")
    menu, info, feasible, broke = engine.choose_menu(
        comps, ing, people, settings, **GOLDEN_MENU_KW)
    diag = {}
    weeks, demand = engine.build_week(comps, people, settings, menu,
                                      seed=GOLDEN_SEED, ing=ing, diag=diag)
    sp = costing.session_plan(comps, ing, settings, weeks)
    rows, waste_perishable, waste_total = costing.purchase(
        comps, ing, menu, sp["batches"])
    return dict(ing=ing, comps=comps, people=people, settings=settings,
                menu=menu, feasible=feasible, broke=broke, weeks=weeks,
                demand=demand, diag=diag, session_plan=sp, purchase=rows,
                waste_perishable=waste_perishable, waste_total=waste_total)


def dish_pipeline_run():
    """The frozen DISH-MODE pipeline (M1.13) on fixtures/solo_dishes: load
    (+dishes.yaml via the real default path) -> choose_menu_dishes ->
    build_week_dishes -> session_plan -> purchase over the dish closure.
    One function shared by the golden fixture and the regen entry point so
    they cannot drift."""
    from mealplan import dishes as dishes_mod
    ing, comps, people, settings = load("solo_dishes")
    dmap = io_yaml.load_dishes(FIXTURES / "solo_dishes" / "dishes.yaml",
                               comps=comps, people=people)
    menu, info, feasible, broke = dishes_mod.choose_menu_dishes(
        comps, ing, people, settings, dmap, **GOLDEN_DISH_MENU_KW)
    diag = {}
    weeks, demand, mealdays = dishes_mod.build_week_dishes(
        comps, people, settings, dmap, menu, seed=GOLDEN_SEED, ing=ing,
        diag=diag)
    sp = costing.session_plan(comps, ing, settings, weeks)
    rows, waste_perishable, waste_total = costing.purchase(
        comps, ing, [i for i in info["closure"] if sp["batches"].get(i)],
        sp["batches"])
    return dict(ing=ing, comps=comps, people=people, settings=settings,
                dishes=dmap, menu=menu, closure=info["closure"],
                feasible=feasible, broke=broke, weeks=weeks, demand=demand,
                mealdays=mealdays, diag=diag, session_plan=sp,
                purchase=rows, waste_perishable=waste_perishable,
                waste_total=waste_total)


@pytest.fixture(scope="module")
def solo_dish_pipeline():
    return dish_pipeline_run()


@pytest.fixture(scope="module")
def family_week():
    ing, comps, people, settings = load("family_four")
    weeks, demand = engine.build_week(comps, people, settings, list(comps),
                                      seed=0, ing=ing)
    return dict(ing=ing, comps=comps, people=people, settings=settings,
                weeks=weeks)


@pytest.fixture(scope="module")
def conflict_week():
    ing, comps, people, settings = load("conflicting_exclusions")
    weeks, demand = engine.build_week(comps, people, settings, list(comps),
                                      seed=0)
    return dict(ing=ing, comps=comps, people=people, settings=settings,
                weeks=weeks)


# --------------------------------------------------------------------------- #
#  1. composite-dish fat-forcing (all_fatty)
# --------------------------------------------------------------------------- #
def test_fat_forcing_no_lean_anchor_forces_fat_over_then_lean_flips_it():
    """CATCHES: a plate LP that reports the wrong SIGN on a forced macro (over
    vs short), or that silently under-delivers protein instead of exposing the
    fat-forcing trade of an all-fatty library.

    On all_fatty (every main p/f ~1.2-1.5, fat/protein per gram < 1), the LP
    minimizes total violation by FILLING protein — dragging fat OVER its band:
    infeasible with a POSITIVE fat miss. Adding one synthetic lean anchor to
    the same library flips the person feasible."""
    ing, comps, people, settings = load("all_fatty")
    # fixture guard against a vacuous pass: no lean anchors, all mains fatty
    mains = [c for c in comps.values() if c["role"] == "main"]
    assert mains and all(c.get("anchor") != "lean" for c in mains)
    assert all(c["per100"]["protein"] / c["per100"]["fat"] < 2.0
               for c in mains)

    res = engine.plate(people["fatima"], comps, list(comps))
    assert not res.ok
    assert res.miss.get("fat", 0) > 0, \
        f"expected POSITIVE fat miss (forced OVER), got {res.miss}"

    # same library + one lean anchor -> feasible
    comps2 = dict(comps)
    comps2["lean_fish"] = dict(
        id="lean_fish", name="lean_fish", cuisine="test", role="main",
        anchor="lean", keeps_days=5, yield_g=2000, active_min=20,
        serve_g={"min": 100, "max": 800}, tags=[],
        per100={"kcal": 109.5, "protein": 24.0, "fat": 1.5, "carb": 0.0})
    res2 = engine.plate(people["fatima"], comps2, list(comps2))
    assert res2.ok, f"lean anchor should flip feasible, miss={res2.miss}"


# --------------------------------------------------------------------------- #
#  2. binding-macro identification (extreme_carb)
# --------------------------------------------------------------------------- #
def test_binding_macro_identifies_carb_short_and_fat_short_sibling():
    """CATCHES: binding_macro naming the wrong macro or the wrong direction —
    e.g. reporting whichever macro is listed first, or dropping the sign.

    carl's 400g carb target dwarfs the library's ~130g/day carb ceiling ->
    carb binds, direction short. A sibling person on the SAME library with an
    unreachable fat target (the library has no fat-dense component) must flip
    the answer to fat."""
    ing, comps, people, settings = load("extreme_carb")
    bm = engine.binding_macro(people["carl"], comps)
    assert bm is not None
    assert bm["macro"] == "carb"
    assert bm["direction"] == "short" and bm["signed_miss_g"] < 0

    fiona = dict(name="fiona", targets={"protein": 60, "fat": 150, "carb": 80},
                 tolerance=0.05, exclude=[], dislikes=[])
    bm2 = engine.binding_macro(fiona, comps)
    assert bm2 is not None
    assert bm2["macro"] == "fat"
    assert bm2["direction"] == "short" and bm2["signed_miss_g"] < 0


# --------------------------------------------------------------------------- #
#  3. shelf-life valley -> explained hole (valley)
# --------------------------------------------------------------------------- #
def test_valley_leaves_day6_empty_and_explains_which_component_expired():
    """CATCHES: build_week silently serving an expired batch on day 6, or the
    diagnostics failing to NAME the hole (an unexplained empty day is the v1
    prototype failure mode this capability exists for).

    cook_days [0,3]; stew keeps 3 cooked days and freezes: false -> day 6
    (6-0=6 >= 3, 6-3=3 >= 3) has no protein source: the day must come out
    EMPTY and the doctor must say which day has zero lean-anchor coverage."""
    ing, comps, people, settings = load("valley")
    assert not engine.available_on(comps["stew"], 6, settings)

    weeks, _ = engine.build_week(comps, people, settings, list(comps), seed=0)
    wk = weeks["val"]
    assert wk[6] == {}, f"day 6 should be an (explained) hole, got {wk[6]}"
    assert all(wk[d] for d in range(6)), "days 0-5 must stay feasible"
    assert all("stew" not in wk[d] for d in (6,))

    # explained, not silent: lean_coverage names the uncovered day, and the
    # stew is absent from that day's availability
    lc = engine.lean_coverage(comps, settings, ing=ing)
    assert lc["uncovered_days"] == [6]
    assert lc["per_day"][6] == []
    assert "stew" in lc["lean_anchors"]
    text, data = engine.doctor(comps, people, settings, ing=ing)
    assert data["lean_coverage"]["uncovered_days"] == [6]
    assert "days with ZERO lean-anchor availability: 6" in text


def test_valley_freezes_true_bridges_day6_with_thaw_marking():
    """CATCHES: the freezer bridge not feeding the post-shelf-life day, or
    feeding it SILENTLY (without the thaw-ahead marking in the session plan).

    Same library, stew.freezes flipped true: day 6 is served from the freezer
    and the session plan carries the 'from freezer — thaw ahead' note."""
    ing, comps, people, settings = load("valley")
    comps["stew"]["freezes"] = True
    assert engine.available_on(comps["stew"], 6, settings)
    assert engine.from_freezer(comps["stew"], 6, settings)

    weeks, _ = engine.build_week(comps, people, settings, list(comps), seed=0)
    assert "stew" in weeks["val"][6], "freezer should bridge day 6"

    sp = costing.session_plan(comps, ing, settings, weeks)
    assert sp["unattributed"] == []
    notes = [f for f in sp["freezer"] if f["component"] == "stew"
             and f["day"] == 6]
    assert notes, f"day-6 stew must be marked as freezer-served: {sp['freezer']}"
    assert "from freezer — thaw ahead" in notes[0]["note"]
    # the note rides on the session that cooks the frozen batch
    k = notes[0]["session"]
    assert notes[0] in sp["sessions"][k]["freezer_notes"]


# --------------------------------------------------------------------------- #
#  4. variety caps don't starve late days (solo_lifter)
# --------------------------------------------------------------------------- #
def test_variety_caps_starch_exempt_late_days_fed_at_strict_tier(solo_pipeline):
    """CATCHES: the variety cap being applied to starches — the single starch
    (the only carb source; ~780g/day) is needed EVERY day, so capping it like
    a main starves days 4-6 out of the STRICT pool. The served week alone
    cannot show that (build_week's relaxation ladder backfills rice on days
    4-6 identically, tier 2 runs cap_days=days), so the capability assert is
    the relaxation diagnostic: every day fed at tier 0. Verified by mutation:
    capping all roles yields the same week but tiers [0,0,0,0,1,2,2].

    solo_lifter: 3 mains, cap 4 days each (12 main-days >= demand), 1 starch
    that must run 7/7 days. NOTE: soft diversity scoring alone spreads these
    3 interchangeable mains under the cap, so this fixture cannot detect a
    dropped main cap — that direction is test_variety_cap_binds_on_mains_...
    below, on dominant_main."""
    p = solo_pipeline
    wk = p["weeks"]["sol"]
    cap = p["settings"]["max_days_same_component"]
    assert all(wk[d] for d in range(7)), \
        f"no day may be empty: {[(d, pl) for d, pl in enumerate(wk)]}"
    used_days = {}
    for pl in wk:
        for cid in pl:
            used_days[cid] = used_days.get(cid, 0) + 1
    mains = [i for i in p["comps"] if p["comps"][i]["role"] == "main"]
    for m in mains:
        assert used_days.get(m, 0) <= cap, (m, used_days)
    # the starch exceeds the main cap and feeds the back half of the week...
    assert used_days["rice_pot"] == 7 > cap
    assert all("rice_pot" in wk[d] for d in (4, 5, 6))
    # ...FROM THE STRICT POOL — the exemption, not the relaxation ladder, is
    # what feeds days 4-6 (a capped starch would force tiers 1/2 here)
    assert p["diag"]["relax_tiers"]["sol"] == [0] * 7, p["diag"]


def test_variety_cap_binds_on_mains_dominant_main_held_to_cap():
    """CATCHES: the variety cap not applying to mains at all. Needs a fixture
    where soft diversity does NOT already mimic the cap: the plate-choice
    score sums used_days^2 over PLATE MEMBERS, so dominant_main's power_bowl
    (a complete meal, always a 1-component plate) keeps out-scoring the
    3-component turkey/tofu ensembles even when it is the most-used
    component. Verified by mutation: without the main cap the same seed
    serves power_bowl 4 days > cap 3; with it, exactly 3 and the strict pool
    still covers all 7 days (3 mains x cap 3 >= 7) with no relaxation."""
    ing, comps, people, settings = load("dominant_main")
    diag = {}
    weeks, _ = engine.build_week(comps, people, settings, list(comps),
                                 seed=0, ing=ing, diag=diag)
    wk = weeks["dom"]
    cap = settings["max_days_same_component"]
    assert all(wk[d] for d in range(settings["days"])), \
        f"no day may be empty: {[(d, pl) for d, pl in enumerate(wk)]}"
    used_days = {}
    for pl in wk:
        for cid in pl:
            used_days[cid] = used_days.get(cid, 0) + 1
    for m in (i for i in comps if comps[i]["role"] == "main"):
        assert used_days.get(m, 0) <= cap, (m, used_days)
    # anti-vacuity: the cap BINDS — power_bowl sits exactly AT it (the soft
    # diversity score alone would serve this plate a 4th day), and the days
    # it lost went to the ensemble mains
    assert used_days["power_bowl"] == cap, used_days
    assert used_days["turkey_block"] >= 1 and used_days["tofu_block"] >= 1
    # the exempt starch rides every ensemble day, above the cap, at tier 0
    assert used_days["rice_pot"] > cap, used_days
    assert diag["relax_tiers"]["dom"] == [0] * settings["days"], diag


# --------------------------------------------------------------------------- #
#  5. unit snapping stays in bounds (discrete_edges)
# --------------------------------------------------------------------------- #
def test_unit_snapping_in_bounds_property_15_seeds():
    """CATCHES: the snap-and-clamp pass emitting a portion off the unit grid
    or outside serve bounds (e.g. rounding 90g of patty to 100g=unit but then
    clamping to a non-multiple, or snapping past serve_max).

    Property over 15 seeded random weight draws on discrete_edges (three
    unit_g components; patty's bounds admit exactly the grid points 100/200):
    every served portion lies within serve bounds AND on the unit grid."""
    ing, comps, people, settings = load("discrete_edges")
    ids = list(comps)
    feasible, unit_portions = 0, 0
    for seed in range(15):
        rng = random.Random(seed)
        w = {i: rng.uniform(0.35, 2.4) for i in ids}
        res = engine.plate(people["disco"], comps, ids, weights=w)
        if not res.ok:
            continue          # snapping may legitimately break a tight band
        feasible += 1
        for cid, g in res.items_g.items():
            # M1.7: bounds are the PERSON's effective (kcal-scaled) band
            lo, hi, _w = engine.effective_serve_bounds(comps[cid],
                                                       people["disco"])
            assert lo <= g <= hi, (seed, cid, g, lo, hi)
            u = comps[cid].get("unit_g")
            if u:
                unit_portions += 1
                assert g % u == 0, \
                    f"seed {seed}: {cid} {g}g off the {u}g unit grid"
    # anti-vacuity: the property must actually have exercised the snapper
    assert feasible >= 5, f"only {feasible}/15 seeds feasible"
    assert unit_portions >= 10, "unit components were barely served"


# --------------------------------------------------------------------------- #
#  6. excluded tags never served (family_four + conflicting_exclusions)
# --------------------------------------------------------------------------- #
def _assert_no_excluded_portions(comps, people, weeks):
    for pname, person in people.items():
        excl = set(person.get("exclude") or [])
        for d, pl in enumerate(weeks[pname]):
            for cid in pl:
                carried = set(comps[cid]["tags"]) & excl
                assert not carried, \
                    f"{pname} day {d}: served '{cid}' carrying {carried}"


def test_excluded_tags_never_served_in_full_weeks(family_week, conflict_week):
    """CATCHES: a regression in tag DERIVATION (component tags = union of
    ingredient tags) or in eligibility filtering — either would let a dairy/
    nut/alpha/beta component onto the excluding person's plate.

    Iterates EVERY person x EVERY day of the assembled weeks on both
    exclusion fixtures."""
    # anti-vacuity: the fixtures really do carry the tags being excluded
    assert family_week["comps"]["cheese_topping"]["tags"] == ["dairy"]
    assert family_week["comps"]["nut_drizzle"]["tags"] == ["nut"]
    assert conflict_week["comps"]["salsa_alpha"]["tags"] == ["alpha"]
    assert conflict_week["comps"]["cream_beta"]["tags"] == ["beta"]
    for fx in (family_week, conflict_week):
        _assert_no_excluded_portions(fx["comps"], fx["people"], fx["weeks"])
    # the accent-split is real: the non-excluded accents ARE eaten
    alice_served = {c for pl in conflict_week["weeks"]["alice"] for c in pl}
    bob_served = {c for pl in conflict_week["weeks"]["bob"] for c in pl}
    assert alice_served & {"salsa_beta", "cream_beta"}
    assert bob_served & {"salsa_alpha", "crunch_alpha"}


def test_excluded_tags_never_served_plate_property_seeds():
    """CATCHES: an eligibility leak that only appears under unusual objective
    weights (the build_week path above always uses diverse_plates weights;
    this drives plate() directly across seeded random weight draws)."""
    for name in ("family_four", "conflicting_exclusions"):
        ing, comps, people, settings = load(name)
        ids = list(comps)
        for pname, person in people.items():
            excl = set(person.get("exclude") or [])
            if not excl:
                continue
            for seed in range(3):
                rng = random.Random(seed)
                w = {i: rng.uniform(0.35, 2.4) for i in ids}
                res = engine.plate(person, comps, ids, weights=w)
                for cid in res.items_g:
                    assert not (set(comps[cid]["tags"]) & excl), \
                        (name, pname, seed, cid)


# --------------------------------------------------------------------------- #
#  7. determinism golden (solo_lifter, full pipeline)
# --------------------------------------------------------------------------- #
def _jsonable(x):
    """Canonical JSON-ready form: tuples -> lists, dict keys stringified."""
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def golden_payload(p):
    """The byte-stable serialization of the solo_lifter full pipeline."""
    obj = dict(
        fixture="solo_lifter", seed=GOLDEN_SEED,
        menu_kwargs={k: v for k, v in GOLDEN_MENU_KW.items()},
        menu=p["menu"], feasible=p["feasible"], broke=p["broke"],
        weeks=p["weeks"], demand=p["demand"],
        session_plan=p["session_plan"], purchase=p["purchase"],
        waste_perishable=p["waste_perishable"],
        waste_total=p["waste_total"])
    return json.dumps(_jsonable(obj), sort_keys=True, indent=1,
                      ensure_ascii=False) + "\n"


def test_determinism_golden_full_pipeline_byte_stable(solo_pipeline):
    """CATCHES: any nondeterminism across the WHOLE pipeline (menu search,
    week assembly, session attribution, purchasing) — and any silent behavior
    change: same inputs + seed must reproduce the recorded plan byte for byte
    on the pinned reference environment (PRD §9; tests/golden/README.md).
    Off the reference environment this test SKIPS and
    test_golden_pipeline_properties_all_platforms carries the guarantee —
    the sanctioned green path PRD §9 requires.

    A legitimate engine change shows up here as a diff to be reviewed and
    deliberately re-recorded — never auto-regenerated."""
    if not refenv.is_reference_env():
        pytest.skip(refenv.off_reference_reason())
    got = golden_payload(solo_pipeline)
    ref = (GOLDEN / "solo_lifter_pipeline.json").read_text()
    assert got == ref, (
        "pipeline output diverged from tests/golden/solo_lifter_pipeline.json"
        " — if the change is intended, regenerate deliberately per"
        " tests/golden/README.md")


def golden_dish_payload(p):
    """The byte-stable serialization of the solo_dishes DISH-MODE pipeline
    (M1.13, M113_SPEC §12: the determinism golden extends to dish output —
    mealdays carry dish identity, servings scalars, and flags)."""
    obj = dict(
        fixture="solo_dishes", seed=GOLDEN_SEED,
        menu_kwargs={k: v for k, v in GOLDEN_DISH_MENU_KW.items()},
        menu=p["menu"], closure=p["closure"],
        feasible=p["feasible"], broke=p["broke"],
        weeks=p["weeks"], demand=p["demand"], mealdays=p["mealdays"],
        session_plan=p["session_plan"], purchase=p["purchase"],
        waste_perishable=p["waste_perishable"],
        waste_total=p["waste_total"])
    return json.dumps(_jsonable(obj), sort_keys=True, indent=1,
                      ensure_ascii=False) + "\n"


def test_determinism_golden_dish_pipeline_byte_stable(solo_dish_pipeline):
    """M1.13's half of the golden policy (M113_SPEC §12/§14 slow-1):
    same inputs + seed must reproduce the recorded DISH-MODE plan byte for
    byte on the pinned reference environment — dish assignment (skeleton),
    portioning (plate-dish LP), meal composition, session attribution, and
    purchasing all inside the guarantee. Off-reference this SKIPS and
    test_golden_dish_pipeline_properties_all_platforms carries it."""
    if not refenv.is_reference_env():
        pytest.skip(refenv.off_reference_reason())
    got = golden_dish_payload(solo_dish_pipeline)
    ref = (GOLDEN / "solo_dishes_pipeline.json").read_text()
    assert got == ref, (
        "dish pipeline output diverged from"
        " tests/golden/solo_dishes_pipeline.json — if the change is"
        " intended, regenerate deliberately per tests/golden/README.md")


def test_golden_dish_pipeline_properties_all_platforms(solo_dish_pipeline):
    """The every-platform half for dish mode: properties of the frozen
    solo_dishes pipeline that hold on any OS/arch/CBC build."""
    p = solo_dish_pipeline
    settings, dishes = p["settings"], p["dishes"]
    assert p["feasible"] is True and p["broke"] == {}
    assert p["menu"] and set(p["menu"]) <= set(dishes)
    assert set(p["weeks"]) == set(p["people"]) == set(p["mealdays"])
    for pname, mds in p["mealdays"].items():
        assert len(mds) == settings["days"], pname
        for md in mds:
            for meal in md["meals"]:
                # a hole would carry dish=None — the frozen fixture solves
                # every slot with a named menu dish
                assert meal["dish"] in p["menu"], (pname, meal)
    # conservation: Σ meals == day plate == demand (definitional, §8)
    total = {}
    for pname, wk in p["weeks"].items():
        assert len(wk) == settings["days"]
        for plate, md in zip(wk, p["mealdays"][pname]):
            dealt = {}
            for meal in md["meals"]:
                for cid, g in meal["items"].items():
                    dealt[cid] = dealt.get(cid, 0) + g
            assert dealt == plate
            for cid, g in plate.items():
                total[cid] = total.get(cid, 0) + g
    assert total == p["demand"]
    sp = p["session_plan"]
    assert sp["unattributed"] == []
    for name, need, units, pack, left, per, keeps in p["purchase"]:
        assert units * pack >= need, name
    # determinism ON this machine: the same frozen inputs re-run must
    # serialize identically to the fixture's run
    assert golden_dish_payload(dish_pipeline_run()) == \
        golden_dish_payload(p)


def test_golden_pipeline_properties_all_platforms(solo_pipeline):
    """The every-platform half of the golden policy (PRD §9): where the byte
    compare only runs on the pinned reference environment, these PROPERTIES
    of the same frozen pipeline hold on any OS/arch/CBC build. CATCHES:
    gross pipeline breakage everywhere, without byte-claims a foreign CBC
    could legally falsify by picking a different equal-cost optimum."""
    p = solo_pipeline
    settings, comps = p["settings"], p["comps"]
    assert p["feasible"] is True and p["broke"] == {}
    assert p["menu"] and set(p["menu"]) <= set(comps)
    assert set(p["weeks"]) == set(p["people"])
    for pname, wk in p["weeks"].items():
        assert len(wk) == settings["days"], pname
        assert any(pl for pl in wk), f"{pname}: all-empty week"
    assert p["demand"] and all(g > 0 for g in p["demand"].values())
    sp = p["session_plan"]
    assert sp["unattributed"] == []      # a feasible golden feeds every day
    assert sp["minutes"] == sum(s["minutes"] for s in sp["sessions"])
    for name, need, units, pack, left, per, keeps in p["purchase"]:
        assert units * pack >= need, name
    assert p["waste_total"] >= p["waste_perishable"] >= 0
    # determinism ON this machine (whatever it is): the same frozen inputs
    # re-run must serialize identically to the fixture's run
    ing2, comps2, people2, settings2 = load("solo_lifter")
    menu2, _, feas2, broke2 = engine.choose_menu(
        comps2, ing2, people2, settings2, **GOLDEN_MENU_KW)
    weeks2, demand2 = engine.build_week(comps2, people2, settings2, menu2,
                                        seed=GOLDEN_SEED, ing=ing2)
    sp2 = costing.session_plan(comps2, ing2, settings2, weeks2)
    rows2, wp2, wt2 = costing.purchase(comps2, ing2, menu2, sp2["batches"])
    rerun = dict(ing=ing2, comps=comps2, people=people2, settings=settings2,
                 menu=menu2, feasible=feas2, broke=broke2, weeks=weeks2,
                 demand=demand2, session_plan=sp2, purchase=rows2,
                 waste_perishable=wp2, waste_total=wt2)
    assert golden_payload(rerun) == golden_payload(p)


# --------------------------------------------------------------------------- #
#  8. volume-floor ordering (watery_vs_dense)
# --------------------------------------------------------------------------- #
def test_volume_floor_watery_library_needs_more_mass_than_dense():
    """CATCHES: volume_floor not actually responding to energy density —
    e.g. bisecting on the wrong variable or ignoring max_daily_mass_g. Same
    person, same 300g carb target: the 10 c/100g library needs ~2,850g/day;
    the 70 c/100g library bottoms out the search range."""
    _, watery, wp, _ = load("watery_vs_dense/watery")
    _, dense, dp, _ = load("watery_vs_dense/dense")
    fw = engine.volume_floor(wp["floaty"], watery)
    fd = engine.volume_floor(dp["floaty"], dense)
    assert fw["floor_g"] is not None and fd["floor_g"] is not None
    assert fw["floor_g"] > fd["floor_g"], (fw, fd)
    assert fw["floor_g"] >= 2500, fw     # ~285g carb / 0.10 density
    assert fd["floor_g"] <= 600, fd


# --------------------------------------------------------------------------- #
#  9. raw freshness: blocked session explained; freezable sibling thaws
# --------------------------------------------------------------------------- #
def test_raw_freshness_blocked_session_is_explained_not_silent():
    """CATCHES: a cook session silently 'cooking' spoiled raw ingredients
    (shop day 0, session day 4, keeps-2 non-freezable shrimp), or demand on
    an unreachable day being silently dropped instead of reported.

    family_four: shrimp_mix (keeps 2, not freezable) blocks shrimp_saute in
    session 1; the blocker names the ingredient; doctor renders the
    explanation; unfeedable demand comes back as 'unattributed'."""
    ing, comps, people, settings = load("family_four")
    ok, blockers, frozen = costing.raw_freshness(comps["shrimp_saute"], 4,
                                                 settings, ing)
    assert not ok
    assert [b["ingredient"] for b in blockers] == ["shrimp_mix"]
    assert blockers[0]["shop_day"] == 0 and blockers[0]["keeps_days"] == 2
    assert costing.cookable_sessions(comps["shrimp_saute"], settings,
                                     ing) == [0]
    # day 5 is only reachable from the blocked session -> not available, and
    # forced demand there is reported, never invented
    assert not engine.available_on(comps["shrimp_saute"], 5, settings,
                                   ing=ing)
    sp = costing.session_plan(comps, ing, settings,
                              {"x": [{}, {}, {}, {}, {}, {"shrimp_saute": 300},
                                     {}]})
    assert sp["unattributed"] == [dict(component="shrimp_saute", day=5,
                                       grams=300)]
    # doctor says so, naming component, session, and offending ingredient
    # (one-person subset: the raw-freshness section is person-independent)
    text, data = engine.doctor(comps, {"petit": people["petit"]}, settings,
                               ing=ing)
    assert any(r["component"] == "shrimp_saute" and r["session"] == 1
               for r in data["raw_freshness"])
    assert "shrimp_saute" in text and "cannot be cooked in session 1" in text
    assert "shrimp_mix" in text


def test_raw_freshness_freezable_sibling_cooks_with_thaw_note():
    """CATCHES: the freezable escape hatch either not unblocking the session
    or unblocking it SILENTLY (no thaw note in the cook plan).

    fish_mix is the freezable sibling (same keeps 2): session 1 may cook
    fish_bake, and the session plan carries the thaw-ahead note."""
    ing, comps, people, settings = load("family_four")
    ok, blockers, frozen = costing.raw_freshness(comps["fish_bake"], 4,
                                                 settings, ing)
    assert ok and blockers == []
    assert [f["ingredient"] for f in frozen] == ["fish_mix"]
    assert costing.cookable_sessions(comps["fish_bake"], settings,
                                     ing) == [0, 1]
    # day-5 fish demand: only session 1 reaches it (keeps 3 cooked) -> thaw
    sp = costing.session_plan(comps, ing, settings,
                              {"x": [{}, {}, {}, {}, {}, {"fish_bake": 300},
                                     {}]})
    s1 = sp["sessions"][1]
    assert s1["batches"] == {"fish_bake": 1}
    assert any(n["ingredient"] == "fish_mix"
               and "thaw before session 1" in n["note"]
               for n in s1["thaw_notes"]), s1["thaw_notes"]


# --------------------------------------------------------------------------- #
#  fixture hygiene: every fixture loads through the real validation path
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "solo_lifter", "family_four", "conflicting_exclusions", "extreme_carb",
    "all_fatty", "valley", "discrete_edges", "watery_vs_dense/watery",
    "watery_vs_dense/dense", "dominant_main"])
def test_fixture_loads_clean_and_is_tiny(name):
    """CATCHES: a fixture drifting out of schema (io_yaml.load validates
    everything) or growing past the purpose-built size budget that keeps the
    LP-heavy capability tests fast."""
    ing, comps, people, settings = load(name)
    assert 1 <= len(ing) <= 10 and 1 <= len(comps) <= 8


if __name__ == "__main__":
    # Deliberate golden regeneration (reviewed commits only — see
    # tests/golden/README.md):  python tests/test_capabilities.py regen
    import sys
    if sys.argv[1:] == ["regen"]:
        ing, comps, people, settings = load("solo_lifter")
        menu, info, feasible, broke = engine.choose_menu(
            comps, ing, people, settings, **GOLDEN_MENU_KW)
        weeks, demand = engine.build_week(comps, people, settings, menu,
                                          seed=GOLDEN_SEED, ing=ing)
        sp = costing.session_plan(comps, ing, settings, weeks)
        rows, wp, wt = costing.purchase(comps, ing, menu, sp["batches"])
        p = dict(menu=menu, feasible=feasible, broke=broke, weeks=weeks,
                 demand=demand, session_plan=sp, purchase=rows,
                 waste_perishable=wp, waste_total=wt)
        out = GOLDEN / "solo_lifter_pipeline.json"
        out.write_text(golden_payload(p))
        print(f"wrote {out}")
        # M1.13: the dish-mode golden (same deliberate-regeneration policy)
        out = GOLDEN / "solo_dishes_pipeline.json"
        out.write_text(golden_dish_payload(dish_pipeline_run()))
        print(f"wrote {out}")
