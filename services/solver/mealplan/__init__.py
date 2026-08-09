"""mealplan — constraint-based batch-cooking planner engine.

M0 extraction of the v1 prototype (deleted at M0 completion; preserved in git
history at commit 4851dd1). Extracted behavior-preserving, bug-for-bug; Phase 2
then fixed the prototype's known defects one by one, each with regression tests.
All eleven Phase 2 fixes (M0.3–M0.13) have landed with regression tests:
explicit-seed determinism, canonical per-session batching (costing.session_plan
is the one source for cook plan, minutes, purchasing, cost), dead config wired
or deleted (--force works; freezes is live via use_freezer; min_lean_anchors
is a setting; meals_per_day reserved for M1 presentation), raw freshness
(shop_days + freezable with thaw notes), edible_fraction, snap/pin clamping,
Atwater-only kcal, pantry deduction, estimated-batch score scale, and the
doctor diagnostics (binding macro, volume floor, lean coverage, carb headroom).

Modules:
    model    typed dataclasses + macro/tag derivation (per100, tag union)
    io_yaml  load/save with schema validation and atomic writes
    engine   plate LP, doctor, menu search, week assembly, availability
    costing  purchasing, cook minutes, batch estimation, cost attribution
    units    human pack sizes, miss formatting, kcal
    cli      argparse front end (console script: mealplan)
    instrument  wall-clock stage timing OUTSIDE the engine (M0.14, §8.5);
             the deterministic solve counters live in engine.SOLVE_COUNTS
"""

__version__ = "0.1.0"

SCHEMA_VERSION = 1
