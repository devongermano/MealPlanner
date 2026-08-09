"""M0.14 — CI-assertable LP-solve budgets (PRD §8.5, measure-then-promise).

Solve COUNTS are deterministic — same inputs + seed produce the same CBC
invocations on every machine — so CI asserts them. Wall-clock timings are
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

# One frozen parameter set, shared with the determinism golden — a drift
# between the two files cannot happen because both import it from here.
from test_capabilities import GOLDEN_MENU_KW, GOLDEN_SEED

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# --------------------------------------------------------------------------- #
#  recorded baselines — `make baseline` -> services/solver/BASELINES.md
#  (recorded 2026-08-09, macOS arm64 / Python 3.14.4 / PuLP 3.3.2 CBC)
# --------------------------------------------------------------------------- #
# Golden solo_lifter pipeline: EXACT per-stage counts (deterministic).
GOLDEN_SOLO_COUNTS = {
    "menu-verify": 1,
    "plate": 323,
}

# Examples (founder corpus) `week`-command pipeline (doctor + choose_menu at
# the CLI default n=12 + build_week + session_plan), total solve count.
# Budget = recorded count + ~20% headroom: the corpus is hand-edited data and
# small drifts are expected; order-of-magnitude jumps are not.
EXAMPLES_RECORDED_TOTAL = 698
EXAMPLES_BUDGET = int(EXAMPLES_RECORDED_TOTAL * 1.2)      # 837


def test_golden_solo_pipeline_solve_counts_exact():
    """CATCHES: any change in how much LP work the frozen golden pipeline
    does — an accidental extra verify pass, a doubled re-solve, a silently
    skipped stage. The counts are exact per stage because the pipeline is
    deterministic end to end."""
    engine.reset_solve_counts()
    ing, comps, people, settings = io_yaml.load(FIXTURES / "solo_lifter")
    menu, info, feasible, broke = engine.choose_menu(
        comps, ing, people, settings, **GOLDEN_MENU_KW)
    weeks, demand = engine.build_week(comps, people, settings, menu,
                                      seed=GOLDEN_SEED, ing=ing)
    costing.session_plan(comps, ing, settings, weeks)
    got = engine.solve_counts()
    assert got == GOLDEN_SOLO_COUNTS, (
        f"solve counts drifted from the recorded baseline: {got} != "
        f"{GOLDEN_SOLO_COUNTS} — if the change is intended, re-record via "
        "`make baseline` in a reviewed commit (services/solver/BASELINES.md)")


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
