VENV ?= .venv
PY   := $(VENV)/bin/python

.PHONY: test install

install:
	$(VENV)/bin/pip install -e "services/solver[dev]"

test:
	$(PY) -m pytest services/solver/tests -q
