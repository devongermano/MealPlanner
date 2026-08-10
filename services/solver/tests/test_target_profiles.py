"""M1.11 target-profile FAST-tier tests (M111_SPEC §11) — pure model
resolution + all-errors validation + the pre-solve CLI date gate. Zero CBC
solves anywhere in this module by construction (the LP-bearing e2e lives in
test_targets_e2e.py, SLOW-tier).

The load-bearing claims:
- resolution is ONE function (model.resolve_targets, P10) and its inert
  path is OBJECT IDENTITY — the proof the no-profile pipeline cannot
  drift by a byte (M111_SPEC §8 links 1-2);
- anchor math is calendar-checked against datetime.date arithmetic (an
  independent computation — kills off-by-one/mod mutants);
- validation reports every rule violation in one pass, and the extracted
  ``_macro_map_issues`` helper is refactor-pure for legacy targets docs.
"""

import datetime
import json
from pathlib import Path

import pytest

from mealplan import cli, engine, io_yaml, model
from mealplan.io_yaml import validate_people_doc
from mealplan.model import (DAY_KEYS, Person, distinct_day_types,
                            person_for_day, resolve_targets, week_day_key,
                            week_day_label, weekly_targets)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

BASE = {"protein": 150, "fat": 65, "carb": 250}
LIFT = {"protein": 190, "fat": 60, "carb": 280}
WKND = {"protein": 110, "fat": 75, "carb": 180}
WEEK = {"mon": "lift", "wed": "lift", "fri": "lift",
        "sat": "weekend", "sun": "weekend"}


def _person(profiles=True, week=None):
    d = dict(targets=dict(BASE), tolerance=0.06, exclude=[], dislikes=[])
    if profiles:
        d["target_profiles"] = {"lift": dict(LIFT), "weekend": dict(WKND)}
        d["week"] = dict(WEEK if week is None else week)
    return Person.from_raw("sol", d)


# --------------------------------------------------------------------------- #
#  T1 — inertness links 1-2: identity, not equality
# --------------------------------------------------------------------------- #
def test_resolve_no_profiles_is_identity():
    for p in (_person(profiles=False),
              dict(targets=dict(BASE), tolerance=0.06)):
        assert resolve_targets(p) is p["targets"]
        assert resolve_targets(p, 3, 2) is p["targets"]      # anchor ignored
        assert person_for_day(p) is p
        assert person_for_day(p, 5, 4) is p                  # SAME object


def test_base_fallback_day_view_is_identity_too():
    """A profiled person's BASE day (tue with anchor 0) resolves to the
    base map by identity, so the day-view person is the same object —
    zero churn on the days the profiles don't touch."""
    p = _person()
    assert resolve_targets(p, 1, 0) is p["targets"]          # tue -> base
    assert person_for_day(p, 1, 0) is p


# --------------------------------------------------------------------------- #
#  T2 — anchor math, all 7 starts x 7 days, calendar-checked
# --------------------------------------------------------------------------- #
def test_resolve_anchor_math_all_starts():
    """Expected weekday computed INDEPENDENTLY via datetime.date arithmetic
    (2026-08-10 is a Monday), never via the same (anchor+d)%7 expression —
    an off-by-one or mod mutant in week_day_key cannot satisfy both."""
    monday = datetime.date(2026, 8, 10)
    assert monday.weekday() == 0
    # 7 distinct profiles, one per weekday — every day is sign-testable
    profs = {f"t_{k}": {"protein": 100 + i, "fat": 50, "carb": 200}
             for i, k in enumerate(DAY_KEYS)}
    p = Person.from_raw("x", dict(
        targets=dict(BASE), tolerance=0.05,
        target_profiles=profs, week={k: f"t_{k}" for k in DAY_KEYS}))
    for anchor in range(7):
        start = monday + datetime.timedelta(days=anchor)
        assert start.weekday() == anchor
        for d in range(7):
            day_date = start + datetime.timedelta(days=d)
            expected_key = DAY_KEYS[day_date.weekday()]
            assert week_day_key(d, anchor) == expected_key, (anchor, d)
            assert week_day_label(p, d, anchor) == f"t_{expected_key}"
            assert resolve_targets(p, d, anchor) \
                is profs[f"t_{expected_key}"]


# --------------------------------------------------------------------------- #
#  T3 — partial week map: absent weekday -> base, listed -> profile
# --------------------------------------------------------------------------- #
def test_partial_week_falls_back_to_base():
    p = _person()
    # anchor 0 (Monday start): d0 mon=lift, d1 tue=base, d5 sat=weekend
    assert resolve_targets(p, 0, 0) is p["target_profiles"]["lift"]
    assert resolve_targets(p, 1, 0) is p["targets"]
    assert resolve_targets(p, 5, 0) is p["target_profiles"]["weekend"]


# --------------------------------------------------------------------------- #
#  T4 — engine-side bug guard
# --------------------------------------------------------------------------- #
def test_profiles_without_anchor_raises():
    p = _person()
    with pytest.raises(ValueError):
        resolve_targets(p)
    with pytest.raises(ValueError):
        resolve_targets(p, 3, None)
    with pytest.raises(ValueError):
        resolve_targets(p, None, 0)
    with pytest.raises(ValueError):
        person_for_day(p, None, None)
    with pytest.raises(ValueError):
        weekly_targets(p, 7, None)


# --------------------------------------------------------------------------- #
#  T5 — day-view person swaps targets ONLY; input unmutated
# --------------------------------------------------------------------------- #
def test_person_for_day_swaps_targets_only():
    raw = dict(targets=dict(BASE), tolerance=0.06, exclude=["dairy"],
               dislikes=["herb_sauce"], meals_per_day=3,
               max_daily_mass_g=2200,
               target_profiles={"lift": dict(LIFT), "weekend": dict(WKND)},
               week=dict(WEEK))
    p = Person.from_raw("sol", raw)
    before = dict(p.raw)
    view = person_for_day(p, 0, 0)                    # mon -> lift
    assert view is not p
    assert isinstance(view, Person)
    assert view["targets"] is p["target_profiles"]["lift"]
    # everything EXCEPT targets is preserved
    for k in before:
        if k != "targets":
            assert view.raw[k] == before[k], k
    # the input person was not mutated
    assert p.raw == before
    assert p["targets"] == BASE
    # plain-mapping inputs get a dict copy
    d = dict(targets=dict(BASE), tolerance=0.06,
             target_profiles={"lift": dict(LIFT), "weekend": dict(WKND)},
             week=dict(WEEK))
    v2 = person_for_day(d, 0, 0)
    assert v2 is not d and isinstance(v2, dict)
    assert v2["targets"] is d["target_profiles"]["lift"]
    assert d["targets"] == BASE


# --------------------------------------------------------------------------- #
#  T6 — emergent weekly sum
# --------------------------------------------------------------------------- #
def test_weekly_targets_sum():
    p = _person()
    wt = weekly_targets(p, 7, 0)
    expected = {m: 0.0 for m in ("protein", "fat", "carb")}
    for d in range(7):
        t = resolve_targets(p, d, 0)
        for m in expected:
            expected[m] += t[m]
    assert wt == expected
    # anchor 0: 3 lift + 2 base + 2 weekend
    assert wt["protein"] == 3 * 190 + 2 * 150 + 2 * 110
    # no-profile person: days x daily, no anchor needed
    flat = _person(profiles=False)
    assert weekly_targets(flat, 7) == {m: 7 * BASE[m] for m in BASE}
    # days != 7: resolution is mod-7, the sum covers the actual plan days
    assert weekly_targets(flat, 3) == {m: 3 * BASE[m] for m in BASE}


# --------------------------------------------------------------------------- #
#  T7 — label None on base days (drives the §9 rendering branch)
# --------------------------------------------------------------------------- #
def test_week_day_label_none_for_base():
    p = _person()
    assert week_day_label(p, 0, 0) == "lift"
    assert week_day_label(p, 1, 0) is None            # tue: base
    assert week_day_label(p, 3, 0) is None            # thu: base
    flat = _person(profiles=False)
    assert week_day_label(flat, 0, 0) is None
    assert week_day_label(flat, 0, None) is None      # inert path: no anchor


# --------------------------------------------------------------------------- #
#  T8 — serve-bound scaling turns day-correct for free (named §4 consequence)
# --------------------------------------------------------------------------- #
def test_person_scale_day_correct(monkeypatch):
    """The SCALING mechanism is DORMANT (identity clamps — the lard-beans
    revocation), so this test re-arms it the way test_m1_phase1 does to
    prove the day-view injection reaches person_scale: a lift day and a
    weekend day scale differently once the clamps allow it."""
    monkeypatch.setitem(engine.SCALING, "scale_min", 0.5)
    monkeypatch.setitem(engine.SCALING, "scale_max", 2.0)
    p = _person()
    s_lift = engine.person_scale(person_for_day(p, 0, 0))     # mon: lift
    s_wknd = engine.person_scale(person_for_day(p, 5, 0))     # sat: weekend
    from mealplan.units import kcal_of
    assert s_lift != s_wknd
    assert s_lift == kcal_of(LIFT) / engine.SCALING["reference_kcal"]
    assert s_wknd == kcal_of(WKND) / engine.SCALING["reference_kcal"]


# --------------------------------------------------------------------------- #
#  distinct_day_types (§7 driver)
# --------------------------------------------------------------------------- #
def test_distinct_day_types_shapes():
    p = _person()
    types = distinct_day_types(p)
    assert [(l, d) for l, _, d in types] == [
        ("base", ("tue", "thu")),
        ("lift", ("mon", "wed", "fri")),
        ("weekend", ("sat", "sun"))]
    assert types[0][1] is p["targets"]                 # base by identity
    assert types[1][1] is p["target_profiles"]["lift"]
    # full-coverage week map: NO base entry
    full = Person.from_raw("x", dict(
        targets=dict(BASE), tolerance=0.05,
        target_profiles={"a": dict(LIFT), "b": dict(WKND)},
        week={k: ("a" if i < 4 else "b")
              for i, k in enumerate(DAY_KEYS)}))
    assert [l for l, _, _ in distinct_day_types(full)] == ["a", "b"]
    # no profiles: exactly one 'base' type covering all seven weekdays
    flat = _person(profiles=False)
    assert distinct_day_types(flat) == [("base", flat["targets"], DAY_KEYS)]


def test_day_type_views_never_short_circuits_a_profiled_person():
    """Regression (M111_SPEC §7 + §12 P-1): a FULL 7-day week map naming
    exactly ONE profile is validation-clean and §13/P-3 expects authors to
    write it — and it yields a single distinct day-type whose targets are
    the PROFILE, not base. Gating the inert short-circuit on
    ``len(types) == 1`` would hand the doctor the person object (base
    targets, which no plan day ever eats) and skip every feasibility /
    binding-macro / volume-floor / carb-headroom check of the only
    day-type the household actually eats — silently removing the safety
    net P-1 rests on. The gate is profile ABSENCE, which is also why a
    profile *named* "base" is still diagnosed as a profile."""
    full = Person.from_raw("sol", dict(
        targets=dict(BASE), tolerance=0.06,
        target_profiles={"cut": dict(WKND)},
        week={k: "cut" for k in DAY_KEYS}))
    views = engine._day_type_views(full)
    assert len(views) == 1
    suffix, pv = views[0]
    assert suffix == " — day-type 'cut' (mon, tue, wed, thu, fri, sat, sun)"
    assert pv is not full
    assert pv["targets"] is full["target_profiles"]["cut"]
    assert pv["tolerance"] == full["tolerance"]
    assert full["targets"] == BASE                    # input unmutated
    # a profile NAMED "base" is a profile, never the inert branch
    shadow = Person.from_raw("sol", dict(
        targets=dict(BASE), tolerance=0.06,
        target_profiles={"base": dict(LIFT)},
        week={k: "base" for k in DAY_KEYS}))
    sfx, sv = engine._day_type_views(shadow)[0]
    assert "day-type 'base'" in sfx
    assert sv["targets"] is shadow["target_profiles"]["base"]
    # inert path intact: no profiles -> the SAME object, empty suffix
    flat = _person(profiles=False)
    inert = engine._day_type_views(flat)
    assert len(inert) == 1 and inert[0][0] == "" and inert[0][1] is flat


# --------------------------------------------------------------------------- #
#  T-V1..V9 — validation (all-errors, one code per rule)
# --------------------------------------------------------------------------- #
def _doc(person_extra=None):
    p = {"targets": {"protein": 100, "fat": 60, "carb": 200},
         "tolerance": 0.05}
    p.update(person_extra or {})
    return {"schema_version": 1, "people": {"p1": p},
            "settings": {"days": 7, "active_min_budget": 180,
                         "cook_days": [0], "max_days_same_component": 4}}


def _codes(doc):
    return [i.code for i in validate_people_doc(doc)]


PROFILES = {"lift": {"protein": 180, "fat": 60, "carb": 250}}


def test_v1_profiles_without_week():
    codes = _codes(_doc({"target_profiles": dict(PROFILES)}))
    assert "profiles_without_week" in codes


def test_v2_week_without_profiles():
    codes = _codes(_doc({"week": {"mon": "lift"}}))
    assert "week_without_profiles" in codes


@pytest.mark.parametrize("bad", [{}, [], "lift", 3,
                                 {"": {"protein": 1, "fat": 1, "carb": 1}},
                                 {"lift": [180, 60, 250]}])
def test_v3_bad_target_profiles(bad):
    codes = _codes(_doc({"target_profiles": bad,
                         "week": {"mon": "lift"}}))
    assert "bad_target_profiles" in codes, bad


def test_v4_profile_missing_macro_where():
    issues = validate_people_doc(_doc(
        {"target_profiles": {"lift": {"protein": 180, "fat": 60}},
         "week": {"mon": "lift"}}))
    hits = [i for i in issues if i.code == "missing_field"
            and "target_profiles" in i.where]
    assert hits, [str(i) for i in issues]
    assert hits[0].where == \
        "people.yaml: person 'p1', target_profiles.lift.carb"


def test_v5_profile_unknown_field():
    issues = validate_people_doc(_doc(
        {"target_profiles": {"lift": {"protein": 180, "fat": 60,
                                      "carb": 250, "kcal": 2500}},
         "week": {"mon": "lift"}}))
    hits = [i for i in issues if i.code == "unknown_field"]
    assert hits and "kcal" in hits[0].message


@pytest.mark.parametrize("bad_key", ["monday", "Mon", 0, "sun day"])
def test_v6_bad_week_day(bad_key):
    codes = _codes(_doc({"target_profiles": dict(PROFILES),
                         "week": {bad_key: "lift", "mon": "lift"}}))
    assert "bad_week_day" in codes, bad_key


def test_v6b_bad_week_shape():
    for bad in ({}, [], "mon: lift"):
        codes = _codes(_doc({"target_profiles": dict(PROFILES),
                             "week": bad}))
        assert "bad_week" in codes, bad


def test_v7_unknown_profile_and_dead_profile():
    codes = _codes(_doc({"target_profiles": dict(PROFILES),
                         "week": {"mon": "bulk"}}))
    assert "unknown_profile" in codes
    assert "dead_profile" in codes          # 'lift' authored, never used
    # non-string week value is also unknown_profile
    codes = _codes(_doc({"target_profiles": dict(PROFILES),
                         "week": {"mon": 3, "tue": "lift"}}))
    assert "unknown_profile" in codes


def test_v8_validation_all_errors_accumulate():
    """One document tripping five distinct rules yields all five codes in
    ONE pass — never first-error-wins."""
    issues = validate_people_doc(_doc({
        "target_profiles": {
            "lift": {"protein": 180, "fat": 60},           # missing carb
            "junk": {"protein": 1, "fat": 1, "carb": 1,
                     "kcal": 9},                           # unknown_field
            "dead": {"protein": 2, "fat": 2, "carb": 2},   # dead_profile
        },
        "week": {"monday": "lift",                         # bad_week_day
                 "mon": "lift", "tue": "junk",
                 "wed": "bulk"},                           # unknown_profile
    }))
    codes = {i.code for i in issues}
    for expect in ("missing_field", "unknown_field", "dead_profile",
                   "bad_week_day", "unknown_profile"):
        assert expect in codes, (expect, sorted(codes))
    assert len(issues) >= 5


def test_v9_targets_validation_bytes_unchanged():
    """The _macro_map_issues extraction is refactor-pure: a legacy
    bad-targets doc produces the EXACT pre-M1.11 issue (code + where +
    message + severity), and a clean legacy doc produces zero issues."""
    doc = _doc()
    del doc["people"]["p1"]["targets"]["protein"]
    issues = [i for i in validate_people_doc(doc)
              if i.code == "missing_field" and "targets" in i.where]
    assert len(issues) == 1
    i = issues[0]
    assert i.where == "people.yaml: person 'p1', targets.protein"
    assert i.message == "daily target for 'protein' is missing"
    assert i.severity == "error"
    assert validate_people_doc(_doc()) == []


def test_valid_cycling_doc_is_clean():
    doc = _doc({"target_profiles":
                {"lift": {"protein": 180, "fat": 60, "carb": 250},
                 "weekend": {"protein": 120, "fat": 70, "carb": 180}},
                "week": {"mon": "lift", "sat": "weekend"}})
    assert validate_people_doc(doc) == []


# --------------------------------------------------------------------------- #
#  T-C1 — the CLI date gate fires PRE-SOLVE; no-profile users unaffected
# --------------------------------------------------------------------------- #
SOLO_MENU = "chicken_grill,rice_pot,garden_veg"      # explicit menu: zero LP


def test_week_requires_date_with_profiles(capsys):
    """`week` on the cycling fixture without --date: structured
    date_required at the usage exit code, raised BEFORE any solve (this
    test runs in the FAST tier precisely because nothing solves)."""
    with pytest.raises(SystemExit) as e:
        cli.main(["week", "--library", str(FIXTURES / "cycling_lifter"),
                  "--json"])
    assert e.value.code == cli.EXIT_USAGE
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "date_required"
    assert "sol" in doc["error"]["message"]           # names who forced it


def test_menu_requires_date_with_profiles_too(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["menu", "--library", str(FIXTURES / "cycling_lifter"),
                  "--menu", SOLO_MENU, "--json"])
    assert e.value.code == cli.EXIT_USAGE
    assert json.loads(capsys.readouterr().out)["error"]["code"] == \
        "date_required"


def test_no_profiles_still_runs_without_date(capsys):
    """A missing date cannot crash anyone it doesn't concern: solo_lifter
    (no profiles) runs date-less exactly as today. Explicit --menu keeps
    this zero-LP (menu is scored, not solved)."""
    cli.main(["menu", "--library", str(FIXTURES / "solo_lifter"),
              "--menu", SOLO_MENU])
    out = capsys.readouterr().out
    assert "chicken_grill" in out


def test_cycling_with_date_passes_the_gate(capsys):
    cli.main(["menu", "--library", str(FIXTURES / "cycling_lifter"),
              "--date", "2026-08-10", "--menu", SOLO_MENU])
    out = capsys.readouterr().out
    assert "chicken_grill" in out


def test_doctor_needs_no_date_smoke():
    """The doctor is anchor-free by construction (§7) — the date gate must
    NOT cover it. Validation-level smoke only: the gate check sits before
    command dispatch, so reaching the (LP-heavy) doctor body is proven by
    the SLOW doctor test; here we just prove the gate exempts doctor by
    inspecting the guard, not by solving."""
    import inspect
    src = inspect.getsource(cli._run)
    assert 'a.cmd != "doctor"' in src
