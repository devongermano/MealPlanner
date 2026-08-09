"""M0 Phase 5 (adversarial review) regression tests.

Covers the verified gate findings:

- validation type gaps: non-numeric macros / pack_g / unit_g / serve bounds
  are structured validation ERRORS, never raw engine tracebacks (M0.2's
  all-errors contract on user-editable YAML);
- cook_days canonicalization: session attribution (PRD §8.2 "earliest
  session wins") is invariant under YAML list order of cook_days;
- budget doc validation: mode enum (incl. the attribution-only
  'by_consumption'), period reserved to 'week';
- P8 relaxation surfacing: a week whose variety caps were relaxed says so
  in the rendered output;
- previously-untested engine behaviors: dislikes soft down-weighting,
  max_batches_per_component binding, budget mode 'per_person',
  costing.attribute, the active_min_budget over-budget score term, and the
  SCORE_WEIGHTS override surface (PRD §8.3).
"""

import pytest

from mealplan import cli, costing, engine, io_yaml, model
from mealplan.costing import attribute, budget_ceiling, session_plan
from mealplan.engine import SCORE_WEIGHTS
from mealplan.io_yaml import (validate_components_doc,
                              validate_ingredients_doc, validate_people_doc)


def errors_of(issues):
    return [i for i in issues if i.severity == "error"]


# --------------------------------------------------------------------------- #
#  validation type gaps — structured errors, not tracebacks
# --------------------------------------------------------------------------- #
def _ing(**over):
    d = {"p": 20.0, "f": 5.0, "c": 10.0, "perishable": True, "pack_g": 500,
         "keeps_days": 5, "tags": [], "cost": 4.0}
    d.update(over)
    return d


def _ing_doc(**ings):
    return {"schema_version": 1, "ingredients": dict(ings)}


@pytest.mark.parametrize("field", ["p", "f", "c", "cost", "keeps_days",
                                   "pack_g"])
@pytest.mark.parametrize("bad", ["high", [1], True])
def test_non_numeric_ingredient_field_is_structured_error(field, bad):
    errs = errors_of(validate_ingredients_doc(_ing_doc(x=_ing(**{field: bad}))))
    assert any(e.code == "bad_number" and f"'{field}'" in e.where
               for e in errs), (field, bad, [str(e) for e in errs])


def test_string_macro_load_is_validation_error_not_traceback(tmp_path):
    """The verified repro: beef p: "high" used to pass validation and crash
    derive_component with a raw TypeError. It must be ONE structured
    ValidationError from load()."""
    import yaml
    ing_doc = _ing_doc(beef=_ing(p="high"))
    comp_doc = {"schema_version": 1, "components": [
        {"id": "stew", "name": "stew", "cuisine": "test", "role": "main",
         "yield_g": 500, "serve_g": {"min": 100, "max": 400},
         "keeps_days": 5, "active_min": 30, "ingredients": {"beef": 500}}]}
    ppl_doc = {"schema_version": 1,
               "people": {"p1": {"targets": {"protein": 100, "fat": 60,
                                             "carb": 200},
                                 "tolerance": 0.05}},
               "settings": {"days": 7, "active_min_budget": 180,
                            "max_days_same_component": 4, "cook_days": [0]}}
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(ing_doc))
    (lib / "components.yaml").write_text(yaml.safe_dump(comp_doc))
    (lib / "people.yaml").write_text(yaml.safe_dump(ppl_doc))
    with pytest.raises(io_yaml.ValidationError) as ei:
        io_yaml.load(lib)
    assert "bad_number" in ei.value.codes()


def _comp_entry(**over):
    d = {"id": "c1", "name": "c1", "cuisine": "test", "role": "main",
         "yield_g": 1000, "serve_g": {"min": 80, "max": 400},
         "keeps_days": 5, "active_min": 10, "ingredients": {"chicken": 500}}
    d.update(over)
    return d


def _comp_doc(*entries):
    return {"schema_version": 1, "components": list(entries)}


KNOWN = {"chicken"}


@pytest.mark.parametrize("bad", ["30", -30, 0, True])
def test_bad_unit_g_is_validation_error(bad):
    """unit_g '30' passed validation and crashed the discrete-snap pass;
    nonpositive unit_g silently skipped the M0.8 alignment guarantee. Both
    are now bad_unit_g errors."""
    doc = _comp_doc(_comp_entry(unit_g=bad, serve_g={"min": 90, "max": 390}))
    errs = errors_of(validate_components_doc(doc, known_ingredients=KNOWN))
    assert any(e.code == "bad_unit_g" for e in errs), \
        (bad, [str(e) for e in errs])


def test_positive_numeric_unit_g_is_accepted():
    doc = _comp_doc(_comp_entry(unit_g=40))
    errs = errors_of(validate_components_doc(doc, known_ingredients=KNOWN))
    assert not errs, [str(e) for e in errs]


@pytest.mark.parametrize("field,bad", [("yield_g", "1kg"),
                                       ("active_min", "30min"),
                                       ("keeps_days", "week")])
def test_non_numeric_component_field_is_structured_error(field, bad):
    doc = _comp_doc(_comp_entry(**{field: bad}))
    errs = errors_of(validate_components_doc(doc, known_ingredients=KNOWN))
    assert any(e.code == "bad_number" and f"'{field}'" in e.where
               for e in errs), (field, [str(e) for e in errs])


def test_non_numeric_serve_bound_is_structured_error():
    doc = _comp_doc(_comp_entry(serve_g={"min": "80", "max": 400}))
    errs = errors_of(validate_components_doc(doc, known_ingredients=KNOWN))
    assert any(e.code == "bad_number" and "serve_g.min" in e.where
               for e in errs), [str(e) for e in errs]


def test_all_type_errors_reported_together():
    """The all-errors contract holds for the new checks too: one pass
    reports every type problem at once."""
    doc = _ing_doc(a=_ing(p="high", cost="cheap"), b=_ing(pack_g="500g"))
    errs = errors_of(validate_ingredients_doc(doc))
    assert len([e for e in errs if e.code == "bad_number"]) == 3


# --------------------------------------------------------------------------- #
#  cook_days canonicalization — attribution invariant under list order
# --------------------------------------------------------------------------- #
def _stew_lib():
    comps = {"stew": dict(
        id="stew", name="stew", cuisine="test", role="main", yield_g=1000,
        serve_g={"min": 100, "max": 500}, keeps_days=7, active_min=30,
        ingredients={"stuff": 500},
        per100={"kcal": 145.0, "protein": 25.0, "fat": 5.0, "carb": 0.0},
        tags=[])}
    ing = {"stuff": dict(p=25.0, f=5.0, c=0.0, perishable=False,
                         keeps_days=999, pack_g=500, cost=4.0, tags=[])}
    return comps, ing


def _settings(cook_days):
    return model.Settings.from_raw(
        dict(days=7, active_min_budget=600, batch_time_factor=0.5,
             max_days_same_component=7, cook_days=cook_days, shop_days=[0]),
        {"mode": "off"})


def test_cook_days_order_never_changes_session_attribution():
    """The verified repro: demand 500g on day 5, stew keeps 7d, sessions on
    days 0 and 4. PRD §8.2 says the EARLIEST session (day 0) feeds it —
    regardless of whether the YAML says [0, 4] or [4, 0]."""
    comps, ing = _stew_lib()
    weeks = {"p1": [{}, {}, {}, {}, {}, {"stew": 500}, {}]}
    plans = {}
    for cd in ([0, 4], [4, 0], [4, 0, 4]):
        sp = session_plan(comps, ing, _settings(cd), weeks)
        plans[tuple(cd)] = sp
        fed = [s for s in sp["sessions"] if s["batches"].get("stew")]
        assert len(fed) == 1 and fed[0]["start"] == 0, (cd, sp["sessions"])
    a, b, c = plans.values()
    assert a["batches"] == b["batches"] == c["batches"]
    assert a["minutes"] == b["minutes"] == c["minutes"]


def test_settings_from_raw_canonicalizes_cook_days():
    st = _settings([4, 0, 4])
    assert st["cook_days"] == [0, 4]
    assert costing.sessions_for(st) == [0, 4]
    # sessions_for canonicalizes raw dicts too (mirrors shop_days_for)
    assert costing.sessions_for({"cook_days": [4, 0]}) == [0, 4]


# --------------------------------------------------------------------------- #
#  budget doc validation
# --------------------------------------------------------------------------- #
def _ppl_doc(budget=None):
    doc = {"schema_version": 1,
           "people": {"p1": {"targets": {"protein": 100, "fat": 60,
                                         "carb": 200},
                             "tolerance": 0.05}},
           "settings": {"days": 7, "active_min_budget": 180,
                        "cook_days": [0], "max_days_same_component": 4}}
    if budget is not None:
        doc["budget"] = budget
    return doc


@pytest.mark.parametrize("mode", ["shared", "per_person", "by_consumption",
                                  "off"])
def test_known_budget_modes_are_accepted(mode):
    issues = validate_people_doc(_ppl_doc({"mode": mode}))
    assert not errors_of(issues), mode


def test_unknown_budget_mode_is_error():
    issues = validate_people_doc(_ppl_doc({"mode": "strict"}))
    assert any(i.code == "bad_enum" and "'mode'" in i.where
               for i in errors_of(issues))


def test_unknown_budget_period_is_error():
    issues = validate_people_doc(_ppl_doc({"mode": "shared", "total": 500,
                                           "period": "month"}))
    assert any(i.code == "bad_enum" and "'period'" in i.where
               for i in errors_of(issues))
    assert not errors_of(validate_people_doc(
        _ppl_doc({"mode": "shared", "total": 500, "period": "week"})))


def test_budget_ceiling_per_person_mode():
    """budget mode 'per_person': ceiling is the sum over the people present;
    people missing from the mapping contribute 0; an empty mapping means no
    ceiling. 'by_consumption' and 'off' never produce a ceiling."""
    people = {"a": {}, "b": {}}
    st = {"budget": {"mode": "per_person",
                     "per_person": {"a": 100, "b": 50, "ghost": 999}}}
    assert budget_ceiling(st, people) == 150
    st2 = {"budget": {"mode": "per_person", "per_person": {"a": 100}}}
    assert budget_ceiling(st2, people) == 100
    assert budget_ceiling({"budget": {"mode": "per_person",
                                      "per_person": {}}}, people) is None
    assert budget_ceiling({"budget": {"mode": "by_consumption"}},
                          people) is None
    assert budget_ceiling({"budget": {"mode": "off"}}, people) is None


# --------------------------------------------------------------------------- #
#  costing.attribute — consumption-share cost split
# --------------------------------------------------------------------------- #
def test_attribute_splits_grocery_total_by_consumption_share():
    comps, ing = _stew_lib()
    weeks = {"big": [{"stew": 600}], "small": [{"stew": 300}]}
    shares, eaten = attribute(comps, ing, weeks, grocery_total=90.0)
    # 2:1 consumption -> 2:1 shares, summing to the full grocery bill
    assert shares["big"] == pytest.approx(60.0)
    assert shares["small"] == pytest.approx(30.0)
    assert sum(shares.values()) == pytest.approx(90.0)
    assert eaten["big"] == pytest.approx(2 * eaten["small"])


# --------------------------------------------------------------------------- #
#  dislikes — soft down-weighting (engine.plate)
# --------------------------------------------------------------------------- #
def _twin_lib():
    per = {"kcal": 145.0, "protein": 25.0, "fat": 5.0, "carb": 5.0}
    mk = lambda cid: dict(id=cid, name=cid, cuisine="test", role="main",
                          yield_g=1000, serve_g={"min": 50, "max": 400},
                          keeps_days=7, active_min=10,
                          ingredients={"stuff": 500}, per100=dict(per),
                          tags=[])
    return {"liked": mk("liked"), "hated": mk("hated")}


def test_dislikes_soft_downweight_steers_the_plate():
    """Two nutritionally identical mains; targets one can cover alone. With
    dislikes=['hated'] the tie-break objective must put the plate on
    'liked' — and 'hated' stays ELIGIBLE (soft, not an exclusion)."""
    comps = _twin_lib()
    person = dict(name="p1", targets={"protein": 50, "fat": 10, "carb": 10},
                  tolerance=0.2, exclude=[], dislikes=["hated"])
    ok, pl, miss = engine.plate(person, comps, list(comps))
    assert ok, miss
    assert pl.get("liked", 0) > pl.get("hated", 0), pl
    assert pl.get("hated", 0) == 0, pl
    assert engine.eligible(comps["hated"], person)   # soft != exclude


# --------------------------------------------------------------------------- #
#  max_batches_per_component — the cap actually binds in build_week
# --------------------------------------------------------------------------- #
def _solo_lib():
    comps = {"stew": dict(
        id="stew", name="stew", cuisine="test", role="main", yield_g=400,
        serve_g={"min": 100, "max": 400}, keeps_days=7, active_min=30,
        ingredients={"stuff": 400},
        per100={"kcal": 170.0, "protein": 20.0, "fat": 10.0, "carb": 0.0},
        tags=[])}
    ing = {"stuff": dict(p=20.0, f=10.0, c=0.0, perishable=False,
                         keeps_days=999, pack_g=500, cost=4.0, tags=[])}
    people = {"p1": dict(name="p1",
                         targets={"protein": 60, "fat": 30, "carb": 0},
                         tolerance=0.2, exclude=[], dislikes=[])}
    return comps, ing, people


def _week_settings(cap):
    return model.Settings.from_raw(
        dict(days=7, active_min_budget=600, batch_time_factor=0.5,
             max_days_same_component=7, cook_days=[0], shop_days=[0],
             max_batches_per_component=cap),
        {"mode": "off"})


def test_max_batches_per_component_binds_and_relaxation_is_observable():
    """~300g/day x 7 days on a 400g-yield component: cap 1 (400g) binds by
    day 2 and the relaxation ladder must engage (diag tier > 0 somewhere);
    a loose cap (99) never relaxes. A regression that ignores the cap makes
    the two runs identical."""
    comps, ing, people = _solo_lib()
    diag_tight, diag_loose = {}, {}
    engine.build_week(comps, people, _week_settings(1), list(comps),
                      seed=0, ing=ing, diag=diag_tight)
    engine.build_week(comps, people, _week_settings(99), list(comps),
                      seed=0, ing=ing, diag=diag_loose)
    tight_tiers = diag_tight["relax_tiers"]["p1"]
    loose_tiers = diag_loose["relax_tiers"]["p1"]
    assert all(t == 0 for t in loose_tiers), loose_tiers
    assert any(t is None or t > 0 for t in tight_tiers), \
        f"cap never bound: {tight_tiers}"


# --------------------------------------------------------------------------- #
#  P8 — relaxed variety caps are surfaced in the rendered plan
# --------------------------------------------------------------------------- #
def test_render_flags_relaxed_days_and_strict_weeks_stay_clean():
    comps, ing, people = _solo_lib()
    for cap, expect_flag in ((1, True), (99, False)):
        settings = _week_settings(cap)
        diag = {}
        weeks, demand = engine.build_week(comps, people, settings,
                                          list(comps), seed=0, ing=ing,
                                          diag=diag)
        sp = session_plan(comps, ing, settings, weeks)
        menuinfo = dict(cuisines=1, active_min=sp["minutes"],
                        waste_perishable=0, roles={})
        out = cli.render(comps, ing, people, settings, list(comps), weeks,
                         demand, "", menuinfo, sp, diag=diag)
        flagged = "Variety caps relaxed" in out or \
                  "variety caps relaxed" in out
        assert flagged == expect_flag, (cap, out[:400])


def test_cli_week_output_carries_relaxation_flag(tmp_path, capsys):
    """End to end through the real CLI: a library whose only main cannot
    cover the week under the strict caps must render a relaxation flag
    (P8: the output says so — only tests could see it before)."""
    import yaml
    ing_doc = {"schema_version": 1, "ingredients": {
        "stuff": {"p": 20.0, "f": 10.0, "c": 12.0, "perishable": False,
                  "pack_g": 500, "keeps_days": 999, "cost": 4.0, "tags": []}}}
    comp_doc = {"schema_version": 1, "components": [
        {"id": "stew", "name": "stew", "cuisine": "test", "role": "main",
         "yield_g": 400, "serve_g": {"min": 100, "max": 400},
         "keeps_days": 7, "active_min": 30, "ingredients": {"stuff": 400}},
        {"id": "rice", "name": "rice", "cuisine": "test", "role": "starch",
         "yield_g": 400, "serve_g": {"min": 50, "max": 400},
         "keeps_days": 7, "active_min": 10, "ingredients": {"stuff": 400}}]}
    ppl_doc = {"schema_version": 1,
               "people": {"p1": {"targets": {"protein": 60, "fat": 30,
                                             "carb": 40},
                                 "tolerance": 0.2}},
               "settings": {"days": 7, "active_min_budget": 600,
                            "max_days_same_component": 7, "cook_days": [0],
                            "max_batches_per_component": 1}}
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(ing_doc))
    (lib / "components.yaml").write_text(yaml.safe_dump(comp_doc))
    (lib / "people.yaml").write_text(yaml.safe_dump(ppl_doc))
    out_file = tmp_path / "plan.md"
    cli.main(["week", "--library", str(lib), "--menu", "stew,rice",
              "--out", str(out_file)])
    out = out_file.read_text()
    assert "variety caps relaxed" in out.lower(), out[:600]


# --------------------------------------------------------------------------- #
#  score weights (PRD §8.3) — named, provisional, overridable
# --------------------------------------------------------------------------- #
def _score_fixture():
    comps, ing, people = _solo_lib()
    settings = _week_settings(3)
    return comps, ing, people, settings


def test_active_min_over_budget_term_binds_at_documented_weight():
    """The over-time-budget term was never exercised at a binding value:
    same menu, budget lowered by 10 minutes -> score rises by exactly
    10 x time_over_budget_per_min."""
    comps, ing, people = _solo_lib()
    menu = list(comps)

    def score(budget):
        st = model.Settings.from_raw(
            dict(days=7, active_min_budget=budget, batch_time_factor=0.5,
                 max_days_same_component=7, cook_days=[0], shop_days=[0]),
            {"mode": "off"})
        s, info = engine.score_menu(comps, ing, menu, st, people)
        return s, info

    s_loose, info = score(10_000)          # never binds
    active = info["active_min"]
    s_tight, _ = score(active - 10)        # binds by exactly 10 minutes
    assert s_tight - s_loose == \
        10 * SCORE_WEIGHTS["time_over_budget_per_min"], (s_tight, s_loose)


def test_score_weights_override_changes_exactly_that_term():
    comps, ing, people = _solo_lib()
    menu = list(comps)
    st = model.Settings.from_raw(
        dict(days=7, active_min_budget=600, batch_time_factor=0.5,
             max_days_same_component=7, cook_days=[0], shop_days=[0]),
        {"mode": "off"})
    base, _ = engine.score_menu(comps, ing, menu, st, people)
    # single main => the person_mains_floor penalty is active; doubling its
    # weight must raise the score by exactly the base weight
    w = SCORE_WEIGHTS["person_mains_floor"]
    bumped, _ = engine.score_menu(comps, ing, menu, st, people,
                                  score_weights={"person_mains_floor": 2 * w})
    assert bumped - base == w, (base, bumped)


def test_every_score_weight_is_named_and_numeric():
    """PRD §8.3: score terms are configurable weights with provisional
    provenance — the registry must exist, cover the plate objective too,
    and hold only numbers."""
    for key in ("time_over_budget_per_min", "budget_overage_per_dollar",
                "cost_per_dollar", "dislike_multiplier",
                "plate_slack_dominance", "plate_weight_tiebreak",
                "carb_headroom_shortfall", "lean_anchor_floor",
                "uncovered_lean_day"):
        assert key in SCORE_WEIGHTS, key
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in SCORE_WEIGHTS.values())
