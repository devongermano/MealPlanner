# Architecture — Decision Record

| | |
|---|---|
| **Status** | Decided by owner, 2026-08-09 (resolves PRD OQ-T1) |
| **Scope** | System topology for M2+; repo shape from M0. Engine internals: PRD §8. |
| **Rule of thumb** | Boring, mainstream, agent-maintainable. Few moving parts beat elegance. |

## The stack (owner-decided)

| Layer | Choice | Notes |
|---|---|---|
| Solver/engine | Python (PuLP/CBC), pure package | The asset. No I/O, deterministic, seeded |
| Solver service | FastAPI wrapper (M2+) | Dumb, stateless, private — see below |
| REST API | NestJS | Owns all state, auth, orchestration |
| Frontend | Angular | Plain `ng` CLI workspace |
| DB + Auth | Supabase (Postgres + GoTrue Auth) | Only those two parts — see matrix |
| Hosting | Render | API + private solver service + static web |
| Local dev | Docker Compose (+ `supabase start`) | M2+ only; M0/M1 need just a Python venv |
| Repo | Turborepo monorepo (pnpm workspaces) | Python folder = opaque Turbo tasks |

## Topology (M2+)

```
Angular (static, Render)          MCP / CLI --json (operator layer, M3)
        │                                   │
        └────────────┬──────────────────────┘
                     ▼
             NestJS API (Render)  ──── Supabase Auth (JWT verify)
              │            │
              ▼            ▼
   Python solver svc   Supabase Postgres (Prisma)
   (Render, private)   [RLS on as household_id safety net,
   engine-in, result-out    NOT as business logic]
```

- **One brain:** authorization, orchestration, and persistence live in NestJS.
  Angular talks only to the API (plus Supabase auth flows). Nothing else talks to
  the database.
- **One producer:** the Python engine is the sole producer of solver results. Nest
  stores and serves engine result objects verbatim, never re-derives solver math.
  This is PRD **P10 enforced by topology**, not discipline.

## The solver service stays dumb

FastAPI wrapper around the engine package. One responsibility: validated config in →
engine result object out. No database, no auth, no state, no outbound calls. Not
internet-facing (Render private service; only the API can reach it). Slow solves are
the API's problem to queue/serialize, mirroring the engine's own solve lock.

## The one non-negotiable wiring rule (decided now, built at M2 start)

**Nobody hand-writes the TypeScript mirror of engine types. Ever.**

Engine models (pydantic) → OpenAPI/JSON Schema emitted by the solver service →
codegen'd TS types in `packages/contracts` → consumed by both NestJS and Angular.
Regenerated in CI; a contract test fails the build on drift. This pipeline is the
first M2 task and **blocks all other M2 API work** — hand-mirrored types are how
P10 dies quietly across a language boundary.

## Supabase usage matrix

| Part | Use? | Why |
|---|---|---|
| Postgres | ✅ | It's managed Postgres; Nest connects via Prisma like any other |
| Auth (GoTrue) | ✅ | Signup/login/JWT solved; Nest verifies tokens. Not building auth |
| RLS | ⚠️ safety net only | Enabled with dumb `household_id` policies underneath Nest; never business logic |
| Realtime | ❌ for now | Veto window at beta scale is a polling problem, not websockets |
| Edge Functions | ❌ | Business logic stays in one brain (Nest) |

## Monorepo layout

```
mealplan/
  apps/web            # Angular
  apps/api            # NestJS
  services/solver     # Python: engine package + FastAPI wrapper + CLI  ← M0 starts here
  packages/contracts  # GENERATED TS types + JSON schemas — never hand-edited
  examples/           # founder household config (dev corpus; not shipped)
  docker-compose.yml  # M2+: solver + api + supabase local + web dev
```

Turbo + pnpm for the JS side; `services/solver` participates as opaque Turbo tasks
(`test`, `lint`, `codegen` wrapping `uv`/`pytest`) — Turbo caches task outputs without
understanding Python. A contract change is one atomic PR: schema + codegen + both
consumers. (Nx was considered for Angular-awareness; Turbo chosen for simplicity and
owner preference.)

## Deploy & cost (beta scale)

Render: API (Node starter ~$7/mo) + solver (Python private service ~$7/mo) + web
(static, free). Supabase free tier. **~$15–25/month** at M4 scale (tens of
households). Costs revisited when the free tier or starter dynos actually strain.

## What this means for M0/M1

Almost nothing — that's the virtue. The engine is built exactly as TASKS.md M0
describes, living at `services/solver/` from day one so no later move is needed.
No Docker, no Node, no Supabase until M2.

## Solver roadmap (recorded 2026-08-09; owner-reviewed analysis)

- **Core portioning LP:** stays PuLP-shaped. The measured cost is ~21ms/solve of
  CBC *subprocess transport* (BASELINES.md), not solver quality. The earmarked fix
  is **in-process HiGHS** (`highspy` — smallest dependency that removes exactly the
  transport overhead; SciPy's default LP engine). It is a pinned-reference-env
  change (goldens regenerate), so it lands at a milestone boundary as a deliberate
  migration, not a drive-by. OR-Tools was evaluated and rejected for this job:
  CP-SAT is integer-only (wrong shape for a continuous LP) and GLOP is just another
  LP inside a 10× heavier dependency.
- **Cook-day timeline scheduling (M1.12+):** greedy list scheduling first, on
  provisional durations. **OR-Tools CP-SAT is the earmarked escalation** once real
  cook days calibrate durations — the one problem in this product that matches its
  shape (interval variables, no-overlap cook attention, cumulative stations,
  temp-conditional oven capacity). Deterministic with fixed seed + single worker.
- The fuzzy problems (menu selection, meal dealing) stay greedy by design — their
  objectives are proxies; exact optimizers would be fake precision.

## Deferred wiring (decided at M2/M3 planning, not before)

- MCP server placement: Node SDK against the Nest API (parity-by-construction) vs
  Python alongside the engine (works offline/local). Leaning Node-against-API;
  local mode can keep the Python CLI.
- Notification channel (PRD OQ-P1: push/email/SMS) and its provider.
- Prisma vs alternatives if codegen friction appears (Prisma is the default).
- Queue/backpressure for solves if beta load ever makes the in-process lock visible.

## Provenance

Stack chosen by Devon (2026-08-09): "Plan solver in Py. Rest API in Nest, FE in
Angular… Supabase, and Render for hosting. Local dev can be Docker/Compose.
Monorepo preffed… I'd pref Turbo." Elaborations above (dumb solver service, contract
codegen, Supabase part-selection, RLS posture) are implementer design within that
decision, presented and approved same day.
