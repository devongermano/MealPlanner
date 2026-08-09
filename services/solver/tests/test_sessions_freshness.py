"""M0.4 + M0.6 regression tests.

M0.4 — canonical session batching (PRD §8.2, P10):
- ONE function (costing.session_plan) attributes each (component, day) demand
  to the EARLIEST cook session whose batch is still within cooked shelf life
  on that day (economy over freshness);
- per-session batches = ceil(session demand / yield_g);
- per-session minutes: first batch full active_min PER SESSION, marginal
  batches at batch_time_factor;
- purchasing and menu cost consume the summed per-session batches;
- the prototype fork (plan.py global ceil vs serve.py latest-session ceils)
  is dead: the CLI week/all cook list renders from session_plan.

M0.6 — raw freshness (PRD §8.2):
- settings.shop_days: sorted day indices, default [0], validated within the
  week, at least one;
- ingredient.freezable: bool, default false;
- a component is cookable in session s iff every perishable ingredient
  satisfies (session_start - nearest prior shop_day) < keeps_days, OR is
  freezable (frozen on arrival -> thaw note in the cook plan);
- wired into build_week availability, menu-search structural score, doctor.
"""

from pathlib import Path

import pytest

from mealplan import costing, engine, io_yaml, model
from mealplan.costing import purchase, session_plan
from mealplan.io_yaml import ValidationError, validate_ingredients_doc, validate_people_doc

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"


# --------------------------------------------------------------------------- #
#  synthetic fixtures — plain dicts, engine reads them dict-style
# --------------------------------------------------------------------------- #
def _comp(cid, ingredients, yield_g=500, keeps=5, active=30, role="main",
          per100=None, serve=(100, 400)):
    return dict(id=cid, name=cid, cuisine="test", role=role, yield_g=yield_g,
                serve_g={"min": serve[0], "max": serve[1]}, keeps_days=keeps,
                active_min=active, ingredients=ingredients,
                per100=per100 or {"kcal": 180.0, "protein": 15.0,
                                  "fat": 8.0, "carb": 10.0},
                tags=[])


def _ing(perishable=True, keeps=5, freezable=False, pack_g=500, cost=5.0):
    d = dict(p=20.0, f=8.0, c=2.0, perishable=perishable, keeps_days=keeps,
             pack_g=pack_g, cost=cost, tags=[])
    if freezable:
        d["freezable"] = True
    return d


# Full validated-settings shape (M0.17): the engine reads settings by plain
# indexing — hand-built dicts must carry every field the model layer would
# have defaulted.
SETTINGS = dict(days=7, active_min_budget=180, batch_time_factor=0.5,
                max_days_same_component=4, cook_days=[0, 4], shop_days=[0],
                min_lean_anchors=2, max_batches_per_component=3,
                use_freezer=True, budget={"mode": "off"})


# --------------------------------------------------------------------------- #
#  M0.4 — earliest-session attribution (economy over freshness)
# --------------------------------------------------------------------------- #
def test_day4_demand_fed_by_session_0_no_extra_batch():
    """cook_days [0,4], component keeps 5 cooked days: day-4 demand is still
    within session 0's shelf life (4 - 0 = 4 < 5), so it is fed by session 0.
    serve.py's latest-session rule would have opened a 2nd batch in session 1;
    the canonical rule must NOT."""
    comps = {"stew": _comp("stew", {"beef": 500}, yield_g=500, keeps=5)}
    ing = {"beef": _ing(perishable=True, keeps=5)}
    weeks = {"p1": [{"stew": 300}, {}, {}, {}, {"stew": 150}, {}, {}]}
    sp = session_plan(comps, ing, SETTINGS, weeks)
    s0, s1 = sp["sessions"]
    assert s0["batches"] == {"stew": 1}          # 450g <= one 500g batch
    assert s1["batches"] == {}                   # NO extra session-1 batch
    assert sp["batches"] == {"stew": 1}
    # serve.py's latest-session split would have been 2 batches total;
    # earliest-session economy needs only 1.


def test_demand_past_session0_life_rolls_to_session_1():
    """A day the session-0 batch cannot reach (day 6, keeps 5: 6-0=5 >= 5)
    must be fed by session 1 (6-4=2 < 5)."""
    comps = {"stew": _comp("stew", {"beef": 500}, yield_g=500, keeps=5)}
    ing = {"beef": _ing(perishable=True, keeps=5)}
    weeks = {"p1": [{"stew": 300}, {}, {}, {}, {}, {}, {"stew": 200}]}
    sp = session_plan(comps, ing, SETTINGS, weeks)
    s0, s1 = sp["sessions"]
    assert s0["batches"] == {"stew": 1}
    assert s1["batches"] == {"stew": 1}
    assert sp["batches"] == {"stew": 2}


def test_made_covers_need_per_component_and_session():
    comps = {
        "stew": _comp("stew", {"beef": 500}, yield_g=400, keeps=3),
        "rice": _comp("rice", {"grain": 300}, yield_g=600, keeps=6,
                      role="starch"),
    }
    ing = {"beef": _ing(keeps=5), "grain": _ing(perishable=False, keeps=999)}
    weeks = {
        "p1": [{"stew": 350, "rice": 200}, {"stew": 300}, {"rice": 250},
               {}, {"stew": 380, "rice": 300}, {"stew": 220}, {"rice": 100}],
        "p2": [{"rice": 150}, {}, {"stew": 100}, {}, {}, {"stew": 200}, {}],
    }
    sp = session_plan(comps, ing, SETTINGS, weeks)
    total_need = {}
    for s in sp["sessions"]:
        for cid, need in s["demand_g"].items():
            made = s["batches"][cid] * comps[cid]["yield_g"]
            assert made >= need, (s["index"], cid)
            total_need[cid] = total_need.get(cid, 0) + need
    # every gram demanded is attributed to exactly one session
    week_demand = {}
    for wk in weeks.values():
        for pl in wk:
            for cid, g in pl.items():
                week_demand[cid] = week_demand.get(cid, 0) + g
    assert total_need == week_demand
    assert sp["unattributed"] == []
    for cid, b in sp["batches"].items():
        assert b * comps[cid]["yield_g"] >= week_demand[cid], cid


def test_session_minutes_sum_to_total_and_first_batch_is_full():
    """Each session pays the full active_min for its first batch; marginal
    batches cost batch_time_factor. Total == sum of per-session minutes."""
    comps = {"stew": _comp("stew", {"beef": 500}, yield_g=300, keeps=5,
                           active=30)}
    ing = {"beef": _ing(keeps=5)}
    # day 0 needs 2 batches in session 0; day 6 forces 1 batch in session 1
    weeks = {"p1": [{"stew": 400}, {}, {}, {}, {}, {}, {"stew": 250}]}
    sp = session_plan(comps, ing, SETTINGS, weeks)
    s0, s1 = sp["sessions"]
    assert s0["batches"] == {"stew": 2}
    assert s1["batches"] == {"stew": 1}
    # session 0: 30 * (1 + 0.5) = 45; session 1: full 30 again (NOT 15)
    assert s0["minutes"] == 45
    assert s1["minutes"] == 30
    assert sp["minutes"] == s0["minutes"] + s1["minutes"] == 75


def test_purchase_covers_summed_session_batches():
    comps = {"stew": _comp("stew", {"beef": 500}, yield_g=500, keeps=5)}
    ing = {"beef": _ing(keeps=5, pack_g=454)}
    weeks = {"p1": [{"stew": 300}, {}, {}, {}, {}, {}, {"stew": 200}]}
    sp = session_plan(comps, ing, SETTINGS, weeks)
    assert sp["batches"] == {"stew": 2}
    rows, wp, wt = purchase(comps, ing, ["stew"], sp["batches"])
    (name, need, units, pack, left, per, keeps), = rows
    assert name == "beef" and need == 1000     # 500g x 2 batches
    assert units * pack >= need


# --------------------------------------------------------------------------- #
#  M0.6 — raw-freshness cookability
# --------------------------------------------------------------------------- #
FRESH_SETTINGS = dict(days=7, active_min_budget=180, batch_time_factor=0.5,
                      max_days_same_component=4, cook_days=[0, 4],
                      shop_days=[0], min_lean_anchors=2,
                      max_batches_per_component=3, use_freezer=True,
                      budget={"mode": "off"})


def _fresh_lib(freezable):
    comps = {
        "grill": _comp("grill", {"fishy": 400, "grain": 100}, yield_g=500,
                       keeps=3,
                       per100={"kcal": 160.0, "protein": 18.0, "fat": 4.0,
                               "carb": 12.0}),
        "pilaf": _comp("pilaf", {"grain": 300}, yield_g=600, keeps=6,
                       role="starch",
                       per100={"kcal": 190.0, "protein": 5.0, "fat": 2.0,
                               "carb": 38.0}),
    }
    ing = {"fishy": _ing(perishable=True, keeps=2, freezable=freezable),
           "grain": _ing(perishable=False, keeps=999)}
    return comps, ing


def test_nonfreezable_keeps2_protein_uncookable_in_session_1():
    """Shop day 0, session 1 on day 4: the keeps-2 protein is 4 days old at
    cook time (4 >= 2) and not freezable -> session 1 cannot cook it, and the
    explanation names the offending ingredient."""
    comps, ing = _fresh_lib(freezable=False)
    ok, blockers, frozen = costing.raw_freshness(comps["grill"], 4,
                                                 FRESH_SETTINGS, ing)
    assert not ok
    assert [b["ingredient"] for b in blockers] == ["fishy"]
    assert blockers[0]["shop_day"] == 0
    assert frozen == []
    assert costing.cookable_sessions(comps["grill"], FRESH_SETTINGS,
                                     ing) == [0]
    # session 0 itself is fine (0 - 0 = 0 < 2)
    assert costing.raw_freshness(comps["grill"], 0, FRESH_SETTINGS, ing)[0]


def test_freezable_true_makes_session_1_cookable_with_thaw_note():
    comps, ing = _fresh_lib(freezable=True)
    ok, blockers, frozen = costing.raw_freshness(comps["grill"], 4,
                                                 FRESH_SETTINGS, ing)
    assert ok and blockers == []
    assert [f["ingredient"] for f in frozen] == ["fishy"]
    assert costing.cookable_sessions(comps["grill"], FRESH_SETTINGS,
                                     ing) == [0, 1]
    # demand on day 5 (cooked keeps 3: only session 1 reaches it) -> session 1
    # cooks it, and the cook plan carries the thaw note
    weeks = {"p1": [{}, {}, {}, {}, {}, {"grill": 300}, {}]}
    sp = session_plan(comps, ing, FRESH_SETTINGS, weeks)
    s1 = sp["sessions"][1]
    assert s1["batches"] == {"grill": 1}
    notes = [n["note"] for n in s1["thaw_notes"]]
    assert any("frozen on day 0" in n and "thaw before session 1" in n
               for n in notes), notes
    assert any(n["ingredient"] == "fishy" for n in s1["thaw_notes"])
    # session 0 gets no thaw note (bought fresh that day)
    assert sp["sessions"][0]["thaw_notes"] == []


def test_uncookable_demand_is_reported_not_silently_dropped():
    """If demand lands on a day only an uncookable session could feed, the
    plan reports it instead of inventing a batch."""
    comps, ing = _fresh_lib(freezable=False)
    weeks = {"p1": [{}, {}, {}, {}, {}, {"grill": 300}, {}]}
    sp = session_plan(comps, ing, FRESH_SETTINGS, weeks)
    assert sp["batches"].get("grill") is None
    assert sp["unattributed"] == [dict(component="grill", day=5, grams=300)]


def test_available_on_accounts_for_raw_freshness():
    comps, ing = _fresh_lib(freezable=False)
    g = comps["grill"]
    # day 5: cooked-life reachable only from session 1 (5-4=1 < 3), but
    # session 1 cannot cook it -> unavailable once ing is considered
    assert engine.available_on(g, 5, FRESH_SETTINGS)          # shape only
    assert not engine.available_on(g, 5, FRESH_SETTINGS, ing=ing)
    # day 1 comes from session 0, which is fine
    assert engine.available_on(g, 1, FRESH_SETTINGS, ing=ing)
    comps2, ing2 = _fresh_lib(freezable=True)
    assert engine.available_on(comps2["grill"], 5, FRESH_SETTINGS, ing=ing2)


def test_build_week_never_serves_uncookable_session_output():
    comps, ing = _fresh_lib(freezable=False)
    people = {"p1": dict(name="p1",
                         targets={"protein": 60, "fat": 20, "carb": 120},
                         tolerance=0.5, exclude=[], dislikes=[])}
    weeks, demand = engine.build_week(comps, people, FRESH_SETTINGS,
                                      ["grill", "pilaf"], seed=0, ing=ing)
    for d, pl in enumerate(weeks["p1"]):
        if d >= 3:      # session-0 batch (keeps 3) is gone; session 1 can't cook it
            assert "grill" not in pl, (d, pl)


def test_score_menu_penalizes_uncookable_sessions():
    comps_f, ing_f = _fresh_lib(freezable=False)
    comps_t, ing_t = _fresh_lib(freezable=True)
    people = {"p1": dict(name="p1",
                         targets={"protein": 60, "fat": 20, "carb": 120},
                         tolerance=0.5, exclude=[], dislikes=[])}
    s_f, _ = engine.score_menu(comps_f, ing_f, ["grill", "pilaf"],
                               FRESH_SETTINGS, people)
    s_t, _ = engine.score_menu(comps_t, ing_t, ["grill", "pilaf"],
                               FRESH_SETTINGS, people)
    assert s_f > s_t, (s_f, s_t)


def test_doctor_reports_uncookable_pairs_with_offending_ingredient():
    comps, ing = _fresh_lib(freezable=False)
    people = {"p1": dict(name="p1",
                         targets={"protein": 60, "fat": 20, "carb": 120},
                         tolerance=0.5, exclude=[], dislikes=[])}
    docmsg, _ = engine.doctor(comps, people, FRESH_SETTINGS, ing=ing)
    assert "## Raw freshness" in docmsg
    assert "grill" in docmsg and "fishy" in docmsg and "session 1" in docmsg


# --------------------------------------------------------------------------- #
#  M0.6 — schema: shop_days and freezable validation
# --------------------------------------------------------------------------- #
def _ppl_doc(settings):
    return {"schema_version": 1,
            "people": {"p1": {"targets": {"protein": 100, "fat": 60,
                                          "carb": 200},
                              "tolerance": 0.05}},
            "settings": settings}


def test_shop_days_empty_list_is_error():
    issues = validate_people_doc(_ppl_doc(
        {"days": 7, "active_min_budget": 180, "cook_days": [0],
         "shop_days": []}))
    assert any(i.code == "bad_shop_days" and i.severity == "error"
               for i in issues)


def test_shop_days_out_of_week_is_error():
    issues = validate_people_doc(_ppl_doc(
        {"days": 7, "active_min_budget": 180, "cook_days": [0],
         "shop_days": [0, 9]}))
    assert any(i.code == "shop_day_out_of_range" and i.severity == "error"
               for i in issues)


def test_shop_days_valid_and_absent_are_fine():
    for st in ({"days": 7, "active_min_budget": 180, "cook_days": [0],
                "shop_days": [0, 4]},
               {"days": 7, "active_min_budget": 180, "cook_days": [0]}):
        issues = validate_people_doc(_ppl_doc(st))
        assert not [i for i in issues if i.severity == "error"], st


def test_shop_days_default_is_day_zero_sorted():
    # M0.17: the [0] default lives in the MODEL layer (Settings.from_raw),
    # not as an inline fallback in costing — shop_days_for reads validated
    # settings and only canonicalizes (sorted, deduped).
    st = model.Settings.from_raw({"days": 7, "cook_days": [0]},
                                 {"mode": "off"})
    assert st["shop_days"] == [0]
    assert costing.shop_days_for(st) == [0]
    assert costing.shop_days_for({"days": 7, "shop_days": [4, 0]}) == [0, 4]


def test_freezable_must_be_bool():
    doc = {"schema_version": 1, "ingredients": {
        "beef": {"p": 20, "f": 8, "c": 0, "perishable": True, "pack_g": 454,
                 "keeps_days": 3, "cost": 6.5, "freezable": "yes"}}}
    issues = validate_ingredients_doc(doc)
    assert any(i.code == "bad_freezable" and i.severity == "error"
               for i in issues)


# --------------------------------------------------------------------------- #
#  examples corpus carries the new fields
# --------------------------------------------------------------------------- #
def test_examples_have_shop_days_and_freezable_meats():
    ing, comps, people, settings = io_yaml.load(EXAMPLES)
    assert settings["shop_days"] == [0]
    for iid in ("ground_beef_85", "chicken_breast", "chicken_thigh",
                "ground_turkey_93", "shrimp_raw", "beef_chuck",
                "flank_steak", "pork_shoulder"):
        assert ing[iid].get("freezable") is True, iid
    # produce is not blanket-frozen
    assert not ing["spinach"].get("freezable")
    assert not ing["avocado"].get("freezable")
