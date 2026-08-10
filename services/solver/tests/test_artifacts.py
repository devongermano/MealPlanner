"""M1 Phase 2 fast-tier tests — the three deliverables (M1.1) and relaxed
mode (M1.2).

Every renderer test here builds tiny ALREADY-SOLVED structures by hand
(weeks / session_plan-shaped dicts / purchase rows) — ZERO LP solves, so
this module stays in the fast tier by design. The one end-to-end artifact
test that runs a real solve lives in test_artifacts_e2e.py (SLOW_MODULES).
"""

import math
import random

import pytest

from mealplan import artifacts, model
from mealplan.artifacts import (day_error_bars, footer,
                                friendly_batch_fraction, render_cook_plan,
                                render_eat_sheet, render_portion,
                                render_shopping_list)
from mealplan.io_yaml import validate_components_doc, validate_people_doc
from mealplan.model import RELAXED_TOLERANCE, Person
from mealplan.units import MACROS

META = {"seed": 7, "library": "testlib", "date": "2026-08-09"}


# --------------------------------------------------------------------------- #
#  hand-built structures — engine reads them dict-style, no solver anywhere
# --------------------------------------------------------------------------- #
def _comp(cid, per100, role="main", serve=(50, 600), keeps=5, unit=None,
          hu=None, yield_g=500, ingredients=None, active=20, freezes=None):
    d = dict(id=cid, name=cid, cuisine="test", role=role, yield_g=yield_g,
             serve_g={"min": serve[0], "max": serve[1]}, keeps_days=keeps,
             active_min=active, ingredients=ingredients or {"stuff": 100},
             per100=per100, tags=[])
    if unit:
        d["unit_g"] = unit
    if hu:
        d["household_unit"] = hu
    if freezes is not None:
        d["freezes"] = freezes
    return d


COMPS = {
    "stew": _comp("stew", {"kcal": 145.0, "protein": 20.0, "fat": 5.0,
                           "carb": 5.0}, yield_g=750, keeps=5,
                  ingredients={"beef": 500, "onion": 200}, active=40),
    "rice": _comp("rice", {"kcal": 130.0, "protein": 2.5, "fat": 0.5,
                           "carb": 28.0}, role="starch", yield_g=1500,
                  keeps=7, hu={"name": "cup", "grams": 185},
                  ingredients={"rice_dry": 500}, active=10),
    "tortilla": _comp("tortilla", {"kcal": 300.0, "protein": 8.0,
                                   "fat": 7.0, "carb": 50.0},
                      role="starch", yield_g=568, keeps=14, unit=71,
                      hu={"name": "tortilla", "grams": 71},
                      ingredients={"tortilla_pack": 568}, active=0),
}

ING = {
    "beef": dict(p=20.0, f=8.0, c=0.0, perishable=True, keeps_days=4,
                 pack_g=454, cost=6.0, tags=[]),
    "onion": dict(p=1.0, f=0.0, c=9.0, perishable=True, keeps_days=14,
                  pack_g=500, cost=1.0, tags=[]),
    "rice_dry": dict(p=7.0, f=1.0, c=78.0, perishable=False, keeps_days=365,
                     pack_g=907, cost=3.0, tags=[]),
    "tortilla_pack": dict(p=8.0, f=7.0, c=50.0, perishable=False,
                          keeps_days=14, pack_g=568, cost=3.5, tags=[]),
}

SET = model.Settings.from_raw(
    dict(days=7, active_min_budget=100, batch_time_factor=0.45,
         max_days_same_component=4, cook_days=[0, 4], shop_days=[0]),
    {"mode": "off"})

PRECISE = dict(name="ada", targets={"protein": 150, "fat": 60, "carb": 300},
               tolerance=0.05, mode="precision", exclude=[], dislikes=[])
RELAXED = dict(name="rex", targets={"protein": 150, "fat": 60, "carb": 300},
               tolerance=RELAXED_TOLERANCE, mode="relaxed", exclude=[],
               dislikes=[])


def _sp(minutes_total=None, sessions=None, leftover=None, unattributed=None):
    """A hand-built costing.session_plan-shaped structure."""
    sessions = sessions if sessions is not None else [
        dict(index=0, start=0, demand_g={"stew": 900, "rice": 1200},
             batches={"stew": 2, "rice": 1},
             made_g={"stew": 1500, "rice": 1500}, minutes=68,
             thaw_notes=[], freezer_notes=[]),
        dict(index=1, start=4, demand_g={"stew": 600},
             batches={"stew": 1}, made_g={"stew": 750}, minutes=40,
             thaw_notes=[dict(component="stew", ingredient="beef",
                              shop_day=0, session=1,
                              note="'beef' for 'stew': frozen on day 0, "
                                   "thaw before session 1 (day 4)")],
             freezer_notes=[]),
    ]
    total = {}
    for s in sessions:
        for cid, b in s["batches"].items():
            total[cid] = total.get(cid, 0) + b
    return dict(sessions=sessions, batches=total,
                minutes=(minutes_total if minutes_total is not None
                         else sum(s["minutes"] for s in sessions)),
                unattributed=unattributed or [], freezer=[],
                leftover=leftover or [])


# purchase()-shaped rows: (name, need, units, pack, left, perishable, keeps)
ROWS = [
    ("beef", 1500, 4, 454, 316, True, 4),
    ("onion", 400, 1, 500, 100, True, 14),
    ("rice_dry", 500, 1, 907, 407, False, 365),
]


# =========================================================================== #
#  M1.1 — shopping_list.md
# =========================================================================== #
def test_shopping_list_checkboxes_groups_and_store_units():
    out = render_shopping_list(ING, ROWS, 25.50, meta=META)
    # checkbox rows in store units via human_pack (454 -> "1 lb")
    assert "- [ ] beef — 4 × 1 lb" in out
    assert "- [ ] rice_dry — 1 × 32 oz" in out
    # grouped: perishable section before shelf-stable, members in the
    # right group
    per = out.split("## Perishable")[1].split("## Shelf-stable")[0]
    stable = out.split("## Shelf-stable")[1]
    assert "beef" in per and "onion" in per and "rice_dry" not in per
    assert "rice_dry" in stable
    # perishable rows carry raw shelf life
    assert "keeps 4d raw" in per


def test_shopping_list_pantry_deduction_notes():
    pantry = {"stock": [{"ingredient": "beef", "grams": 300,
                         "acquired": "2026-08-08"},
                        {"ingredient": "rice_dry", "grams": 900,
                         "acquired": "2026-08-01"}]}
    rows = [
        ("beef", 1200, 3, 454, 162, True, 4),        # deducted, still buying
        ("rice_dry", 0, 0, 907, 0, False, 365),      # fully covered
    ]
    out = render_shopping_list(ING, rows, 19.00, pantry=pantry, meta=META)
    assert "have 300g in pantry, buying the rest" in out
    assert "covered by pantry (have 900g) — nothing to buy" in out
    # a covered row is not a checkbox — nothing to tick off
    assert "- [ ] rice_dry" not in out


def test_shopping_list_expiring_unused_pantry_warning():
    warn = [dict(code="stock_expiring_unused", ingredient="beef", grams=250,
                 message="250g of 'beef' stock (acquired 2026-08-01, 8 days "
                         "old, keeps 4d raw) cannot survive to any cook "
                         "session — not deducted from the shopping list; "
                         "it will expire unused")]
    out = render_shopping_list(ING, ROWS, 25.50, stock_warnings=warn,
                               meta=META)
    assert "Pantry warnings" in out
    assert "expire unused" in out and "250g of 'beef'" in out


def test_shopping_list_total_cost_carries_estimate_provenance():
    out = render_shopping_list(ING, ROWS, 25.5, meta=META)
    assert "**Total estimated cost: $25.50**" in out
    assert "at estimated prices" in out


# =========================================================================== #
#  M1.1 — cook_plan.md
# =========================================================================== #
def test_cook_plan_batches_and_scaled_ingredients():
    out = render_cook_plan(COMPS, SET, _sp(), meta=META)
    s0 = out.split("## Session 0")[1].split("## Session 1")[0]
    # component × batch count
    assert "stew × 2 batches" in s0
    assert "rice × 1 batch" in s0
    # ingredient grams scaled by the session's batches (2 × recipe)
    assert "- [ ] beef: 1000g" in s0
    assert "- [ ] onion: 400g" in s0
    # session 1 cooks one batch — unscaled grams
    s1 = out.split("## Session 1")[1]
    assert "- [ ] beef: 500g" in s1


def test_cook_plan_over_budget_flag_is_explicit_never_silent():
    sp = _sp(minutes_total=140)          # budget is 100
    out = render_cook_plan(COMPS, SET, sp, meta=META)
    assert "**OVER budget by 40 min**" in out
    ok = render_cook_plan(COMPS, SET, _sp(minutes_total=90), meta=META)
    assert "OVER" not in ok
    assert "10 min under budget" in ok


def test_cook_plan_thaw_leftover_and_shortest_keeps():
    lo = [dict(component="stew", day=1, grams=300,
               note="'stew' day 1: 300g from leftover (cooked 2026-08-07) — "
                    "leftovers are already paid for, eat them first")]
    out = render_cook_plan(COMPS, SET, _sp(leftover=lo), meta=META)
    assert "THAW: 'beef' for 'stew': frozen on day 0" in out
    assert "LEFTOVER FIRST: 300g of 'stew' from the fridge before cooking" \
        in out
    # shortest-keeps callout per session: session 0 cooks stew (5d) and
    # rice (7d) -> stew; good through day start + keeps - 1 = 4
    s0 = out.split("## Session 0")[1].split("## Session 1")[0]
    assert "Shortest keeps this session: **stew** — 5d cooked; " \
           "good through day 4" in s0


def test_cook_plan_unattributed_demand_is_a_warning():
    sp = _sp(unattributed=[dict(component="stew", day=6, grams=250)])
    out = render_cook_plan(COMPS, SET, sp, meta=META)
    assert "WARNING: 250g of `stew` demanded on day 6" in out


# =========================================================================== #
#  M1.1 — eat sheets (precision)
# =========================================================================== #
WEEK = [{"stew": 450, "rice": 555, "tortilla": 142}] + [{}] * 0


def test_eat_sheet_precision_exact_grams_sorted_desc():
    out = render_eat_sheet("ada", PRECISE, COMPS, [dict(WEEK[0])], SET,
                           list(COMPS), ing=ING, meta=META)
    day = out.split("## Day 1")[1]
    # portions sorted desc: rice 555 > stew 450 > tortilla 142
    assert (day.index("rice") < day.index("stew")
            < day.index("tortilla"))
    assert "rice: **555g**" in day
    assert "stew: **450g**" in day
    assert "tortilla: **142g**" in day
    # precision people never see approximations
    assert "about" not in day
    assert "eyeball" not in out


def test_eat_sheet_macro_totals_vs_targets_tolerance_status():
    # rice 555g: p 13.875 f 2.775 c 155.4 | stew 450g: p 90 f 22.5 c 22.5
    # tortilla 142g: p 11.36 f 9.94 c 71 -> tot p 115.2 f 35.2 c 248.9
    # targets 150/60/300 at ±5%: p short (-35), f short (-25), c short (-51)
    out = render_eat_sheet("ada", PRECISE, COMPS, [dict(WEEK[0])], SET,
                           list(COMPS), ing=ING, meta=META)
    assert "- protein: 115g of 150g target — -35g short" in out
    assert "- fat: 35g of 60g target — -25g short" in out
    assert "- carb: 249g of 300g target — -51g short" in out
    # a plate inside the band reads "hit"; one over reads "+Ng over"
    p2 = dict(PRECISE, targets={"protein": 115, "fat": 30, "carb": 250})
    out2 = render_eat_sheet("ada", p2, COMPS, [dict(WEEK[0])], SET,
                            list(COMPS), ing=ING, meta=META)
    assert "- protein: 115g of 115g target — hit" in out2
    assert "- fat: 35g of 30g target — +5g over" in out2


def test_eat_sheet_explained_hole_names_expired_components():
    # day 7 (index 6): stew keeps 5d from cook day 4 -> covers through day 8;
    # make a short-keeps comp instead: keeps 2 -> last covered day is 5
    comps = {"salad": _comp("salad", {"kcal": 50.0, "protein": 2.0,
                                      "fat": 1.0, "carb": 8.0}, keeps=2),
             "rice": COMPS["rice"]}
    week = [{"salad": 200, "rice": 300}] + [{}] * 5 + [{}]
    out = render_eat_sheet("ada", PRECISE, comps, week, SET, list(comps),
                           ing=None, meta=META)
    d7 = out.split("## Day 7")[1]
    assert "NO FEASIBLE PLATE" in d7
    # salad is named as past shelf life; rice (keeps 7d) is not
    expired_line = [l for l in d7.splitlines()
                    if "past shelf life" in l][0]
    assert "salad" in expired_line and "rice" not in expired_line


def test_eat_sheet_freezer_thaw_marking():
    comps = {"stew": _comp("stew", {"kcal": 145.0, "protein": 20.0,
                                    "fat": 5.0, "carb": 5.0},
                           keeps=3, freezes=True)}
    st = model.Settings.from_raw(
        dict(days=7, active_min_budget=100, batch_time_factor=0.45,
             max_days_same_component=7, cook_days=[0], shop_days=[0],
             use_freezer=True),
        {"mode": "off"})
    week = [{}] * 5 + [{"stew": 300}, {}]
    out = render_eat_sheet("ada", PRECISE, comps, week, st, list(comps),
                           ing=None, meta=META)
    d6 = out.split("## Day 6")[1].split("## Day 7")[0]
    assert "from freezer — thaw ahead" in d6


# =========================================================================== #
#  M1.2 — relaxed rendering + honest error bars
# =========================================================================== #
def test_relaxed_unit_component_renders_counts():
    txt, implied = render_portion(COMPS["tortilla"], 284, "relaxed")
    assert txt.startswith("**4 tortillas**")
    assert implied == 284.0              # solver already snapped to units


def test_relaxed_household_unit_rounds_to_half_units():
    # 340g of 185g/cup rice = 1.84 cups -> nearest half is 2 cups (370g)
    txt, implied = render_portion(COMPS["rice"], 340, "relaxed")
    assert "about 2 cups" in txt and "~370g" in txt
    assert implied == pytest.approx(370.0)
    # 278g = 1.5 cups nearly exactly
    txt2, implied2 = render_portion(COMPS["rice"], 270, "relaxed")
    assert "about 1 1/2 cups" in txt2
    assert implied2 == pytest.approx(277.5)
    # tiny portions never round to zero
    txt3, implied3 = render_portion(COMPS["rice"], 30, "relaxed")
    assert "about 1/2 cup" in txt3
    assert implied3 == pytest.approx(92.5)


def test_relaxed_other_components_render_friendly_batch_fractions():
    # 250g of a 750g batch = 1/3
    txt, implied = render_portion(COMPS["stew"], 250, "relaxed")
    assert "about 1/3 of the batch" in txt
    assert implied == pytest.approx(250.0)
    # 700g of 750 = 0.933 -> whole batch
    txt2, implied2 = render_portion(COMPS["stew"], 700, "relaxed")
    assert "about the whole batch" in txt2
    assert implied2 == pytest.approx(750.0)
    # 1100g of 750 = 1.467 -> 1 1/2 batches
    txt3, implied3 = render_portion(COMPS["stew"], 1100, "relaxed")
    assert "about 1 1/2 batches" in txt3
    assert implied3 == pytest.approx(1125.0)


def test_precision_render_portion_is_exact_grams():
    txt, implied = render_portion(COMPS["rice"], 340, "precision")
    assert txt == "**340g**" and implied == 340.0


def test_friendly_fraction_grid_only():
    friendly = {0.0, 0.25, 1 / 3, 0.5, 2 / 3, 0.75}
    rng = random.Random(20260809)
    for _ in range(300):
        x = rng.uniform(0.02, 3.0)
        v = friendly_batch_fraction(x)
        assert v > 0
        frac = v - int(v + 1e-9)
        assert any(abs(frac - f) < 1e-9 for f in friendly), (x, v)


def test_error_bars_are_real_arithmetic_from_the_rounding_applied():
    # rice 340g rendered as 2 cups (370g): delta +30g -> carb +8.4g,
    # protein +0.75g, fat +0.15g. stew 250g renders exactly (1/3 batch).
    entries = [("rice", 340.0, 370.0), ("stew", 250.0, 250.0)]
    bars = day_error_bars(entries, COMPS)
    solved_carb = 340 * 28.0 / 100 + 250 * 5.0 / 100      # 107.7
    assert bars["carb"] == (math.floor(solved_carb),
                            math.ceil(solved_carb + 30 * 28.0 / 100))
    # a negative delta widens the LOW side, not the high side
    entries2 = [("rice", 340.0, 277.5)]
    bars2 = day_error_bars(entries2, COMPS)
    assert bars2["carb"] == (math.floor(340 * .28 - 62.5 * .28),
                             math.ceil(340 * .28))


def test_relaxed_eat_sheet_shows_units_and_error_bars():
    week = [{"stew": 250, "rice": 340, "tortilla": 142}]
    out = render_eat_sheet("rex", RELAXED, COMPS, week, SET, list(COMPS),
                           ing=ING, meta=META)
    assert "2 tortillas" in out
    assert "about 2 cups" in out and "~370g" in out
    assert "about 1/3 of the batch" in out
    assert "If you eyeball it:" in out
    assert "Mode: **relaxed**" in out
    # tolerance shown is the relaxed default
    assert "±12%" in out


@pytest.mark.parametrize("trial", range(20))
def test_property_implied_grams_always_within_printed_bars(trial):
    """M1.2 property: for ANY relaxed day, the macro totals implied by the
    rendered units lie inside the printed error bars. The bars are computed
    from the same deltas the rendering actually applied — no decoration."""
    rng = random.Random(1000 + trial)
    plate = {cid: rng.randrange(30, 1200) for cid in COMPS
             if rng.random() > 0.2}
    if "tortilla" in plate:              # solver emits unit multiples
        plate["tortilla"] = 71 * rng.randrange(1, 5)
    if not plate:
        plate = {"stew": 300}
    entries = []
    for cid, g in plate.items():
        _, implied = render_portion(COMPS[cid], g, "relaxed")
        entries.append((cid, float(g), implied))
    bars = day_error_bars(entries, COMPS)
    for m in MACROS:
        implied_total = sum(ig * COMPS[cid]["per100"][m] / 100
                            for cid, _, ig in entries)
        lo, hi = bars[m]
        assert lo <= implied_total <= hi, (m, plate, bars)
        # and the solved total is inside its own bars too
        assert lo <= bars["solved"][m] <= hi


# =========================================================================== #
#  traceability footer on all three deliverables
# =========================================================================== #
def test_footer_on_every_deliverable():
    want = "plan seed 7 · library testlib · date 2026-08-09"
    shop = render_shopping_list(ING, ROWS, 25.5, meta=META)
    cook = render_cook_plan(COMPS, SET, _sp(), meta=META)
    eat = render_eat_sheet("ada", PRECISE, COMPS, [dict(WEEK[0])], SET,
                           list(COMPS), ing=ING, meta=META)
    for doc in (shop, cook, eat):
        assert want in doc
    assert "date unspecified" in footer({"seed": 0, "library": "x"})


def test_render_artifacts_composes_one_file_per_person():
    weeks = {"ada": [dict(WEEK[0])], "rex": [dict(WEEK[0])]}
    people = {"ada": PRECISE, "rex": RELAXED}
    files = artifacts.render_artifacts(
        COMPS, ING, people, SET, list(COMPS), weeks, _sp(), ROWS, 25.5,
        meta=META)
    assert set(files) == {"shopping_list.md", "cook_plan.md",
                          "eat_ada.md", "eat_rex.md"}
    assert "555g" in files["eat_ada.md"]              # precision grams
    assert "If you eyeball it:" in files["eat_rex.md"]  # relaxed bars


# =========================================================================== #
#  M1.2 — schema: person.mode + component.household_unit
# =========================================================================== #
def _ppl_doc(person_extra=None, drop=()):
    p = {"targets": {"protein": 100, "fat": 60, "carb": 200},
         "tolerance": 0.05}
    p.update(person_extra or {})
    for k in drop:
        p.pop(k, None)
    return {"schema_version": 1, "people": {"p1": p},
            "settings": {"days": 7, "active_min_budget": 180,
                         "cook_days": [0], "max_days_same_component": 4}}


def test_person_mode_enum_validated():
    for bad in ("chill", 3, True, ""):
        issues = validate_people_doc(_ppl_doc({"mode": bad}))
        assert any(i.code == "bad_enum" and "mode" in i.where
                   and i.severity == "error" for i in issues), bad
    for ok in ("precision", "relaxed"):
        issues = validate_people_doc(_ppl_doc({"mode": ok}))
        assert not [i for i in issues if i.severity == "error"], ok
    # absent mode is fine (defaults to precision)
    assert not [i for i in validate_people_doc(_ppl_doc())
                if i.severity == "error"]


def test_relaxed_person_may_omit_tolerance_precision_may_not():
    issues = validate_people_doc(
        _ppl_doc({"mode": "relaxed"}, drop=("tolerance",)))
    assert not [i for i in issues if i.severity == "error"]
    issues = validate_people_doc(_ppl_doc(drop=("tolerance",)))
    assert any(i.code == "missing_field" and "tolerance" in i.where
               for i in issues)


def test_relaxed_default_tolerance_is_the_named_constant():
    assert RELAXED_TOLERANCE == 0.12     # PROVISIONAL (PRD §4.1)
    p = Person.from_raw("rex", {"targets": {"protein": 1, "fat": 1,
                                            "carb": 1}, "mode": "relaxed"})
    assert p.tolerance == RELAXED_TOLERANCE
    assert p["tolerance"] == RELAXED_TOLERANCE     # dict-style too
    # an explicit tolerance always wins over the default
    p2 = Person.from_raw("rex", {"targets": {}, "mode": "relaxed",
                                 "tolerance": 0.2})
    assert p2.tolerance == 0.2
    # precision default stays None-less: field simply absent -> None
    p3 = Person.from_raw("ada", {"targets": {}, "tolerance": 0.05})
    assert p3.mode == "precision" and p3.tolerance == 0.05


def test_relaxed_explicit_tolerance_still_capped_at_half():
    issues = validate_people_doc(
        _ppl_doc({"mode": "relaxed", "tolerance": 0.6}))
    assert any(i.code == "bad_tolerance" for i in issues)


def _comp_doc(extra=None):
    c = {"id": "rice", "name": "rice", "cuisine": "t", "role": "starch",
         "yield_g": 1000, "serve_g": {"min": 100, "max": 500},
         "keeps_days": 5, "active_min": 10, "ingredients": {"rice_dry": 300}}
    c.update(extra or {})
    return {"schema_version": 1, "components": [c]}


def test_household_unit_shape_validated():
    bad_cases = [
        "cup",                                        # not a mapping
        {"name": "cup"},                              # grams missing
        {"grams": 185},                               # name missing
        {"name": "", "grams": 185},                   # empty name
        {"name": "cup", "grams": 0},                  # nonpositive
        {"name": "cup", "grams": -3},
        {"name": "cup", "grams": "185"},              # not a number
        {"name": "cup", "grams": True},               # bool is not a number
        {"name": 3, "grams": 185},                    # name not a string
        {"name": "cup", "grams": 185, "oz": 6.5},     # unknown key
    ]
    for bad in bad_cases:
        issues = validate_components_doc(_comp_doc({"household_unit": bad}),
                                         known_ingredients={"rice_dry"})
        assert any(i.code == "bad_household_unit" and i.severity == "error"
                   for i in issues), bad
    ok = validate_components_doc(
        _comp_doc({"household_unit": {"name": "cup", "grams": 185}}),
        known_ingredients={"rice_dry"})
    assert not [i for i in ok if i.severity == "error"]
    # absent is fine
    assert not [i for i in validate_components_doc(
        _comp_doc(), known_ingredients={"rice_dry"})
        if i.severity == "error"]


def test_mode_is_presentation_only_engine_still_solves_grams():
    """Doctor/PLAN unaffected (M1.2): mode changes rendering and the
    tolerance default — nothing else. Same person, same tolerance, both
    modes -> byte-identical plate LP inputs, so we assert the one consumer
    difference lives in artifacts: render_portion output differs, plate
    grams do not (represented here by the shared solved week)."""
    week = [{"rice": 340}]
    prec = render_eat_sheet("p", dict(PRECISE, targets=RELAXED["targets"]),
                            COMPS, [dict(week[0])], SET, list(COMPS),
                            ing=ING, meta=META)
    rel = render_eat_sheet("p", RELAXED, COMPS, [dict(week[0])], SET,
                           list(COMPS), ing=ING, meta=META)
    # same solved grams underneath: both sheets computed identical macro
    # totals from the same 340g
    assert "- carb: 95g of 300g target" in prec
    assert "- carb: 95g of 300g target" in rel
    # but rendered differently
    assert "**340g**" in prec and "about 2 cups" in rel
