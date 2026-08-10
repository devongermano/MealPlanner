#!/usr/bin/env sh
# Drift gate: regenerate both artifacts to a temp dir and diff against the
# checked-in copies. Nonzero exit on ANY drift — a controller or DTO changed
# without regenerating, or someone hand-edited a generated file.
#
# This is what stops the web app from compiling against types the API no longer
# serves: the shapes are owned here, and they can only change by regenerating.
set -eu
cd "$(dirname "$0")/.."
HERE="$(pwd)"
API_DIR="$HERE/../../apps/api"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

(cd "$API_DIR" && npx ts-node -P tsconfig.json scripts/dump-openapi.ts "$TMP/openapi.json")
npx openapi-typescript "$TMP/openapi.json" -o "$TMP/index.ts.tmp"
{
  printf '// GENERATED — never hand-edit.\n'
  printf '// Source: apps/api Nest controllers + DTOs via openapi.json.\n'
  printf '// Regenerate: pnpm --filter @mealplan/contracts-api run gen. CI gate: pnpm --filter @mealplan/contracts-api run check.\n'
  cat "$TMP/index.ts.tmp"
} > "$TMP/index.ts"

FAIL=0
diff -u openapi.json "$TMP/openapi.json" \
  || { echo "DRIFT: openapi.json is stale — run 'pnpm --filter @mealplan/contracts-api run gen' and commit."; FAIL=1; }
diff -u src/index.ts "$TMP/index.ts" \
  || { echo "DRIFT: src/index.ts is stale — run 'pnpm --filter @mealplan/contracts-api run gen' and commit."; FAIL=1; }
exit "$FAIL"
