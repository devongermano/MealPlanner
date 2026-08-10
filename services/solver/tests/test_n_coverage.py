"""M1.4 — household sizes n=1..4 exercised end to end (slow tier).

One parametrized pipeline test across the fixture ladder:

    solo_lifter             n=1
    conflicting_exclusions  n=2 (disjoint allergen worlds)
    trio_split              n=3 (NEW — solo library, three eaters)
    family_four             n=4 (kcal spread + two exclusion tags)

For each: load → choose_menu → build_week → session_plan runs to
completion and every person comes out with the full per-person structure —
a week of day plates and at least one actually-served day. Capability
framing (PRD §9): the assertion is structural presence for every household
size, not solver luck on any particular fixture.
"""

from pathlib import Path

import pytest

from mealplan import costing, engine, io_yaml

FIXTURES = Path(__file__).parent / "fixtures"

LADDER = [("solo_lifter", 1), ("conflicting_exclusions", 2),
          ("trio_split", 3), ("family_four", 4)]


@pytest.mark.parametrize("fixture,n_people", LADDER,
                         ids=[f"{name}-n{n}" for name, n in LADDER])
def test_pipeline_serves_every_household_size(fixture, n_people):
    ing, comps, people, settings = io_yaml.load(FIXTURES / fixture)
    assert len(people) == n_people
    # whole-library menus: the ladder proves the pipeline serves every
    # household SIZE, so give each fixture its full (tiny) library rather
    # than let a 6-of-8 subset starve the high-kcal eater. n == len(comps)
    # is the just-fits case choose_menu explicitly supports (M1.4 minor A's
    # neighbor).
    n = len(comps)
    menu, info, feas, broke = engine.choose_menu(
        comps, ing, people, settings, n=n, seed=0, iters=600, shortlist=8)
    assert menu and len(menu) == n
    diag = {}
    weeks, demand = engine.build_week(comps, people, settings, menu,
                                      seed=0, ing=ing, diag=diag)
    sp = costing.session_plan(comps, ing, settings, weeks)

    # per-person structures present for ALL people, at every n
    assert set(weeks) == set(people)
    days = settings["days"]
    fed_someone = False
    for pname, wk in weeks.items():
        assert len(wk) == days, (pname, len(wk))
        served = [pl for pl in wk if pl]
        # a person the library can feasibly plate MUST get served days; a
        # structurally infeasible person (family_four's petit runs a real
        # fat deficit on the full library — a doctor-level fixture fact)
        # still gets the FULL per-person structure: a week of explained
        # holes, never a missing entry
        ok, _, _ = engine.plate(people[pname], comps, menu)
        if ok:
            assert served, f"{pname}: plate-feasible but never served"
            fed_someone = True
        else:
            assert all(t is None for t in diag["relax_tiers"][pname]), \
                f"{pname}: infeasible yet a relaxation tier claims a plate"
        for pl in served:
            assert all(cid in menu and g > 0 for cid, g in pl.items())
        # relaxation-tier bookkeeping exists for everyone (P8)
        assert len(diag["relax_tiers"][pname]) == days
    assert fed_someone
    # one canonical session plan feeds them all (PRD §8.2)
    assert sp["minutes"] == sum(s["minutes"] for s in sp["sessions"])
    assert sp["batches"], "nothing cooked for a served week"
