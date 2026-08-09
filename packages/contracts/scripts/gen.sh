#!/usr/bin/env sh
# Regenerate the contract artifacts (both CHECKED IN):
#   openapi.json  <- the solver service's OpenAPI document (dump_openapi.py)
#   src/index.ts  <- openapi-typescript over openapi.json, with the
#                    "GENERATED — never hand-edit" header prepended
# PYTHON must point at a python with the mealplan [service] extra installed
# (defaults to python3; the Makefile passes the repo venv).
set -eu
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"

"$PY" scripts/dump_openapi.py openapi.json
npx openapi-typescript openapi.json -o src/index.ts.tmp
{
  printf '// GENERATED — never hand-edit.\n'
  printf '// Source: services/solver/mealplan/schemas.py + service.py via openapi.json.\n'
  printf '// Regenerate: make contracts (or: npm run gen). CI gate: npm run check.\n'
  cat src/index.ts.tmp
} > src/index.ts
rm -f src/index.ts.tmp
