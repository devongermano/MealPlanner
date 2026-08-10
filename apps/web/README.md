# apps/web

Angular web app — the collaborative loop (household setup, propose → veto → lock →
deliver, day rebalance, pantry stock UI). That arrives at **M2 proper** per
ARCHITECTURE.md; what exists now is the scaffold (M2 pre-work): a plain `ng` CLI
workspace (Angular 22, standalone components, zoneless, vitest) consuming generated
types from `packages/contracts` and — from M2 — talking only to the NestJS API in
`apps/api`.

## What the scaffold contains

- App shell (`src/app/app.ts`) with a router and a lazy `/health` route.
- `src/app/health/` — feature-first folder, Angular 20+ style-guide filenames (no
  type suffixes). `health-data.ts` types the API `/healthz` response via the
  GENERATED contracts package (`components['schemas']['Healthz']`); the runtime
  call is mocked (`of(...)`) until M2 proper swaps in `HttpClient` — the contract
  type stays. Contract drift breaks `ng build` here: compile-time proof of
  consumption.
- Component tests (vitest + jsdom), angular-eslint flat config.

## Run dev

From the repo root (Node 22 + pnpm 9, see root `package.json` `packageManager`):

```sh
pnpm install
pnpm --filter @mealplan/web start   # ng serve on http://localhost:4200
```

Checks (also via `pnpm turbo run build test lint` at the root):

```sh
pnpm --filter @mealplan/web build
pnpm --filter @mealplan/web test    # vitest, single run
pnpm --filter @mealplan/web lint
```
