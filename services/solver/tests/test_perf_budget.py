"""M0.14 — CI-assertable LP-solve budgets (PRD §8.5, measure-then-promise).

Solve COUNTS are deterministic for a given CBC build — same inputs + seed
produce the same invocations on the pinned reference environment, where CI
asserts them exactly; other platforms assert tolerance bands (PRD §9 golden
policy — a foreign CBC may legally search differently). Wall-clock timings are
recorded-only in services/solver/BASELINES.md (``make baseline``) and are
NEVER asserted here: v1's single-shot wall-clock CI guards flaked by
construction, and PRD §8.5 bans the pattern.

The recorded numbers below come from ``make baseline`` on the reference
environment (see BASELINES.md). Updating them is a deliberate, reviewed act
— exactly like the determinism golden: a drift in the golden pipeline's
exact counts is a behavior change to review, never to absorb silently.
"""

from pathlib import Path

from mealplan import costing, engine, io_yaml

import refenv

# One frozen parameter set, shared with the determinism golden — a drift
# between the two files cannot happen because both import it from
# tests/_shared.py (M1.0: never from another test module — importlib-mode
# safe).
from _shared import GOLDEN_MENU_KW, GOLDEN_SEED

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# --------------------------------------------------------------------------- #
#  recorded baselines — `make baseline` -> services/solver/BASELINES.md
#  (recorded 2026-08-09, macOS arm64 / Python 3.14.4 / PuLP 3.3.2 CBC)
# --------------------------------------------------------------------------- #
# Golden solo_lifter pipeline: EXACT per-stage counts (deterministic).
# Re-recorded 2026-08-09 via `make baseline` for M1 Phase 1 (was plate:
# 323): M1.7 per-person serve scaling — with effective bounds aligned to
# the whole-gram emission grid — changes the plate LPs' feasible regions,
# so build_week's re-plate ladder takes a different (deterministic) number
# of solves — see services/solver/M1P1_NOTES.md.
GOLDEN_SOLO_COUNTS = {
    "menu-verify": 1,
    "plate": 323,   # re-recorded 2026-08-09: identity serve bounds (scaling
                    # revoked — the lard-beans incident) changed plate retry
                    # paths; see BASELINES.md + PRD Appendix B item 2
}

# Examples (founder corpus) `week`-command pipeline (doctor + choose_menu at
# the CLI default n=12 + build_week + session_plan), total solve count.
# Budget = recorded count x2: the corpus is LIVE hand-edited data (M1's gate
# will edit it), so this guard is deliberately order-of-magnitude — it
# catches solve-count EXPLOSIONS, and must not break on routine corpus edits
# (PRD §9: the founder household is not a test fixture).
# Re-recorded 2026-08-09 via `make baseline` for M1 Phase 1 (was 698):
# M1.7 scaling roughly doubles the examples pipeline's plate-LP work (588
# -> 1340 plate solves) — see services/solver/M1P1_NOTES.md.
EXAMPLES_RECORDED_TOTAL = 1454
EXAMPLES_BUDGET = EXAMPLES_RECORDED_TOTAL * 2


def test_golden_solo_pipeline_solve_counts_exact():
    """CATCHES: any change in how much LP work the frozen golden pipeline
    does — an accidental extra verify pass, a doubled re-solve, a silently
    skipped stage. The counts are exact per stage because the pipeline is
    deterministic end to end ON the pinned reference environment; elsewhere
    a different CBC build may legally take a different search path, so the
    assert relaxes to a tolerance band (PRD §9 golden policy)."""
    engine.reset_solve_counts()
    ing, comps, people, settings = io_yaml.load(FIXTURES / "solo_lifter")
    menu, info, feasible, broke = engine.choose_menu(
        comps, ing, people, settings, **GOLDEN_MENU_KW)
    weeks, demand = engine.build_week(comps, people, settings, menu,
                                      seed=GOLDEN_SEED, ing=ing)
    costing.session_plan(comps, ing, settings, weeks)
    got = engine.solve_counts()
    if refenv.is_reference_env():
        assert got == GOLDEN_SOLO_COUNTS, (
            f"solve counts drifted from the recorded baseline: {got} != "
            f"{GOLDEN_SOLO_COUNTS} — if the change is intended, re-record via "
            "`make baseline` in a reviewed commit (services/solver/"
            "BASELINES.md)")
    else:
        # off-reference: same stages, totals within a 2x band (explosions
        # still fail; a foreign CBC's different search path does not)
        assert set(got) == set(GOLDEN_SOLO_COUNTS), (got, refenv.REFERENCE)
        ref_total = sum(GOLDEN_SOLO_COUNTS.values())
        assert 0 < sum(got.values()) <= 2 * ref_total, (
            f"off-reference solve total {sum(got.values())} outside the "
            f"2x band around recorded {ref_total}")


def test_examples_week_pipeline_within_solve_budget():
    """CATCHES: solve-count explosions on the founder corpus (the pipeline a
    real `mealplan week` run performs). Budget, not exact: the corpus is
    editable data, so the assert allows ~20% headroom over the recorded
    count and only fails on real growth."""
    engine.reset_solve_counts()
    ing, comps, people, settings = io_yaml.load(EXAMPLES)
    engine.doctor(comps, people, settings, ing=ing)
    menu, menuinfo, feas, broke = engine.choose_menu(
        comps, ing, people, settings, n=12, seed=0)   # CLI defaults
    weeks, demand = engine.build_week(comps, people, settings, menu,
                                      seed=0, ing=ing)
    costing.session_plan(comps, ing, settings, weeks)
    total = sum(engine.solve_counts().values())
    assert total > 0, "pipeline ran no LP solves — instrumentation broken"
    assert total <= EXAMPLES_BUDGET, (
        f"examples pipeline used {total} LP solves, over the "
        f"{EXAMPLES_BUDGET} budget (recorded {EXAMPLES_RECORDED_TOTAL} "
        "+ ~20% headroom) — find the regression or re-record deliberately "
        "via `make baseline` (services/solver/BASELINES.md)")
