# mealplan

Batch-cooking planner for a two-person household with very different macro targets,
different exclusions, and a shared grocery budget.

    pip install pyyaml pulp
    python3 serve.py            # the app -> http://localhost:8770

Or from the terminal:

    python3 plan.py doctor      # what the library can/can't hit, and why
    python3 plan.py all --n 12  # writes plan.md
    python3 plan.py frontier    # what each budget level buys you

The app has three tabs. **Plan** — menu chips you can pin or drop, dials for budget,
mass and dish count, and the budget frontier. **Eat** — per-day plates with live macro
meters; type a new gram value and hit Rebalance to re-solve the rest of that day around
it. **Shop & Cook** — checkable shopping list in real store units, and each cook
session's batch counts. Every interaction hits the real LP; nothing is precomputed.

## Files

    library/ingredients.yaml   per-100g macros + how each thing is actually sold
    library/components.yaml    recipes as ingredient lists. macros are DERIVED.
    library/people.yaml        targets, exclusions, tolerance, cook days
    plan.py                    the solver
    serve.py                   local web app (stdlib only)
    app.html                   the UI
    SKILL.md                   instructions for Claude

## Why it's built this way

Two problems, kept separate:

- **Menu selection** — small, combinatorial, fuzzy. Greedy + local search on a cheap
  structural score, then LP-verified. ~1s.
- **Portioning** — continuous and exact. A linear program. ~6s for a full week.

A single MILP over the whole week (all days × people × batch integers × variety
binaries) times out past two minutes. Decomposed, it's six seconds. That decomposition
is the main engineering decision in here.

## Adding a recipe

Append to `library/components.yaml` as an ingredient list with a `yield_g`. Never
hand-enter macros — they're computed. Then `python3 plan.py doctor`.

## Current state

Devon's targets in `library/people.yaml` are a **placeholder**. Replace them.
Nothing downstream is correct until you do.
