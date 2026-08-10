"""M1.10 fast-tier tests — the sheet rework: compiled cook script (method
fragments, shared-prep consolidation, station summary, technique footnotes),
the portioning matrix (a pure reshape of MealDay x session attribution),
and per-slot serving-model phrasing on the eat sheets.

Every test builds tiny ALREADY-SOLVED structures by hand — ZERO LP solves
(fast tier by design). The one end-to-end with a real solve + --methods
lives in test_artifacts_e2e.py (SLOW_MODULES).
"""

import yaml

from mealplan import artifacts, methods, model
from mealplan.artifacts import (attach_accents, build_portioning,
                                cook_script_lines, render_cook_plan,
                                render_eat_sheet, technique_glossary_lines)
from mealplan.costing import session_plan
from mealplan.io_yaml import validate_people_doc
from mealplan.methods import (consolidate_shared_prep, load_methods,
                              load_techniques, station_summary,
                              step_ingredient)

META = {"seed": 7, "library": "testlib", "date": "2026-08-09"}


# --------------------------------------------------------------------------- #
#  hand-built structures
# --------------------------------------------------------------------------- #
def _comp(cid, per100, role="main", serve=(50, 600), keeps=5, unit=None,
          yield_g=500, ingredients=None, active=20, cuisine="test",
          pairs_with=None, hu=None):
    d = dict(id=cid, name=cid, cuisine=cuisine, role=role, yield_g=yield_g,
             serve_g={"min": serve[0], "max": serve[1]}, keeps_days=keeps,
             active_min=active, ingredients=ingredients or {"stuff": 100},
             per100=per100, tags=[])
    if unit:
        d["unit_g"] = unit
    if pairs_with:
        d["pairs_with"] = pairs_with
    if hu:
        d["household_unit"] = hu
    return d


P_MAIN = {"kcal": 145.0, "protein": 20.0, "fat": 5.0, "carb": 5.0}
P_STARCH = {"kcal": 130.0, "protein": 2.5, "fat": 0.5, "carb": 28.0}
P_ACC = {"kcal": 400.0, "protein": 25.0, "fat": 33.0, "carb": 1.0}

COMPS = {
    "picadillo": _comp("picadillo", P_MAIN, cuisine="mexican",
                       ingredients={"onion_yellow": 150, "beef": 454,
                                    "potato": 300, "carrot": 60}),
    "sugo": _comp("sugo", P_MAIN, cuisine="italian",
                  ingredients={"onion_yellow": 200, "sausage": 500}),
    "rice": _comp("rice", P_STARCH, role="starch", yield_g=1500,
                  ingredients={"rice_dry": 400},
                  hu={"name": "cup", "grams": 185}),
    "cheddar": _comp("cheddar", P_ACC, role="accent", cuisine="crossover",
                     ingredients={"cheddar_block": 200},
                     pairs_with=["scramble"]),
    "scramble": _comp("scramble", P_MAIN, cuisine="crossover",
                      ingredients={"eggs": 600, "onion_yellow": 80}),
}

SET = model.Settings.from_raw(
    dict(days=7, active_min_budget=100, batch_time_factor=0.45,
         max_days_same_component=4, cook_days=[0, 4], shop_days=[0]),
    {"mode": "off"})

METHODS = {
    "picadillo": [
        dict(phase="prep", text="Dice the onion", station="prep",
             mode="active", duration_min=4, operation="dice"),
        dict(phase="prep", text="Dice the potatoes and the carrot",
             station="prep", mode="active", duration_min=8,
             operation="dice"),
        dict(phase="cook", text="Simmer until the potato is tender",
             station="stove", mode="passive", duration_min=20,
             operation="simmer"),
    ],
    "sugo": [
        dict(phase="prep", text="Dice the onion", station="prep",
             mode="active", duration_min=4, operation="dice"),
        dict(phase="cook", text="Brown the sausage", station="stove",
             mode="active", duration_min=8, operation="brown"),
        dict(phase="finish", text="Roast to finish", station="oven",
             mode="passive", duration_min=25, oven_temp_f=425,
             operation="roast"),
    ],
}

TECHNIQUES = {
    "dice": dict(name="Dice", one_line="Cut into even small cubes."),
    "simmer": dict(name="Simmer", one_line="Hold at a lazy bubble."),
    "brown": dict(name="Brown", one_line="Color it, don't gray it."),
    "roast": dict(name="Roast", one_line="High dry oven heat."),
}


def _sp_one_session(batches=None):
    batches = batches or {"picadillo": 2, "sugo": 1}
    return dict(
        sessions=[dict(index=0, start=0,
                       demand_g={c: 500 * b for c, b in batches.items()},
                       batches=dict(batches),
                       made_g={c: COMPS[c]["yield_g"] * b
                               for c, b in batches.items()},
                       minutes=60, thaw_notes=[], freezer_notes=[],
                       feeds=[])],
        batches=dict(batches), minutes=60, unattributed=[], freezer=[],
        leftover=[])


# =========================================================================== #
#  fragment loader — validation + graceful degradation
# =========================================================================== #
def _write_fragment(d, cid, steps, schema_version=0, component=None):
    doc = dict(schema_version=schema_version, component=component or cid,
               steps=steps)
    (d / f"{cid}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))


GOOD_STEP = dict(phase="prep", text="Dice the onion", station="prep",
                 mode="active", duration_min=4, operation="dice")


def test_loader_loads_valid_fragments(tmp_path):
    _write_fragment(tmp_path, "picadillo", [dict(GOOD_STEP)])
    loaded, warns = load_methods(tmp_path, known_components={"picadillo"},
                                 known_operations={"dice"})
    assert warns == []
    assert list(loaded) == ["picadillo"]
    assert loaded["picadillo"][0]["text"] == "Dice the onion"


def test_loader_skips_invalid_fragment_with_structured_warning(tmp_path):
    bad = dict(GOOD_STEP, station="microwave")        # bad enum
    _write_fragment(tmp_path, "picadillo", [bad])
    _write_fragment(tmp_path, "sugo", [dict(GOOD_STEP)])
    loaded, warns = load_methods(tmp_path,
                                 known_components={"picadillo", "sugo"},
                                 known_operations={"dice"})
    # graceful: the bad file is skipped (its component degrades to the
    # ingredient-list rendering); the good one still loads
    assert list(loaded) == ["sugo"]
    assert any(w["code"] == "invalid_method_fragment"
               and "microwave" in w["message"] for w in warns)


def test_loader_rejects_oven_step_without_temp_and_vice_versa(tmp_path):
    no_temp = dict(GOOD_STEP, station="oven")         # missing oven_temp_f
    _write_fragment(tmp_path, "picadillo", [no_temp])
    stray = dict(GOOD_STEP, oven_temp_f=300)          # temp off-oven
    _write_fragment(tmp_path, "sugo", [stray])
    loaded, warns = load_methods(tmp_path,
                                 known_components={"picadillo", "sugo"})
    assert loaded == {}
    assert len([w for w in warns
                if w["code"] == "invalid_method_fragment"]) == 2


def test_loader_enforces_phase_order_and_schema_version(tmp_path):
    backwards = [dict(GOOD_STEP, phase="cook", station="stove"),
                 dict(GOOD_STEP, phase="prep")]       # prep after cook
    _write_fragment(tmp_path, "picadillo", backwards)
    _write_fragment(tmp_path, "sugo", [dict(GOOD_STEP)], schema_version=9)
    loaded, warns = load_methods(tmp_path,
                                 known_components={"picadillo", "sugo"})
    assert loaded == {}
    assert len(warns) == 2


def test_loader_drops_unknown_operation_but_keeps_the_step(tmp_path):
    _write_fragment(tmp_path, "picadillo",
                    [dict(GOOD_STEP, operation="teleport")])
    loaded, warns = load_methods(tmp_path, known_components={"picadillo"},
                                 known_operations={"dice"})
    assert "operation" not in loaded["picadillo"][0]  # ref dropped
    assert loaded["picadillo"][0]["text"] == "Dice the onion"
    assert any(w["code"] == "unknown_technique" for w in warns)


def test_loader_silently_skips_fragments_outside_the_library(tmp_path):
    _write_fragment(tmp_path, "alien_dish", [dict(GOOD_STEP)])
    loaded, warns = load_methods(tmp_path, known_components={"picadillo"})
    assert loaded == {} and warns == []


def test_loader_component_filename_mismatch_is_invalid(tmp_path):
    _write_fragment(tmp_path, "picadillo", [dict(GOOD_STEP)],
                    component="sugo")
    loaded, warns = load_methods(tmp_path,
                                 known_components={"picadillo", "sugo"})
    assert loaded == {}
    assert any("does not match filename" in w["message"] for w in warns)


def test_load_techniques_missing_path_degrades():
    techs, warns = load_techniques(None)
    assert techs == {} and warns == []


# =========================================================================== #
#  ingredient identity — the merge key's second half
# =========================================================================== #
def test_step_ingredient_single_match():
    st = dict(text="Dice the onion")
    assert step_ingredient(st, COMPS["picadillo"]) == "onion_yellow"


def test_step_ingredient_multi_match_has_no_identity():
    st = dict(text="Dice the potatoes and the carrot")
    assert step_ingredient(st, COMPS["picadillo"]) is None


def test_step_ingredient_no_match_has_no_identity():
    st = dict(text="Season to taste and pull off the heat")
    assert step_ingredient(st, COMPS["picadillo"]) is None


# =========================================================================== #
#  shared-prep consolidation
# =========================================================================== #
def test_shared_prep_merges_same_operation_same_ingredient():
    batches = {"picadillo": 2, "sugo": 1}
    merged, keys = consolidate_shared_prep(batches, batches, COMPS, METHODS)
    assert len(merged) == 1
    m = merged[0]
    assert (m["operation"], m["ingredient"]) == ("dice", "onion_yellow")
    # batch-scaled: picadillo 150g x 2 + sugo 200g x 1
    assert m["total_g"] == 500
    assert [(p["component"], p["grams"]) for p in m["parts"]] == \
        [("picadillo", 300), ("sugo", 200)]
    # exactly the two "Dice the onion" steps merged — the multi-ingredient
    # dice step (no single identity) and the cook steps never merge
    assert keys == {("picadillo", 0), ("sugo", 0)}


def test_shared_prep_never_merges_distinct_ops_or_single_dish():
    # same ingredient, different operation -> no merge
    alt = {"picadillo": [dict(METHODS["picadillo"][0], operation="mince")],
           "sugo": [dict(METHODS["sugo"][0])]}
    merged, keys = consolidate_shared_prep(
        {"picadillo": 1, "sugo": 1}, {"picadillo": 1, "sugo": 1}, COMPS, alt)
    assert merged == [] and keys == set()
    # one dish alone -> stays in its block
    merged, keys = consolidate_shared_prep(
        {"picadillo": 1}, {"picadillo": 1}, COMPS, METHODS)
    assert merged == [] and keys == set()


def test_shared_prep_ignores_steps_without_operation_or_identity():
    anon = {"picadillo": [dict(phase="prep", text="Dice the onion",
                               station="prep", mode="active",
                               duration_min=4)],          # no operation
            "sugo": [dict(METHODS["sugo"][0])]}
    merged, _ = consolidate_shared_prep(
        {"picadillo": 1, "sugo": 1}, {"picadillo": 1, "sugo": 1}, COMPS,
        anon)
    assert merged == []


# =========================================================================== #
#  the compiled session script
# =========================================================================== #
def test_cook_script_merged_step_and_per_dish_pointers():
    out = render_cook_plan(COMPS, SET, _sp_one_session(), meta=META,
                           methods=METHODS, techniques=TECHNIQUES)
    assert "## Shared prep — consolidated across dishes" in out
    assert ("- [ ] Dice 500g onion_yellow — 300g picadillo, 200g sugo"
            in out)
    # the per-dish blocks point at the shared block instead of repeating it
    assert out.count("Dice the onion") == 2      # two struck-through refs
    assert "~~Dice the onion~~ — done in Shared prep above" in out


def test_cook_script_step_annotations_quantities_and_oven_temp():
    out = render_cook_plan(COMPS, SET, _sp_one_session(), meta=META,
                           methods=METHODS, techniques=TECHNIQUES)
    # multi-ingredient step: no quantity injection, annotated, checkbox
    assert "- [ ] Dice the potatoes and the carrot — _prep · active " \
           "~8 min · [dice]_" in out
    # passive stove step with technique ref — single-ingredient identity
    # ("potato") gets batch-scaled grams injected (300g x 2 batches)
    assert "- [ ] Simmer until the potato is tender (600g potato) — " \
           "_stove · passive ~20 min · [simmer]_" in out
    # oven step carries its temp
    assert "- [ ] Roast to finish — _oven 425°F · passive ~25 min · " \
           "[roast]_" in out
    # single-ingredient cook step gets batch-scaled grams injected
    assert "- [ ] Brown the sausage (500g sausage) — _stove · active " \
           "~8 min · [brown]_" in out


def test_cook_script_station_summary_buckets_oven_by_temp():
    line = station_summary({"picadillo": 2, "sugo": 1}, METHODS)
    assert "oven: 425°F 25 min — shareable buckets" in line
    assert "prep 16 min active" in line          # 4+8+4 minus merge-free sum
    assert "stove 8 min active + 20 min passive" in line


def test_cook_script_technique_glossary_footnotes():
    out = render_cook_plan(COMPS, SET, _sp_one_session(), meta=META,
                           methods=METHODS, techniques=TECHNIQUES)
    assert "## Techniques" in out
    assert "- **[dice]** Dice — Cut into even small cubes." in out
    assert "- **[roast]** Roast — High dry oven heat." in out
    # glossary only lists operations actually used
    glossary = technique_glossary_lines({"dice"}, TECHNIQUES)
    assert not any("roast" in l for l in glossary)


def test_cook_script_degrades_without_fragments():
    # components with no fragment keep the ingredient-list rendering — and
    # the historical strings survive (the M1.1 tests assert them verbatim)
    out = render_cook_plan(COMPS, SET, _sp_one_session(), meta=META)
    assert "picadillo × 2 batches" in out
    assert "- [ ] onion_yellow: 300g" in out
    assert "Shared prep" not in out and "Techniques" not in out


def test_cook_script_timeline_style_falls_back_loudly():
    st = model.Settings.from_raw(
        dict(days=7, active_min_budget=100, batch_time_factor=0.45,
             max_days_same_component=4, cook_days=[0, 4], shop_days=[0],
             cook_plan_style="timeline"),
        {"mode": "off"})
    out = render_cook_plan(COMPS, st, _sp_one_session(), meta=META,
                           methods=METHODS, techniques=TECHNIQUES)
    assert "timeline** is not compiled yet" in out
    assert "M1.12" in out


def test_cook_script_is_deterministic():
    a = render_cook_plan(COMPS, SET, _sp_one_session(), meta=META,
                         methods=METHODS, techniques=TECHNIQUES)
    b = render_cook_plan(COMPS, SET, _sp_one_session(), meta=META,
                         methods=METHODS, techniques=TECHNIQUES)
    assert a == b


def test_cook_plan_style_enum_validated():
    base = {"schema_version": 1,
            "people": {"p1": {"targets": {"protein": 100, "fat": 60,
                                          "carb": 200},
                              "tolerance": 0.05}},
            "settings": {"days": 7, "active_min_budget": 180,
                         "cook_days": [0], "max_days_same_component": 4,
                         "cook_plan_style": "microwave"}}
    issues = validate_people_doc(base)
    assert any(i.code == "bad_enum" and "cook_plan_style" in i.where
               and i.severity == "error" for i in issues)
    for ok in ("recipe", "timeline"):
        base["settings"]["cook_plan_style"] = ok
        issues = validate_people_doc(base)
        assert not [i for i in issues if i.severity == "error"], ok


# =========================================================================== #
#  portioning matrix — pure reshape of MealDay x session attribution
# =========================================================================== #
def _meal(slot, sm, items):
    return dict(slot=slot, serving_model=sm, items=items,
                macros={"kcal": 0, "protein": 0, "fat": 0, "carb": 0},
                target={}, flags=[])


def _mealday(meals):
    return dict(meals=meals, notes=[])


def _matrix_fixture():
    """Two people; ada has lunch(portioned)+dinner(family_style); bob has
    no meal layer. One session feeds picadillo on days 0 and 1, rice day 0."""
    weeks = {
        "ada": [{"picadillo": 300, "rice": 400},
                {"picadillo": 200}] + [{}] * 5,
        "bob": [{"picadillo": 150}] + [{}] * 6,
    }
    meals = {"ada": [
        _mealday([_meal("lunch", "portioned",
                        {"picadillo": 180, "rice": 250}),
                  _meal("dinner", "family_style",
                        {"picadillo": 120, "rice": 150})]),
        _mealday([_meal("lunch", "portioned", {"picadillo": 200}),
                  _meal("dinner", "family_style", {})]),
    ] + [_mealday([])] * 5}
    sp = dict(sessions=[dict(
        index=0, start=0, demand_g={"picadillo": 650, "rice": 400},
        batches={"picadillo": 2, "rice": 1},
        made_g={"picadillo": 1000, "rice": 1500}, minutes=30, thaw_notes=[],
        freezer_notes=[dict(component="picadillo", day=1, grams=200,
                            session=0, note="thaw")],
        feeds=[dict(component="picadillo", day=0, grams=450),
               dict(component="picadillo", day=1, grams=200),
               dict(component="rice", day=0, grams=400)])],
        batches={"picadillo": 2, "rice": 1}, minutes=30, unattributed=[],
        freezer=[], leftover=[])
    people = {"ada": {}, "bob": {}}
    return sp, weeks, people, meals


def test_portioning_matrix_reshape_matches_hand_computation():
    sp, weeks, people, meals = _matrix_fixture()
    matrix = build_portioning(sp, weeks, people, meals, COMPS)
    m = matrix[0]
    # portioned rows: ada's lunch on both fed days, exactly the dealt grams
    assert m["portioned"]["picadillo"] == [
        dict(person="ada", day=0, slot="lunch", grams=180,
             pack_at_thaw=False),
        dict(person="ada", day=1, slot="lunch", grams=200,
             pack_at_thaw=True),               # freezer-bridged day 1
    ]
    assert m["portioned"]["rice"] == [
        dict(person="ada", day=0, slot="lunch", grams=250,
             pack_at_thaw=False)]
    # family-style slots + the meal-free person pool into shared containers
    assert m["shared"]["picadillo"]["total_g"] == 120 + 150
    takers = m["shared"]["picadillo"]["takers"]
    assert dict(person="ada", day=0, slot="dinner", grams=120,
                pack_at_thaw=False) in takers
    assert dict(person="bob", day=0, slot=None, grams=150,
                pack_at_thaw=False) in takers
    assert m["shared"]["rice"]["total_g"] == 150


def test_portioning_matrix_inert_without_meal_structure():
    sp, weeks, people, _ = _matrix_fixture()
    assert build_portioning(sp, weeks, people, {}, COMPS) is None
    assert build_portioning(sp, weeks, people, None, COMPS) is None


def test_portion_and_pack_rendering():
    sp, weeks, people, meals = _matrix_fixture()
    matrix = build_portioning(sp, weeks, people, meals, COMPS)
    out = render_cook_plan(COMPS, SET, sp, meta=META, matrix=matrix)
    assert "## Portion & pack — session 0" in out
    assert "- [ ] ada · eat day 1 · lunch — 180g" in out
    assert ("- [ ] ada · eat day 2 · lunch — 200g — **PACK AT THAW** "
            "(freezer-bridged day: portion when it thaws, not on cook day)"
            in out)
    assert "family style: store **270g** in a shared container" in out
    assert "- bob · eat day 1 — takes 150g" in out


def test_portioning_matrix_grams_come_from_the_canonical_attribution():
    """The reshape consumes session_plan's OWN feeds rows (P10) — run the
    real session_plan on a tiny two-session week and check the join keys:
    every (component, day) in feeds lands in exactly one session."""
    comps = {"stew": _comp("stew", P_MAIN, keeps=3,
                           ingredients={"beef": 500})}
    ing = {"beef": dict(p=20.0, f=8.0, c=0.0, perishable=False,
                        keeps_days=99, pack_g=454, cost=6.0, tags=[])}
    weeks = {"p": [{"stew": 100}, {"stew": 100}, {}, {},
                   {"stew": 100}, {"stew": 100}, {}]}
    sp = session_plan(comps, ing, SET, weeks)
    fed = {(r["component"], r["day"]): s["index"]
           for s in sp["sessions"] for r in s["feeds"]}
    assert fed == {("stew", 0): 0, ("stew", 1): 0,
                   ("stew", 4): 1, ("stew", 5): 1}
    assert all(r["grams"] == 100
               for s in sp["sessions"] for r in s["feeds"])


# =========================================================================== #
#  eat-sheet phrasing per serving model
# =========================================================================== #
PRECISE = dict(name="ada", targets={"protein": 150, "fat": 60, "carb": 300},
               tolerance=0.05, mode="precision", exclude=[], dislikes=[])


def test_eat_sheet_portioned_slot_packed_plate_voice():
    week = [{"picadillo": 300, "rice": 250}]
    md = [_mealday([_meal("lunch", "portioned",
                          {"picadillo": 300, "rice": 250})])]
    out = render_eat_sheet("ada", PRECISE, COMPS, week, SET, list(COMPS),
                           ing=None, meta=META, meal_days=md)
    assert "### lunch — portioned" in out
    assert ("- packed container: picadillo **300g** + rice **250g**"
            in out)


def test_eat_sheet_family_style_attaches_accents_to_their_base():
    week = [{"scramble": 300, "cheddar": 40, "rice": 200}]
    md = [_mealday([_meal("breakfast", "family_style",
                          {"scramble": 300, "cheddar": 40, "rice": 200})])]
    out = render_eat_sheet("ada", PRECISE, COMPS, week, SET, list(COMPS),
                           ing=None, meta=META, meal_days=md)
    assert "### breakfast — family style" in out
    # accent ATTACHED to its pairs_with base, never its own line
    assert ("- scramble (shared batch) — take **300g** + **40g** cheddar "
            "on top" in out)
    assert "- cheddar:" not in out
    assert "- rice (shared batch) — take **200g**" in out


def test_eat_sheet_accent_falls_back_to_cuisine_match():
    comps = dict(COMPS)
    comps["crema"] = _comp("crema", P_ACC, role="accent", cuisine="mexican",
                           ingredients={"cream": 100})
    week = [{"picadillo": 300, "sugo": 200, "crema": 30}]
    md = [_mealday([_meal("dinner", "family_style",
                          {"picadillo": 300, "sugo": 200, "crema": 30})])]
    out = render_eat_sheet("ada", PRECISE, comps, week, SET, list(comps),
                           ing=None, meta=META, meal_days=md)
    # crema (mexican) lands on picadillo (mexican), not sugo (italian)
    assert ("- picadillo (shared batch) — take **300g** + **30g** crema "
            "on top" in out)


def test_eat_sheet_day_pool_family_style_person():
    person = dict(PRECISE, serving_model="family_style")
    week = [{"picadillo": 300, "rice": 250}]
    out = render_eat_sheet("ada", person, COMPS, week, SET, list(COMPS),
                           ing=None, meta=META)
    assert "- picadillo (shared batch) — take **300g**" in out
    assert "- rice (shared batch) — take **250g**" in out
    # a portioned (default) person without meal structure keeps the flat
    # legacy listing
    out2 = render_eat_sheet("ada", PRECISE, COMPS, week, SET, list(COMPS),
                            ing=None, meta=META)
    assert "- picadillo: **300g**" in out2


def test_eat_sheet_family_style_relaxed_units_and_error_bars_still_apply():
    person = dict(name="rex", targets={"protein": 150, "fat": 60,
                                       "carb": 300},
                  tolerance=0.12, mode="relaxed", exclude=[], dislikes=[],
                  serving_model="family_style")
    week = [{"rice": 340, "picadillo": 250}]
    out = render_eat_sheet("rex", person, COMPS, week, SET, list(COMPS),
                           ing=None, meta=META)
    assert "take **about 2 cups** (~370g)" in out
    assert "If you eyeball it:" in out


def test_attach_accents_orphan_accent_stands_alone():
    rows, orphans = attach_accents({"cheddar": 40}, COMPS)
    assert rows == [] and orphans == [("cheddar", 40)]


def test_cook_script_lines_shared_by_plan_report():
    """One renderer, two outputs: cli.render's cook section is the same
    cook_script_lines output at h='###'."""
    lines, _ = cook_script_lines(COMPS, SET, _sp_one_session(),
                                 methods=METHODS, techniques=TECHNIQUES,
                                 h="###")
    assert any(l.startswith("### Session 0 — cook day 0") for l in lines)
    assert any(l.startswith("#### Shared prep") for l in lines)
