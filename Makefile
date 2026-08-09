VENV ?= .venv
PY   := $(VENV)/bin/python

.PHONY: test test-fast test-serial install baseline

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

# M0.14 (PRD §8.5): record measured perf baselines on this machine.
# The date is injected here — the engine (and the baseline script's
# measurements) read no calendar clock.
baseline:
	$(PY) services/solver/tools/baseline.py --runs 5 \
		--date "$$(date +%Y-%m-%d)" --out services/solver/BASELINES.md
