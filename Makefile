VENV ?= .venv
PY   := $(VENV)/bin/python

# Everyday knobs — override like: make plan SEED=3 N=10 LIBRARY=examples
LIBRARY   ?= examples
SEED      ?= 0
N         ?= 12
ARTIFACTS ?= artifacts

.PHONY: plan doctor shop menu test test-fast test-serial install baseline contracts contracts-check

# ---- everyday commands ----------------------------------------------------- #
# Full week plan: writes plan.md + the three deliverables into $(ARTIFACTS)/
plan:
	$(PY) -m mealplan.cli week --library $(LIBRARY) --seed $(SEED) --n $(N) \
		--artifacts $(ARTIFACTS) --out plan.md
	@echo "→ plan.md + $(ARTIFACTS)/{shopping_list,cook_plan,eat_*}.md"

# What can the library hit, and why/why not
doctor:
	$(PY) -m mealplan.cli doctor --library $(LIBRARY)

# Just the shopping list for this week's solve
shop:
	$(PY) -m mealplan.cli shop --library $(LIBRARY) --seed $(SEED) --n $(N)

# Just pick the menu (no portioning)
menu:
	$(PY) -m mealplan.cli menu --library $(LIBRARY) --seed $(SEED) --n $(N)

# ---- development ------------------------------------------------------------ #

install:
	$(VENV)/bin/pip install -e "services/solver[dev]"

# Parallel by default (pytest-xdist): LP-heavy tests are independent and
# CPU-bound. Solve COUNTS stay deterministic per test; the perf-budget and
# golden tests are per-process and unaffected by workers.
test:
	$(PY) -m pytest services/solver/tests -q -n auto

# Inner-loop tier: excludes LP/subprocess-heavy modules (tests/conftest.py
# SLOW_MODULES). This is what you (and fix agents) run while iterating;
# the full `make test` gates phases and CI.
test-fast:
	$(PY) -m pytest services/solver/tests -q -n auto -m "not slow"

# Serial fallback: timing investigations, or debugging worker-dependent flakes.
test-serial:
	$(PY) -m pytest services/solver/tests -q

# Contract codegen (Track B / M2 blocker — ARCHITECTURE.md "the one
# non-negotiable wiring rule"): pydantic schemas -> openapi.json -> TS in
# packages/contracts. Both artifacts are CHECKED IN; contracts-check is the
# drift gate CI runs (.github/workflows/contracts.yml). Requires node/npm
# and the [service] extra: pip install -e "services/solver[dev,service]".
contracts:
	cd packages/contracts && npm install --no-audit --no-fund \
		&& PYTHON="$(abspath $(PY))" npm run gen

contracts-check:
	cd packages/contracts && npm install --no-audit --no-fund \
		&& PYTHON="$(abspath $(PY))" npm run check

# M0.14 (PRD §8.5): record measured perf baselines on this machine.
# The date is injected here — the engine (and the baseline script's
# measurements) read no calendar clock.
baseline:
	$(PY) services/solver/tools/baseline.py --runs 5 \
		--date "$$(date +%Y-%m-%d)" --out services/solver/BASELINES.md
