"""M1.11 target-profile e2e (M111_SPEC §11 SLOW tier — registered in
conftest.SLOW_MODULES: full weeks of CBC solves at two anchors, an
equal-profiles inertness pair, a lock/verify round trip and a dish-mode
cycling week; the pure resolution logic is FAST-tier in
test_target_profiles.py by construction).

Deliberate NON-assertion (T-E1): base days of the cycling week are NOT
asserted byte-equal to the no-profile run's — build_week couples days
through used_days/used_g variety state, so per-day equality across
different neighboring days is not promised. The plumbing-injects-nothing
claim is carried by T-E3/T-E5 instead: profiles NUMERICALLY equal to base
must reproduce the no-profile weeks exactly, at exactly the same solve
counts.
"""

import contextlib
import copy
import io
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from mealplan import cli, costing, engine, io_yaml
from mealplan import dishes as dishes_mod
from mealplan.model import (DAY_KEYS, resolve_targets, week_day_label,
                            weekly_targets)
from mealplan.units import MACROS

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Every CLI-level date here is a THURSDAY on purpose (M111_SPEC §3): on a
# Monday the correct anchor and the trivial wrong one (a hard-coded 0)
# coincide, so a Monday-only suite certifies the derivation vacuously. At
# anchor 3 a Thursday start covers base (thu), lift (fri) and weekend
# (sat) inside the first three plan days.
THURSDAY = "2026-08-13"
THURSDAY_ANCHOR = 3

CYCLE_WEEK = {"mon": "lift", "wed": "lift", "fri": "lift",
              "sat": "weekend", "sun": "weekend"}


def _load(name):
    return io_yaml.load(FIXTURES / name)


def _library_with(tmp_path, name, **person_extra):
    """A copy of fixture library ``name`` whose single person gains
    ``person_extra``. Lets a CLI-level test reach shapes the packaged
    fixture deliberately does not carry, without perturbing the fixture
    every other test in this module solves against."""
    dst = tmp_path / f"{name}_variant"
    shutil.copytree(FIXTURES / name, dst)
    doc = yaml.safe_load((dst / "people.yaml").read_text())
    doc["people"]["sol"].update(person_extra)
    (dst / "people.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return dst


def _totals(comps, plate):
    return {m: sum(g * comps[c]["per100"][m] / 100
                   for c, g in plate.items()) for m in MACROS}


def _in_band(tot, tgt, tol, slack=1.0):
    """±tol band with a 1g grace for the LP's discrete snapping."""
    return all(tgt[m] * (1 - tol) - slack <= tot[m]
               <= tgt[m] * (1 + tol) + slack for m in MACROS)


@pytest.fixture(scope="module")
def cycling():
    ing, comps, people, settings = _load("cycling_lifter")
    return dict(ing=ing, comps=comps, people=people, settings=settings)


def _solve_week(cyc, anchor):
    weeks, demand = engine.build_week(
        cyc["comps"], cyc["people"], cyc["settings"],
        sorted(cyc["comps"]), seed=0, ing=cyc["ing"], anchor=anchor)
    return weeks["sol"]


@pytest.fixture(scope="module")
def week_anchor0(cycling):
    return _solve_week(cycling, 0)


# --------------------------------------------------------------------------- #
#  T-E1 — cycling changes exactly the intended days (anchor 0 = Monday)
# --------------------------------------------------------------------------- #
def test_cycling_changes_exactly_intended_days(cycling, week_anchor0):
    comps = cycling["comps"]
    p = cycling["people"]["sol"]
    tol = p["tolerance"]
    wk = week_anchor0
    assert len(wk) == 7 and all(wk), "explained hole in the cycling week"
    for d, plate in enumerate(wk):
        tgt = resolve_targets(p, d, 0)
        assert _in_band(_totals(comps, plate), tgt, tol), \
            (d, _totals(comps, plate), dict(tgt))
    # sign test, never tolerance noise: every lift day's protein clears
    # every weekend day's (190*.94 - 1 > 110*1.06 + 1)
    prot = [_totals(comps, plate)["protein"] for plate in wk]
    for lift_d in (0, 2, 4):
        for wknd_d in (5, 6):
            assert prot[lift_d] > prot[wknd_d], (prot, lift_d, wknd_d)


# --------------------------------------------------------------------------- #
#  T-E2 — anchor rotation shifts the day-type pattern by exactly one
# --------------------------------------------------------------------------- #
def test_anchor_rotation_shifts_pattern(cycling):
    p = cycling["people"]["sol"]
    # resolved-target pattern: anchor 1's day d IS anchor 0's day d+1
    for d in range(6):
        assert resolve_targets(p, d, 1) is resolve_targets(p, d + 1, 0)
    assert resolve_targets(p, 6, 1) is resolve_targets(p, 0, 0)   # wraps
    # and a solved Tuesday-start week sits in each RESOLVED day's band
    comps, tol = cycling["comps"], p["tolerance"]
    wk1 = _solve_week(cycling, 1)
    assert all(wk1)
    for d, plate in enumerate(wk1):
        assert _in_band(_totals(comps, plate), resolve_targets(p, d, 1),
                        tol), d
    # Tuesday start: d0 is now a BASE day (tue) and d4 a weekend day (sat)
    prot = [_totals(comps, plate)["protein"] for plate in wk1]
    assert prot[1] > prot[4]              # wed lift > sat weekend


# --------------------------------------------------------------------------- #
#  T-E3 / T-E5 — equal profiles are plate-inert at zero extra solves
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def inert_pair():
    """(no-profile run, equal-profiles run): same solo_lifter corpus, the
    second person authors profiles NUMERICALLY equal to base over a full
    cycling week map — the plumbing must inject nothing."""
    out = []
    for profiled in (False, True):
        docs = io_yaml.load_raw_docs(FIXTURES / "solo_lifter")
        ppl = copy.deepcopy(docs["people"])
        if profiled:
            base = ppl["people"]["sol"]["targets"]
            ppl["people"]["sol"]["target_profiles"] = {
                "lift": dict(base), "weekend": dict(base)}
            ppl["people"]["sol"]["week"] = dict(CYCLE_WEEK)
        ing, comps, people, settings = io_yaml.load_docs(
            docs["ingredients"], docs["components"], ppl)
        engine.reset_solve_counts()
        weeks, demand = engine.build_week(
            comps, people, settings, sorted(comps), seed=0, ing=ing,
            anchor=0 if profiled else None)
        out.append(dict(weeks=weeks, demand=demand,
                        counts=engine.solve_counts()))
    return out


def test_equal_profiles_are_plate_inert(inert_pair):
    plain, equal = inert_pair
    assert equal["weeks"] == plain["weeks"]
    assert equal["demand"] == plain["demand"]


def test_profiles_add_zero_week_solves(inert_pair):
    """Resolution costs no LP: solve counts are IDENTICAL per stage for
    the equal-profiles run (solve-count budget, never wall clock)."""
    plain, equal = inert_pair
    assert equal["counts"] == plain["counts"]


# --------------------------------------------------------------------------- #
#  T-E4 — lock --date -> verify-plan round trip (anchor via snapshot)
# --------------------------------------------------------------------------- #
EXPLICIT_MENU = ("chicken_grill,beef_braise,pork_roast,rice_pot,"
                 "garden_veg,oil_drizzle,herb_sauce")


def test_lock_verify_roundtrip_profiles(tmp_path, capsys, cycling):
    """Locked on a THURSDAY, deliberately not a Monday: the round trip is
    only a real test of §3 when the correct anchor (3) differs from the
    trivial wrong one (0). Two independent claims ride on that:

    - the LOCKED plan is anchored to the real weekday — day 1 (thu) is a
      base day and day 2 (fri) a lift day, and the two bands are disjoint,
      so a pinned anchor lands the portions in the wrong band;
    - `verify-plan` re-derives the SAME anchor from the snapshot's
      plan_date (zero new snapshot fields) — a divergence between the two
      derivation sites shows up as portions_ok False.
    """
    plans = tmp_path / "plans"
    cli.main(["lock", "--library", str(FIXTURES / "cycling_lifter"),
              "--date", THURSDAY, "--menu", EXPLICIT_MENU,
              "--plans", str(plans), "--json"])
    lock_doc = json.loads(capsys.readouterr().out)
    assert lock_doc["ok"] is True
    plan_path = lock_doc["result"]["plan_path"]
    cli.main(["verify-plan", plan_path, "--json"])
    ver = json.loads(capsys.readouterr().out)
    assert ver["ok"] is True
    assert ver["result"]["verified"] is True
    assert ver["result"]["hash_ok"] and ver["result"]["portions_ok"]
    doc = yaml.safe_load(Path(plan_path).read_text())
    # zero new snapshot fields: the inputs snapshot carries exactly the
    # M1.3/M1.13 keys — the anchor rides in plan_date
    assert sorted(doc["inputs"]) == ["library", "overrides", "pantry",
                                    "plan_date", "seed"]
    # the locked portions were solved against the THURSDAY-anchored week
    comps, p = cycling["comps"], cycling["people"]["sol"]
    tol = p["tolerance"]
    week = doc["portions"]["sol"]
    for d, plate in enumerate(week):
        if not plate:
            continue
        assert _in_band(_totals(comps, plate),
                        resolve_targets(p, d, THURSDAY_ANCHOR), tol), d
    # non-vacuous: day 0 (thu) is base and day 1 (fri) is lift, and their
    # protein bands do not overlap — anchor 0 would swap the pair
    assert week[0] and week[1]
    assert _totals(comps, week[1])["protein"] \
        > _totals(comps, week[0])["protein"]


def test_lock_without_date_still_date_required(capsys):
    """The pre-existing lock gate still fires first (message names lock's
    artifact-key reason, not the profile reason)."""
    with pytest.raises(SystemExit) as e:
        cli.main(["lock", "--library", str(FIXTURES / "cycling_lifter"),
                  "--menu", EXPLICIT_MENU, "--json"])
    assert e.value.code == cli.EXIT_USAGE
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "date_required"


# --------------------------------------------------------------------------- #
#  T-E6 — dish-mode cycling week (injection point #12)
# --------------------------------------------------------------------------- #
def test_dishes_pipeline_cycling():
    """solo_dishes corpus + in-memory profiles: every dish-mode meal's
    slot target is the RESOLVED day's share (the day-view person reached
    plate_dishes), and day totals sit in the resolved day's band wherever
    the day shipped without a macro miss."""
    docs = io_yaml.load_raw_docs(FIXTURES / "solo_dishes")
    ppl = copy.deepcopy(docs["people"])
    ppl["people"]["sol"]["target_profiles"] = {
        "lift": {"protein": 170, "fat": 62, "carb": 265},
        "weekend": {"protein": 125, "fat": 68, "carb": 220}}
    ppl["people"]["sol"]["week"] = dict(CYCLE_WEEK)
    ing, comps, people, settings = io_yaml.load_docs(
        docs["ingredients"], docs["components"], ppl)
    dmap = io_yaml.load_dishes(FIXTURES / "solo_dishes" / "dishes.yaml",
                               comps=comps, people=people)
    weeks, demand, mealdays = dishes_mod.build_week_dishes(
        comps, people, settings, dmap, sorted(dmap), seed=0, ing=ing,
        anchor=0)
    p = people["sol"]
    tol = p["tolerance"]
    mds = mealdays["sol"]
    assert len(mds) == 7
    for d, md in enumerate(mds):
        tgt = resolve_targets(p, d, 0)
        n = len(md["meals"])
        assert n >= 1
        for meal in md["meals"]:
            # the slot target IS the resolved day's share — direct proof
            # the day-view person reached the dish LP
            assert meal["target"] == {m: round(tgt[m] / n, 1)
                                      for m in MACROS}, (d, meal["slot"])
        missed = any(f.get("code") in ("band_unmet", "dish_band_binding",
                                       "meal_underfilled",
                                       "no_dish_assignable")
                     for f in md["notes"]) \
            or any(f.get("code") == "no_dish_assignable"
                   for meal in md["meals"] for f in meal["flags"])
        if not missed and weeks["sol"][d]:
            assert _in_band(_totals(comps, weeks["sol"][d]), tgt, tol), d


# --------------------------------------------------------------------------- #
#  T-E7 — MEAL-layer injection (§5.1 row 16: meals.deal_week -> deal_day)
# --------------------------------------------------------------------------- #
def test_meal_slot_targets_track_the_resolved_day(tmp_path, capsys):
    """Injection point #16 end to end: `meals.deal_week` deals each day
    against the DAY-VIEW person, so every slot target is the RESOLVED
    day's grams over n — not base grams.

    This is the households M1.9/M1.10 exist for (meal slots => eat
    sheets), and none of the packaged profiled fixtures carry a meal
    structure, so the variant is built here. Two mutants die on the same
    assertion: reverting `deal_week` to the base person, and pinning the
    CLI anchor (the run is a THURSDAY, so day 1 is base and day 2 lift).
    """
    lib = _library_with(tmp_path, "cycling_lifter", meals_per_day=3)
    plans = tmp_path / "plans"
    cli.main(["lock", "--library", str(lib), "--date", THURSDAY,
              "--menu", EXPLICIT_MENU, "--plans", str(plans), "--json"])
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] is True
    plan = yaml.safe_load(Path(res["result"]["plan_path"]).read_text())
    p = _load("cycling_lifter")[2]["sol"]          # same targets/profiles
    mds = plan["meals"]["sol"]
    assert len(mds) == 7
    seen = set()
    for d, md in enumerate(mds):
        tgt = resolve_targets(p, d, THURSDAY_ANCHOR)
        n = len(md["meals"])
        assert n == 3, (d, n)
        for meal in md["meals"]:
            assert meal["target"] == {m: round(tgt[m] / n, 1)
                                      for m in MACROS}, (d, meal["slot"])
        seen.add(week_day_label(p, d, THURSDAY_ANCHOR))
    # non-vacuous: a Thursday start hits base AND both profiles, and the
    # three per-slot target triples are pairwise distinct
    assert seen == {None, "lift", "weekend"}
    trip = {tuple(sorted(mds[d]["meals"][0]["target"].items()))
            for d in range(7)}
    assert len(trip) == 3


# --------------------------------------------------------------------------- #
#  §9 rendering — plan.md AND the household-facing eat sheet
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def thursday_render(tmp_path_factory):
    """ONE CLI `week` run on cycling_lifter at a THURSDAY start, emitting
    BOTH plan.md and the artifact set — the two §9 renderers share the run
    so covering the eat sheet costs zero extra solves.

    Module-scoped, so it captures the CLI's chatter itself (capsys is
    function-scoped) — nothing here asserts on stdout."""
    d = tmp_path_factory.mktemp("thursday_render")
    out, arts = d / "plan.md", d / "artifacts"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        cli.main(["week", "--library", str(FIXTURES / "cycling_lifter"),
                  "--date", THURSDAY, "--menu", EXPLICIT_MENU,
                  "--out", str(out), "--artifacts", str(arts)])
    return dict(plan=out.read_text(),
                eat=(arts / "eat_sol.md").read_text())


def test_week_render_carries_weekly_emergent_line(thursday_render):
    """Rendered through the CLI on a THURSDAY: the day tags are the proof
    that `cli._run` derived the anchor from the plan date rather than
    assuming Monday — at anchor 3 plan day 1 is `thu` (base), day 2 `fri`
    (lift) and day 4 `sun` (weekend). A pinned anchor 0 would render
    `(mon — lift)` first and fail every tag assertion below."""
    text = thursday_render["plan"]
    p = _load("cycling_lifter")[2]["sol"]
    wt = weekly_targets(p, 7, THURSDAY_ANCHOR)
    assert "Weekly total (emergent)" in text
    assert f"{wt['protein']:g}p" in text
    assert "days are solved independently" in text
    # day tags, anchored to the REAL weekday of 2026-08-13
    assert "**Day 1** (thu) —" in text          # base day: weekday only
    assert "**Day 2** (fri — lift) —" in text
    assert "**Day 4** (sun — weekend) —" in text
    assert "(mon — lift)" in text               # day 5, the wrapped Monday
    assert "**Day 1** (mon — lift)" not in text


DAY_HEADING = re.compile(r"^## Day (\d+)(?: \(([^)]+)\))? — ")
MACRO_LINE = re.compile(
    r"^- (protein|fat|carb): ([\d.]+)g of ([\d.]+)g target — (.+)$")


def _parse_eat_days(text):
    """``[(day_number, tag_or_None, {macro: (shown, target, status)})]``
    read back off a rendered eat sheet."""
    days, cur = [], None
    for ln in text.splitlines():
        h = DAY_HEADING.match(ln)
        if h:
            cur = (int(h.group(1)), h.group(2), {})
            days.append(cur)
            continue
        m = cur and MACRO_LINE.match(ln)
        if m:
            cur[2][m.group(1)] = (float(m.group(2)), float(m.group(3)),
                                  m.group(4))
    return days


def _band_status(shown, target, tol):
    """The eat sheet's tolerance verdict, RE-DERIVED here rather than
    imported: the point is to check artifacts.py against the spec, not
    against itself (M111_SPEC §9 / §4 — the ±tol band around the day's
    target, else the signed gram delta)."""
    if target * (1 - tol) - 1e-9 <= shown <= target * (1 + tol) + 1e-9:
        return "hit"
    d = round(shown - target)
    return f"+{d}g over" if d > 0 else f"-{abs(d)}g short"


def test_eat_sheet_renders_the_resolved_day_targets(thursday_render):
    """§5.1 row 17 / §9, the HOUSEHOLD-FACING half: every macro line on
    the eat sheet names the RESOLVED day's grams and judges the day
    against them — plus the profile header block and the `(wed — lift)`
    day headings.

    This is the artifact a lifter actually reads at the fridge, and it is
    the one place base-vs-resolved is a lie a human acts on: with base
    targets a 178.7g-protein LIFT day renders `of 150g target — +29g
    over` and a fat line reads `short` on a day that is in band. Both
    goldens are no-profile (so `resolve_targets` returns the base map by
    identity and can never catch this), which is exactly why the check is
    spelled out here.

    Every expectation is DERIVED (resolve_targets + the §4 band), so the
    test pins behavior, not today's solver output — but it is non-vacuous
    by construction: the three day-types' targets are pairwise distinct,
    and the closing assertions demand that base targets would have
    changed both the printed grams and at least one printed verdict."""
    p = _load("cycling_lifter")[2]["sol"]
    base, tol = p["targets"], p["tolerance"]
    days = _parse_eat_days(thursday_render["eat"])
    assert [n for n, _, _ in days] == list(range(1, 8))

    fed, target_triples, verdict_would_flip = 0, set(), 0
    for n, tag, macros in days:
        d = n - 1
        td = resolve_targets(p, d, THURSDAY_ANCHOR)
        lbl = week_day_label(p, d, THURSDAY_ANCHOR)
        wd = DAY_KEYS[(THURSDAY_ANCHOR + d) % 7]
        # heading tag: `(fri — lift)` on a profiled day, bare `(thu)` on a
        # base day — "base" stays out of the reader's face (§9)
        assert tag == (f"{wd} — {lbl}" if lbl else wd), (n, tag)
        if not macros:
            continue                    # explained hole — no macro lines
        fed += 1
        assert set(macros) == set(MACROS), (n, sorted(macros))
        target_triples.add(tuple(macros[m][1] for m in MACROS))
        for m in MACROS:
            shown, target, status = macros[m]
            assert target == td[m], (n, m, target, dict(td))
            assert status == _band_status(shown, td[m], tol), \
                (n, m, shown, status)
            if _band_status(shown, base[m], tol) != status:
                verdict_would_flip += 1

    assert fed == 7, "every day of this run should ship a plate"
    # non-vacuity: base / lift / weekend all rendered, all distinct, and
    # reverting to base targets would both misprint the grams and flip at
    # least one household-facing verdict
    assert len(target_triples) == 3, target_triples
    assert tuple(base[m] for m in MACROS) in target_triples
    assert verdict_would_flip > 0

    # header block (§9): base line, one line per profile with its
    # weekdays, and the emergent weekly with its "days are solved
    # independently" caveat
    eat = thursday_render["eat"]
    wt = weekly_targets(p, 7, THURSDAY_ANCHOR)
    assert (f"Daily targets: {base['protein']}g protein / {base['fat']}g "
            f"fat / {base['carb']}g carb") in eat
    for nm, dayl in (("lift", "mon, wed, fri"), ("weekend", "sat, sun")):
        pt = p["target_profiles"][nm]
        assert (f"- {nm} ({dayl}): {pt['protein']}g protein / "
                f"{pt['fat']}g fat / {pt['carb']}g carb") in eat
    assert (f"Weekly total (emergent): {wt['protein']:g}g protein / "
            f"{wt['fat']:g}g fat / {wt['carb']:g}g carb") in eat
    assert "days are solved independently" in eat
    # the §9 example heading itself, at its Thursday-anchored day number
    assert "## Day 7 (wed — lift) — " in eat
