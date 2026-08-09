VENV ?= .venv
PY   := $(VENV)/bin/python

.PHONY: test install baseline

install:
	$(VENV)/bin/pip install -e "services/solver[dev]"

test:
	$(PY) -m pytest services/solver/tests -q

# M0.14 (PRD §8.5): record measured perf baselines on this machine.
# The date is injected here — the engine (and the baseline script's
# measurements) read no calendar clock.
baseline:
	$(PY) services/solver/tools/baseline.py --runs 5 \
		--date "$$(date +%Y-%m-%d)" --out services/solver/BASELINES.md
