#!/usr/bin/env sh
# Regenerate the API contract artifacts (both CHECKED IN):
#   openapi.json  <- apps/api's OpenAPI document (scripts/dump-openapi.ts)
#   src/index.ts  <- openapi-typescript over openapi.json, with the
#                    "GENERATED — never hand-edit" header prepended
#
# Sibling of packages/contracts/scripts/gen.sh, deliberately the same shape:
# one command regenerates, one command fails on drift. The difference is the
# producer — that one mirrors the Python engine's result models, this one
# mirrors the Nest API's own request/response DTOs.
set -eu
cd "$(dirname "$0")/.."
HERE="$(pwd)"
API_DIR="$HERE/../../apps/api"

# Boots the real AppModule and reads the routes Nest registered — the document
# cannot describe an endpoint that does not exist.
(cd "$API_DIR" && npx ts-node -P tsconfig.json scripts/dump-openapi.ts "$HERE/openapi.json")

npx openapi-typescript openapi.json -o src/index.ts.tmp
{
  printf '// GENERATED — never hand-edit.\n'
  printf '// Source: apps/api Nest controllers + DTOs via openapi.json.\n'
  printf '// Regenerate: pnpm --filter @mealplan/contracts-api run gen. CI gate: pnpm --filter @mealplan/contracts-api run check.\n'
  cat src/index.ts.tmp
} > src/index.ts
rm -f src/index.ts.tmp
