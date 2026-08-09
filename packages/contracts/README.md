# @mealplan/contracts

GENERATED TypeScript types mirroring the solver engine's result models — **never
hand-edited** (ARCHITECTURE.md "the one non-negotiable wiring rule"). The flow:

```
services/solver/mealplan/schemas.py   pydantic v2 mirrors of REAL engine results
        │  (imported by service.py — FastAPI app)
        ▼
openapi.json                          scripts/dump_openapi.py (checked in)
        │  npx openapi-typescript (pinned devDependency)
        ▼
src/index.ts                          "GENERATED — never hand-edit." (checked in)
```

Consumed by `apps/api` (NestJS) and `apps/web` (Angular) from M2.

## Commands

- `npm run gen` — regenerate both artifacts (`PYTHON` env var must point at a
  python with the `mealplan[service]` extra; from the repo root use
  `make contracts`, which passes the venv python).
- `npm run check` — regenerate to a temp dir and diff against the checked-in
  copies; **nonzero exit on drift**. CI runs this on every PR touching the
  schemas, the service, or this package (`.github/workflows/contracts.yml`).
- `npm run typecheck` — `tsc --noEmit` over the generated types.

## Ground truth

The pydantic models are STRUCTURAL MIRRORS of what the engine actually
returns; `services/solver/tests/test_contracts_roundtrip.py` runs the full
pipeline and validates the real outputs. A mismatch always means the schema
is wrong — fix `schemas.py`, run `make contracts`, commit all three layers in
one atomic PR. Never bend the engine to a schema, and never edit
`openapi.json` or `src/index.ts` by hand.
