"""mealplan — constraint-based batch-cooking planner engine.

M0 extraction of the v1 prototype (`mealplan/plan.py` at the repo root, commit
4851dd1). Behavior-preserving, bug-for-bug: the prototype's known defects
(hash(pname) day seeding, unused --force, global-ceil batching, 1-batch waste in
the menu score, …) are deliberately kept — each is a Phase 2 task with its own
regression test. The ONLY behavior added here is schema validation (M0.2).

Modules:
    model    typed dataclasses + macro/tag derivation (per100, tag union)
    io_yaml  load/save with schema validation and atomic writes
    engine   plate LP, doctor, menu search, week assembly, availability
    costing  purchasing, cook minutes, batch estimation, cost attribution
    units    human pack sizes, miss formatting, kcal
    cli      argparse front end (console script: mealplan)
"""

__version__ = "0.1.0"

SCHEMA_VERSION = 1
