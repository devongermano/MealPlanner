"""M0.5 dead-config regression tests: wire or delete.

Every schema field must be consumed or explicitly reserved after M0.5:

- --force: CLI passes must=[...] to choose_menu; unknown ids are a CLI error.
- min_lean_anchors: score_menu reads settings.min_lean_anchors (default 2)
  instead of a hardcoded >= 2.
- meals_per_day: KEPT, validated (int >= 1), explicitly RESERVED — the M0
  engine ignores it by design (PRD §8.1, M1 eat sheets).
- min_components_per_day / max_components_per_day / batch_g: DELETED from the
  schema; validation errors with a migration message.
- freezes: LIVE (PRD §8.1/§8.2) — freezer-bridging availability under
  settings.use_freezer (default true), with "from freezer — thaw ahead"
  markings in week/cook output.
- Gate: registry test — every schema field is referenced by consuming code or
  listed in model.RESERVED_FIELDS.
"""

import inspect
from dataclasses import fields as dc_fields
from pathlib import Path

import pytest

from mealplan import cli, costing, engine, io_yaml, model
from mealplan.costing import session_plan
from mealplan.io_yaml import validate_components_doc, validate_people_doc

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"


# --------------------------------------------------------------------------- #
#  synthetic fixtures — plain dicts, engine reads them dict-style
# --------------------------------------------------------------------------- #
def _comp(cid, ingredients, yield_g=500, keeps=5, active=30, role="main",
          per100=None, serve=(100, 400), freezes=None):
    d = dict(id=cid, name=cid, cuisine="test", role=role, yield_g=yield_g,
             serve_g={"min": serve[0], "max": serve[1]}, keeps_days=keeps,
             active_min=active, ingredients=ingredients,
             per100=per100 or {"kcal": 180.0, "protein": 15.0,
                               "fat": 8.0, "carb": 10.0},
             tags=[])
    if freezes is not None:
        d["freezes"] = freezes
    return d


def _ing(perishable=True, keeps=5, freezable=False, pack_g=500, cost=5.0):
    d = dict(p=20.0, f=8.0, c=2.0, perishable=perishable, keeps_days=keeps,
             pack_g=pack_g, cost=cost, tags=[])
    if freezable:
        d["freezable"] = True
    return d


# --------------------------------------------------------------------------- #
#  --force wiring (CLI -> choose_menu must=[...])
# --------------------------------------------------------------------------- #
def _menu_ids(out):
    ids = []
    for line in out.splitlines():
        if not line.strip():
            break
        ids.append(line.split()[0])
    return ids


@pytest.mark.slow
def test_cli_force_puts_dishes_on_the_menu(capsys):
    """--force must put entries on the menu that the free search did not
    choose. Self-adapting (PRD §9: the founder corpus is live data, not a
    fixture). M1.13: examples/ is a DISH library now — the menu is dish
    ids, so the forced ids are dishes the free search left out (--force
    with a component id maps through dish membership; that path is
    unit-tested in test_dishes.py). Slow: dish-menu search + LP verify."""
    cli.main(["menu", "--library", str(EXAMPLES), "--seed", "0", "--n", "8"])
    baseline = _menu_ids(capsys.readouterr().out)
    dishes = io_yaml.load_dishes(EXAMPLES / "dishes.yaml")
    left_out = sorted(set(dishes) - set(baseline))[:2]
    assert len(left_out) == 2, \
        "fixture assumption broke: the free menu selected every dish"

    cli.main(["menu", "--library", str(EXAMPLES), "--seed", "0", "--n", "8",
              "--force", ",".join(left_out)])
    forced = _menu_ids(capsys.readouterr().out)
    for did in left_out:
        assert did in forced, (did, forced)


def test_cli_force_unknown_component_is_an_error_naming_it():
    with pytest.raises(SystemExit) as e:
        cli.main(["menu", "--library", str(EXAMPLES), "--seed", "0",
                  "--force", "no_such_dish"])
    assert "no_such_dish" in str(e.value)


# --------------------------------------------------------------------------- #
#  min_lean_anchors — read from settings (default 2), not hardcoded
# --------------------------------------------------------------------------- #
def _lean_lib():
    """Two lean mains (protein/fat ratio far above need_ratio * 1.25), one
    fatty main, plus enough structure that only the min_lean_anchors branch
    varies across settings."""
    per_lean = {"kcal": 120.0, "protein": 25.0, "fat": 2.0, "carb": 1.0}
    per_fat = {"kcal": 200.0, "protein": 10.0, "fat": 15.0, "carb": 2.0}
    per_starch = {"kcal": 130.0, "protein": 3.0, "fat": 1.0, "carb": 28.0}
    per_acc = {"kcal": 80.0, "protein": 2.0, "fat": 5.0, "carb": 6.0}
    comps = {
        "lean1": _comp("lean1", {"meat": 500}, keeps=5, per100=per_lean),
        "lean2": _comp("lean2", {"meat": 500}, keeps=5, per100=per_lean),
        "fatty": _comp("fatty", {"meat": 500}, keeps=5, per100=per_fat),
        "st1": _comp("st1", {"grain": 300}, role="starch", per100=per_starch),
        "st2": _comp("st2", {"grain": 300}, role="starch", per100=per_starch),
        "st3": _comp("st3", {"grain": 300}, role="starch", per100=per_starch),
        "ac1": _comp("ac1", {"grain": 100}, role="accent", per100=per_acc),
        "ac2": _comp("ac2", {"grain": 100}, role="accent", per100=per_acc),
    }
    ing = {"meat": _ing(perishable=True, keeps=5),
           "grain": _ing(perishable=False, keeps=999)}
    people = {"p1": dict(name="p1",
                         targets={"protein": 100, "fat": 50, "carb": 200},
                         tolerance=0.2, exclude=[], dislikes=[])}
    return comps, ing, people


def _score_with(min_lean):
    comps, ing, people = _lean_lib()
    st = dict(days=7, active_min_budget=600, batch_time_factor=0.5,
              max_days_same_component=4, cook_days=[0, 3], shop_days=[0])
    if min_lean is not None:
        st["min_lean_anchors"] = min_lean
    # M0.17: defaults live in the model layer — the engine reads validated
    # settings by plain indexing, so hand-built dicts go through from_raw.
    settings = model.Settings.from_raw(st, {"mode": "off"})
    s, _ = engine.score_menu(comps, ing, list(comps), settings, people)
    return s


def test_min_lean_anchors_setting_changes_menu_score():
    """Two lean mains on the menu: setting 1 or 2 is satisfied, 3 is not —
    the 15000 lean-anchor penalty must appear exactly when the setting says."""
    s1, s2, s3 = _score_with(1), _score_with(2), _score_with(3)
    assert s1 == s2
    assert s3 - s2 == 15000, (s1, s2, s3)


def test_min_lean_anchors_default_is_two():
    """Absent setting behaves exactly like the effective prototype behavior:
    the hardcoded >= 2. Since M0.17 the default is applied by the MODEL
    layer (model.SETTINGS_DEFAULTS via Settings.from_raw), not inline in
    the engine."""
    assert model.SETTINGS_DEFAULTS["min_lean_anchors"] == 2
    assert _score_with(None) == _score_with(2)


# --------------------------------------------------------------------------- #
#  meals_per_day — LIVE since M1.9 (meals.deal_day); still validated int >= 1
# --------------------------------------------------------------------------- #
def _ppl_doc(person_extra=None, settings_extra=None):
    p = {"targets": {"protein": 100, "fat": 60, "carb": 200},
         "tolerance": 0.05}
    p.update(person_extra or {})
    st = {"days": 7, "active_min_budget": 180, "cook_days": [0],
          "max_days_same_component": 4}
    st.update(settings_extra or {})
    return {"schema_version": 1, "people": {"p1": p}, "settings": st}


@pytest.mark.parametrize("bad", [0, -1, 2.5, True, "two"])
def test_meals_per_day_must_be_int_ge_1(bad):
    issues = validate_people_doc(_ppl_doc({"meals_per_day": bad}))
    assert any(i.code == "bad_meals_per_day" and i.severity == "error"
               for i in issues), bad


def test_meals_per_day_valid_and_absent_are_fine():
    for extra in ({"meals_per_day": 2}, {}):
        issues = validate_people_doc(_ppl_doc(extra))
        assert not [i for i in issues if i.severity == "error"], extra


def test_reserved_set_is_exactly_the_documented_one():
    """period (Budget, weekly-only in M0/M1) — reserved WITH a rationale in
    model.py. cooked (Pantry) left the set when M1.8 made it live;
    meals_per_day (Person) left it when M1.9's meal dealer made it live.
    Growing this set is a deliberate act."""
    assert model.RESERVED_FIELDS == {"period"}


# --------------------------------------------------------------------------- #
#  deleted fields — validation errors with a migration message
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["min_components_per_day",
                                   "max_components_per_day"])
def test_person_component_count_fields_are_removed(field):
    issues = validate_people_doc(_ppl_doc({field: 3}))
    hits = [i for i in issues
            if i.code == "removed_field" and field in i.where]
    assert hits and hits[0].severity == "error"
    assert "removed in schema v1" in hits[0].message


def test_component_batch_g_is_removed():
    doc = {"schema_version": 1, "components": [
        {"id": "stew", "name": "stew", "cuisine": "test", "role": "main",
         "yield_g": 500, "serve_g": {"min": 100, "max": 400},
         "keeps_days": 5, "active_min": 30, "ingredients": {"beef": 500},
         "batch_g": 100}]}
    issues = validate_components_doc(doc, known_ingredients={"beef"})
    hits = [i for i in issues
            if i.code == "removed_field" and "batch_g" in i.where]
    assert hits and hits[0].severity == "error"
    assert "removed in schema v1" in hits[0].message


def test_examples_corpus_carries_no_removed_fields():
    ing, comps, people, settings = io_yaml.load(EXAMPLES)
    for pname, p in people.items():
        assert "min_components_per_day" not in p.raw, pname
        assert "max_components_per_day" not in p.raw, pname
    for cid, c in comps.items():
        assert "batch_g" not in c.raw, cid
    # the setting is live and sane on the corpus (its exact value is corpus
    # data, free to change — the default-of-2 behavior is pinned by
    # test_min_lean_anchors_default_is_two on synthetic fixtures)
    mla = settings["min_lean_anchors"]
    assert isinstance(mla, int) and mla >= 1


# --------------------------------------------------------------------------- #
#  freezes — LIVE: freezer-bridging availability (PRD §8.1/§8.2)
# --------------------------------------------------------------------------- #
# Full validated-settings shape (M0.17): the engine reads settings by plain
# indexing — hand-built dicts must carry every field the model layer would
# have defaulted.
VALLEY_SETTINGS = dict(days=7, active_min_budget=180, batch_time_factor=0.5,
                       max_days_same_component=4, cook_days=[0, 3],
                       shop_days=[0], min_lean_anchors=2,
                       max_batches_per_component=3, use_freezer=True,
                       budget={"mode": "off"})


def _valley_lib(freezes):
    """cook_days [0,3], component keeps 3 cooked: fresh coverage is days 0-2
    (session 0) and 3-5 (session 1). Day 6 is the valley."""
    comps = {"valley": _comp("valley", {"grain": 500}, yield_g=500, keeps=3,
                             freezes=freezes,
                             per100={"kcal": 160.0, "protein": 18.0,
                                     "fat": 4.0, "carb": 12.0}),
             "pilaf": _comp("pilaf", {"grain": 300}, yield_g=600, keeps=7,
                            role="starch",
                            per100={"kcal": 190.0, "protein": 5.0,
                                    "fat": 2.0, "carb": 38.0})}
    ing = {"grain": _ing(perishable=False, keeps=999)}
    return comps, ing


def test_freezes_true_bridges_the_day6_valley():
    comps, ing = _valley_lib(freezes=True)
    v = comps["valley"]
    assert engine.available_on(v, 6, VALLEY_SETTINGS, ing=ing)
    assert engine.from_freezer(v, 6, VALLEY_SETTINGS, ing=ing)
    # days a fresh batch still covers are NOT freezer days
    assert engine.available_on(v, 4, VALLEY_SETTINGS, ing=ing)
    assert not engine.from_freezer(v, 4, VALLEY_SETTINGS, ing=ing)


def test_freezes_false_preserves_the_hole():
    comps, ing = _valley_lib(freezes=False)
    assert not engine.available_on(comps["valley"], 6, VALLEY_SETTINGS,
                                   ing=ing)
    comps2, _ = _valley_lib(freezes=None)      # absent field == false
    assert not engine.available_on(comps2["valley"], 6, VALLEY_SETTINGS,
                                   ing=ing)


def test_use_freezer_false_disables_bridging():
    comps, ing = _valley_lib(freezes=True)
    st = dict(VALLEY_SETTINGS, use_freezer=False)
    assert not engine.available_on(comps["valley"], 6, st, ing=ing)
    assert not engine.from_freezer(comps["valley"], 6, st, ing=ing)


def test_session_plan_attributes_valley_demand_via_freezer():
    comps, ing = _valley_lib(freezes=True)
    weeks = {"p1": [{}, {}, {}, {}, {}, {}, {"valley": 300}]}
    sp = session_plan(comps, ing, VALLEY_SETTINGS, weeks)
    assert sp["unattributed"] == []
    assert sp["batches"] == {"valley": 1}
    notes = [n for s in sp["sessions"] for n in s["freezer_notes"]]
    assert any(n["component"] == "valley" and n["day"] == 6 and
               "from freezer — thaw ahead" in n["note"] for n in notes), notes


def test_session_plan_freezes_false_reports_valley_demand():
    comps, ing = _valley_lib(freezes=False)
    weeks = {"p1": [{}, {}, {}, {}, {}, {}, {"valley": 300}]}
    sp = session_plan(comps, ing, VALLEY_SETTINGS, weeks)
    assert sp["batches"].get("valley") is None
    assert sp["unattributed"] == [dict(component="valley", day=6, grams=300)]
    for s in sp["sessions"]:
        assert s["freezer_notes"] == []


def _render(comps, ing, weeks):
    people = {"p1": dict(name="p1",
                         targets={"protein": 60, "fat": 20, "carb": 120},
                         tolerance=0.5, exclude=[], dislikes=[])}
    sp = session_plan(comps, ing, VALLEY_SETTINGS, weeks)
    menuinfo = dict(cuisines=1, active_min=sp["minutes"], waste_perishable=0,
                    roles={})
    return cli.render(comps, ing, people, VALLEY_SETTINGS, list(comps), weeks,
                      {}, "", menuinfo, sp)


def test_week_and_cook_output_mark_freezer_days():
    comps, ing = _valley_lib(freezes=True)
    weeks = {"p1": [{"valley": 300, "pilaf": 200}, {}, {}, {}, {}, {},
                    {"valley": 300, "pilaf": 200}]}
    out = _render(comps, ing, weeks)
    day7 = out.split("**Day 7**")[1]
    assert "from freezer — thaw ahead" in day7
    day1 = out.split("**Day 1**")[1].split("**Day")[0]
    assert "from freezer" not in day1
    cook = out.split("## Cook plan")[1].split("## Custom foods")[0]
    assert "from freezer — thaw ahead" in cook


def test_week_output_keeps_the_explained_hole_when_freezes_false():
    comps, ing = _valley_lib(freezes=False)
    weeks = {"p1": [{"valley": 300, "pilaf": 200}, {}, {}, {}, {}, {}, {}]}
    out = _render(comps, ing, weeks)
    day7 = out.split("**Day 7**")[1]
    assert "NO FEASIBLE PLATE" in day7
    assert "valley" in day7               # named as past shelf life


def test_use_freezer_must_be_bool():
    issues = validate_people_doc(_ppl_doc(
        settings_extra={"use_freezer": "yes"}))
    assert any(i.code == "bad_use_freezer" and i.severity == "error"
               for i in issues)
    issues = validate_people_doc(_ppl_doc(settings_extra={"use_freezer": False}))
    assert not [i for i in issues if i.severity == "error"]


# --------------------------------------------------------------------------- #
#  the M0 gate — no dead config, ever again
# --------------------------------------------------------------------------- #
# Consuming code: the engine-side modules plus the derivation (per100/tags/
# edible-fraction consumption lives in model.derive_component). M1.0: the
# source is stripped of comments and docstrings first — a field named only
# in prose is documentation, not consumption.
from _shared import strip_comments_and_docstrings


def _engine_side_source():
    # artifacts joined the consuming surfaces in M1.1/M1.2: the eat-sheet
    # renderer consumes Person.mode and Component.household_unit (rendering
    # is consumption — the field changes what the user sees). meals joined
    # in M1.9: the dealer consumes meals_per_day / serving_model /
    # meal_slots / pairs_with (via model.resolve_meal_slots, also included).
    # dishes joined in M1.13: the dish layer consumes Dish.* plus
    # Settings.dish_layer and Person.max_dishes_per_slot.
    # schedule joined in M1.12: the timeline compiler consumes
    # Settings.stations (station capacities) and batch_time_factor.
    from mealplan import artifacts, dishes, meals, schedule
    srcs = [inspect.getsource(m)
            for m in (engine, costing, cli, artifacts, meals, dishes,
                      schedule)]
    srcs.append(inspect.getsource(model.derive_component))
    srcs.append(inspect.getsource(model.resolve_meal_slots))
    return "".join(strip_comments_and_docstrings(s) for s in srcs)


# Fields whose consumption IS validation behavior (they change what io_yaml
# accepts/warns, and nothing else — e.g. 'negligible' silences the
# all-zero-macros warning). Kept explicit so validation alone can never
# accidentally launder a genuinely dead field.
VALIDATION_CONSUMED = {"negligible"}


def _referenced(name, src):
    return f'"{name}"' in src or f"'{name}'" in src


def test_no_dead_config_gate():
    """Every schema field on EVERY schema dataclass — Ingredient, Component,
    Person, Settings, Budget, Pantry — must be referenced by
    engine/costing/cli code (or the derivation), be an explicitly
    validation-consumed field, or sit in model.RESERVED_FIELDS. Future dead
    config fails here. (Budget and Pantry were originally outside this loop,
    which let Budget.period ride along dead — the exact hole the gate
    exists to close. Since M1.0 the reference check runs on comment- and
    docstring-stripped source, so prose can never launder a dead field.)"""
    engine_src = _engine_side_source()
    validation_src = strip_comments_and_docstrings(
        inspect.getsource(io_yaml))
    assert model.RESERVED_FIELDS == {"period"}

    for cls in (model.Ingredient, model.Component, model.Person,
                model.Settings, model.Budget, model.Pantry, model.Dish):
        for f in dc_fields(cls):
            name = f.name
            if name == "raw":
                continue          # implementation detail, not schema
            if name in model.RESERVED_FIELDS:
                # reserved = engine ignores it BY DESIGN; it must not be
                # silently consumed anywhere engine-side
                assert not _referenced(name, engine_src), \
                    f"{cls.__name__}.{name} is RESERVED but engine-side " \
                    f"code references it"
                continue
            if name in VALIDATION_CONSUMED:
                assert _referenced(name, validation_src), \
                    f"{cls.__name__}.{name} claims validation consumption " \
                    f"but io_yaml never references it"
                continue
            assert _referenced(name, engine_src), \
                f"dead config: {cls.__name__}.{name} is in the schema but " \
                f"no engine/costing/cli code consumes it — wire it, delete " \
                f"it, or add it to model.RESERVED_FIELDS"
