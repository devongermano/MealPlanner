# @mealplan/contracts-api

GENERATED TypeScript types for the **NestJS API's HTTP surface** — never
hand-edited. The flow:

```
apps/api/src/**/*.controller.ts + dto/*.ts   Nest routes and DTOs (the source of truth)
        │  @nestjs/swagger reads the decorators off the REAL AppModule
        ▼
openapi.json                                 apps/api/scripts/dump-openapi.ts (checked in)
        │  npx openapi-typescript (pinned devDependency)
        ▼
src/index.ts                                 "GENERATED — never hand-edit." (checked in)
```

Consumed by `apps/web`. Produced by `apps/api`, which deliberately does **not**
depend on this package — the producer importing its own generated contract
would be a dependency cycle and would let a hand-edit here silently become the
API's idea of itself.

## Not the same package as `@mealplan/contracts`

| | `@mealplan/contracts` | `@mealplan/contracts-api` (this) |
|---|---|---|
| Mirrors | the Python engine's result models | the Nest API's request/response DTOs |
| Producer | `services/solver` pydantic schemas | `apps/api` controllers + DTOs |
| Answers | "what shape is a solve result?" | "what do I POST to create a household?" |

Both exist because they have different producers and change for different
reasons. A solver result travelling through the API is described by the first;
the envelope it travels in is described by the second.

## Commands

- `pnpm --filter @mealplan/contracts-api run gen` — regenerate both artifacts.
- `pnpm --filter @mealplan/contracts-api run check` — regenerate to a temp dir
  and diff against the checked-in copies; **nonzero exit on drift**. Run by
  `.github/workflows/scaffolds.yml` on every PR touching `apps/api` or this
  package.
- `pnpm --filter @mealplan/contracts-api run typecheck` — `tsc --noEmit`.

## Using it

```ts
import type { paths, components } from '@mealplan/contracts-api';

type HouseholdDetail = components['schemas']['HouseholdDetail'];
type CreateBody =
  paths['/households']['post']['requestBody']['content']['application/json'];
```

Every non-2xx response is `components['schemas']['ApiErrorResponse']` — one
envelope, everywhere. Switch on `error.code`, never on `error.message`.

## Ground truth

The document is produced by booting the real `AppModule` and reading the routes
Nest actually registered — it cannot describe an endpoint that does not exist.
If the generated types are wrong, the controller or DTO is wrong: fix it there,
regenerate, and commit both layers in one PR. Never edit `openapi.json` or
`src/index.ts` by hand.
