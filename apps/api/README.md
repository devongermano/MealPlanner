# apps/api

NestJS REST API — the "one brain": auth (Supabase JWT verify), authorization,
orchestration, and all persistence (Supabase Postgres via Prisma). Those arrive at
**M2 proper** per ARCHITECTURE.md; what exists now is the scaffold (M2 pre-work). It
stores and serves engine result objects verbatim from the private Python solver
service in `services/solver` — it never re-derives solver math (PRD P10).

## What the scaffold contains

- `GET /healthz` — liveness, shaped as the contracts `Healthz` type.
- `GET /contracts-probe` — returns a `WeekPlanResult`-typed fixture
  (`src/contracts-sample.ts`). This is compile-time proof that the app consumes the
  GENERATED `packages/contracts` types: contract drift breaks `nest build` here.
  The fixture is inert probe data, not engine output; it is removed when real
  solver orchestration lands.
- `src/config.ts` — env config with sane defaults (`PORT`=3000, `HOST`=0.0.0.0,
  `SOLVER_URL`=http://localhost:8000). Dependency-free on purpose.
- One supertest e2e suite (`test/app.e2e-spec.ts`), eslint flat config, multi-stage
  `Dockerfile` (build context = repo root).

## Run dev

From the repo root (Node 22 + pnpm 9, see root `package.json` `packageManager`):

```sh
pnpm install
pnpm --filter @mealplan/api start:dev   # watch mode on http://localhost:3000
```

Checks (also via `pnpm turbo run build test lint` at the root):

```sh
pnpm --filter @mealplan/api build
pnpm --filter @mealplan/api test    # e2e (supertest)
pnpm --filter @mealplan/api lint
```
