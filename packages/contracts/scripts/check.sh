#!/usr/bin/env sh
# Drift gate: regenerate both artifacts to a temp dir and diff against the
# checked-in copies. Nonzero exit on ANY drift — schema changed without
# regenerating, or a hand edit to generated files. Run by CI
# (.github/workflows/contracts.yml) and `make contracts-check`.
set -eu
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

"$PY" scripts/dump_openapi.py "$TMP/openapi.json"
npx openapi-typescript "$TMP/openapi.json" -o "$TMP/index.ts.tmp"
{
  printf '// GENERATED — never hand-edit.\n'
  printf '// Source: services/solver/mealplan/schemas.py + service.py via openapi.json.\n'
  printf '// Regenerate: make contracts (or: npm run gen). CI gate: npm run check.\n'
  cat "$TMP/index.ts.tmp"
} > "$TMP/index.ts"

FAIL=0
diff -u openapi.json "$TMP/openapi.json" \
  || { echo "DRIFT: openapi.json is stale — run 'make contracts' and commit."; FAIL=1; }
diff -u src/index.ts "$TMP/index.ts" \
  || { echo "DRIFT: src/index.ts is stale — run 'make contracts' and commit."; FAIL=1; }
exit "$FAIL"
