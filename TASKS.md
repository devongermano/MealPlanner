# TASKS

Execution tracker for `PRD.md` v2.1. The PRD's §11 gates are the authority; this file
is the working checklist. Statuses: `[ ]` todo · `[~]` in progress · `[x]` done ·
`[?]` blocked (say on what). Line references are to the prototype as of commit
`4851dd1`; evidence for every defect is in `PRD-SCRUTINY.md`.

Later milestones stay coarse on purpose — they get expanded when the prior gate is
green, not before.

---

## OPERATIONS BOARD *(live orchestration state — updated every landing; added 2026-08-09 when the fleet outgrew a flat checklist)*

**Tracking model:** this file is the durable truth; the session task board mirrors
it. Every workstream lands via branch → PR → CI gates → orchestrator review → merge.

### Workstreams

| Lane | Scope | Owner / tier | Branch | Status |
|---|---|---|---|---|
| Main chain | M1 phases + dish layer (engine files — single-writer, serialized) | Workflows (Fable) | main | M1.13 landed; M1.11 next |
| M1.12 | Timeline compiler v0 (VERIFIED PASS) | Workflow (Fable) | track-i-timeline | rebase onto main → PR |
| Track F | Data: DATA_GUIDE, dish reconstruction, corpus curation | `data-steward` (Opus, persistent) | track-f-data | ✅ stood down |
| Track G | API: Supabase auth, households, roles, contracts-api types | `api-steward` (Opus, persistent) | track-g-api | ✅ stood down |
| Track H | Web: shell, auth flows, onboarding, settings | `web-steward` (Opus, persistent) | track-h-web | ✅ stood down |
| Done | Track B contracts · Track C scaffolds · Track D USDA corpus · Track E method fragments · M1.9 meal layer · M1.13 dish layer | — | merged | ✅ |

### The frozen zone — LIFTED 2026-08-10

M1.13 landed: meal/dish result shapes (mealdays with dish identity, servings
scalars, flags) are now stable enough to build against. Consumers should read
the shape from the M1.13 golden (`tests/golden/solo_dishes_pipeline.json`)
and the dish-mode e2e tests, not from prose. Remaining churn risk: M1.6 may
ratify MEAL_BAND (flag semantics tighten, shapes stay).

### Steward roster (persistent, name-addressable — say "have the X-steward do Y")

- **data-steward** (Opus): corpus truth. Queue: dishes draft → DISH_REVIEW.md for
  owner → FDC lint cleanup (16 warnings) → household_units/affinity coverage.
- **api-steward** (Opus): apps/api. Queue: auth + households + authz matrix →
  contracts-api drift-gated types. Security gets a Fable review at PR.
- **web-steward** (Opus): apps/web. Queue: shell + auth + onboarding behind mock
  seams awaiting contracts-api.

### Owner inputs on the critical path

- 🧠 **Dish braindump** (answer key for DISH_REVIEW.md) — highest leverage
- 🥘 M1.6 real week (after sheets stabilize post-M1.13)
- PR-3/OQ-P1 (notifications) before Track G builds any notify path; OQ-N1 (name)

---

## M0 — Engine correctness *(✅ complete 2026-08-09 — commits ce8f3f8→bc9b306; gate evidence in bc9b306's message + PHASE5_FIXNOTES.md)*

Extract the engine from the prototype and make it tell the truth. Gate: full suite
green; same inputs + seed ⇒ byte-identical plan on the reference environment; no dead
config; one canonical computation per quantity; measured perf baselines recorded.

### Scaffold
- [x] M0.1 Monorepo skeleton per `ARCHITECTURE.md` (`services/solver/` is home); `pyproject.toml` with pinned deps (pyyaml, pulp, pytest); `make test`; package layout `mealplan/{model,engine,costing,io_yaml,units,cli}.py` extracted from `plan.py` (io_yaml not io — avoids shadowing stdlib); adapters import core only. *Verified: behavior preserved gram-for-gram vs prototype; 18 tests green.*
- [x] M0.2 Schema validation on load and before every write: structured all-errors reporting, atomic writes, `schema_version` on all documents (PRD §8.1). *Unit-alignment is a warning until M0.8 (examples `mango_jalapeno_wings` max 500 vs unit 45 — fix bound to 495 in M0.8).*

### Confirmed defect fixes (each lands with its regression test)
- [x] M0.3 Determinism: kill `hash(pname)` day-seed (`plan.py:537`); every random draw from an explicit seed; no wall clock
- [x] M0.4 Canonical batching: one session-attribution function (earliest session still within shelf life — PRD §8.2) feeding cook plan, minutes, purchasing, cost; deletes the `plan.py:577` vs `serve.py:101` fork
- [x] M0.5 Dead config, wire or delete: `--force` (parsed, unused — `plan.py:715`), `min_lean_anchors` (yaml says 1, code hardcodes 2 — `plan.py:396`), `meals_per_day` (→ presentation-level meal structure or drop), `min/max_components_per_day`, `batch_g`, `freezes` (→ live availability extension per PRD §8.1)
- [x] M0.6 Raw-freshness constraint: shopping trips as data; ingredient cookable in session *s* only within raw `keeps_days` of its trip, else frozen-with-thaw-note (PRD §8.2); enforced in menu search + diagnostics
- [x] M0.7 `edible_fraction` on ingredients; portion math weighs gross, macros apply to edible share (bone-in wings defect)
- [x] M0.8 Discrete snapping clamped into serve bounds; validation that `serve_g` min/max are `unit_g`-aligned; pinned portions clamped by default, warned when deliberately out of bounds (PRD §8.3)
- [x] M0.9 kcal: Atwater-only everywhere; drop ingredient-level label kcal (two-accounting defect); `spices`-style zero-kcal entries get honest values or an explicit `negligible` flag
- [x] M0.10 Menu-score scale consistency: waste, cost, and time all at estimated-batch scale (`plan.py:341` computed waste at 1 batch while cost used estimates)
- [x] M0.11 Diagnostics upgrades: binding-macro identification per person; volume-floor search (generic form of the reproduced 4.7 lb/day finding); real shelf-life-stagger check (replaces the boolean no-op at `plan.py:397-398`); starch/carb headroom derived from availability math, any residual heuristic labeled `provisional`
- [x] M0.12 Pantry: schema + empty-state semantics; purchasing deducts before pack rounding (PRD §8.1)
- [x] M0.13 Availability/replate day-correctness: day-constrained rebalance (v1 replate ignored `available_on`)

### Measurement & fixtures
- [x] M0.14 Instrumentation: LP-solve counts and stage timings; record baselines on reference machine (PRD §8.5)
- [x] M0.15 Synthetic fixture households + libraries in `tests/fixtures/` (single person; several-person; conflicting exclusions; extreme targets; discrete-unit edges) — founder household is NOT a fixture (PRD §9)
- [x] M0.16 Capability test suite (PRD §9 names): composite-dish fat-forcing; binding-macro id; shelf-life valley → explained hole; variety caps don't starve late days; unit snapping in bounds; excluded tags never served; determinism golden (pinned reference env)
- [x] M0.17 Dev-corpus cleanup: fix tolerance advice in `people.yaml:4-5` + `SKILL.md:106` (structural fix first, tolerance last — PRD §8.3); remove `[0,3]` code-default for `cook_days` (config-required); unify `n` defaults; move founder library to `examples/`; delete dead code (`assign_week`, unused imports)

## M1 — Headless demo loop *(in progress — M1.0-M1.5, M1.7-M1.9 done; M1.10 in flight)*

Gate: §3 demo from a fresh config file with format docs at hand; a real week cooked
and eaten from M1 output. Targets are arbitrary by design (owner, 2026-08-09:
"arbitrary goals for arbitrary people" — OQ-D1 dissolved; examples/ is demo data).

- [x] M1.0 Sweep the Phase 5 deferred minors (`services/solver/PHASE5_FIXNOTES.md`, "Deferred minors" section)
- [x] M1.1 The three deliverables as human-readable artifacts: shopping list, per-session cook plan with scaled recipes, per-day per-person eat sheets *(services/solver/mealplan/artifacts.py — rendering only, consumes engine/costing outputs verbatim; CLI `week`/`all --artifacts DIR`; seed+library+date footer on all three)*
- [x] M1.2 Relaxed mode: household-unit rendering + widened tolerance profile with honest error bars *(person.mode enum, RELAXED_TOLERANCE=0.12 provisional default, component.household_unit validated; counts / half-units / friendly batch fractions on eat sheets only — engine still solves grams; error bars are worst-case aggregation of the rounding deltas actually applied, property-tested)*
- [x] M1.3 Locked-plan artifacts: immutable, inputs-hash (incl. seed + pantry), keyed by primary-trip date; open-format export *(`mealplan lock --date D` writes plans/<primary-trip-date>/plan.yaml — verbatim inputs snapshot + sha256 over its canonical JSON, portions, session plan, empty veto_history reserved for M2 — plus the three M1.1 deliverables alongside; existing plan ⇒ refuse (exit 3) unless `--supersede` renames it byte-identically; `mealplan verify-plan` re-solves from the snapshot and checks hash + portions; mealplan/lockplan.py + serialize.py)*
- [x] M1.4 n=1..4 exercised via fixtures; CLI `--json` contract versioned *(every command emits one `mealplan/v2` JSON envelope; §8.4 exit codes 0/2/3/4 — 2 = computed-but-infeasible with misses in-document, 3 carries the all-errors issues list, argparse rewired off 2; n-ladder solo_lifter/conflicting_exclusions/trio_split (new)/family_four; minors: friendly `--n`-exceeds-library error naming both numbers, one-decimal eat-sheet macro totals)*
- [x] M1.5 Interactive-latency targets set from M0 baselines (provisional labels) *(BASELINES.md "Targets (provisional)": 2x headroom over recorded medians for single plate, replate, full pipeline, lock round trip; lock/verify-plan stages instrumented and measured by `make baseline`; never asserted in tests)*
- [ ] M1.6 🥘 **Real-week gate:** founder household cooks and eats one week from M1 output
- [x] M1.7 ~~Per-person-scalable `serve_g` bounds~~ **REVOKED same day** (lard-beans incident; PRD Appendix B item 2 — bounds are per-dish absolutes; mechanism dormant) (PRD §8.1, Appendix A confirmed defect "serve_g shared across divergent eaters"): **explicitly deferred from M0** in the Phase 5 review — `plate()` still applies one shared serve band per component to every eater (PRD Appendix B, item 2). Implement kcal-proportional scaling (provisional) or ratify dropping it, with the M1.6 real week as the measuring stick
- [x] M1.8 Pantry aging + cooked leftovers (PRD §8.1): consume stock `acquired` dates (age reduces effective raw `keeps_days` — validated but unconsumed in M0, PRD Appendix B item 3); integrate pantry `cooked` list into availability (documented M1+ since M0.12)
- [x] M1.9 Meal layer: post-solve dealer per `M19_SPEC.md` (judge-ratified) — meals_per_day LIVE, per-slot serving models, composed meals, conservation + inertness constitutional, strict-opt-in interchangeability *(22782be)*
- [x] M1.10 Sheet rework: serving-model phrasing, compiled cook script from method fragments, shared-prep consolidation, portioning matrix *(workflow in flight; golden regen ratified by orchestrator 2026-08-09 — feeds rows are P10 canonical attribution; additive-only diff verified. Ripple: `session_plan` sessions gain `feeds` rows → golden + packages/contracts regenerated; batches/minutes/portions/purchasing byte-identical; regen justification in tests/golden/README.md per PRD §9)*
- [x] M1.13 **THE DISH LAYER** (owner correction: "these aren't meals") — dishes.yaml assembly restored; meals = one dish portioned + sides; menu selects dishes. Landed 2026-08-10: skeleton-then-solve per M113_SPEC, heritage byte-inert, dish golden (`solo_dishes_pipeline.json`) + §13 instrumentation report in BASELINES.md; examples/dishes.yaml synced from the re-banded draft (lint-silent); jimbo named slots (#28)
- [ ] M1.11 Target profiles: day-type cycling anchored to plan date *(next on main chain)*
- [ ] M1.12 Timeline compiler v0: greedy interleaved cook schedule with timers, station buckets, cook_plan_style: timeline — **VERIFIED PASS** on track-i-timeline; held for M1.13 ordering, now: rebase → PR → merge

## M2 — Collaborative web app *(service posture begins)*

Gate: two-account household completes propose → veto → lock → deliver; eaters see
only the locked plan. Stack decided → `ARCHITECTURE.md` (NestJS/Angular/Supabase/
Render/Turbo). Blocked-on-decisions: OQ-P1 (channel), PR-2/PR-3.

- [ ] Contract-codegen pipeline: pydantic → OpenAPI/JSON Schema → generated `packages/contracts` TS, CI drift test — **blocks all other M2 API work**
- [ ] Solver service: FastAPI wrapper, stateless, Render-private (`ARCHITECTURE.md`)
- [~] **Auth/households/roles pulled forward** (PR-2 ratified 2026-08-09): Supabase Auth JWT verify in Nest, Prisma households/members/roles + RLS safety net, authz matrix tests, contracts-api types — **Track G in flight**. Solve-wrapping API stays gated on the frozen zone
- [~] **Shell/auth/onboarding pulled forward**: responsive shell, supabase-js auth flows, onboarding wizard behind contracts-api mock seams — **Track H in flight**. Plan/eat views stay frozen-zone
- [ ] Service API (NestJS) wrapping solver; transactional writes + audit trail; Postgres via Prisma (YAML stays interchange/fixture format) — after frozen zone lifts
- [ ] Household setup flow; propose → veto → lock loop; deliverable views; day rebalance; pantry stock UI; responsive/PWA
- [ ] Cook mode: full-screen tap-next timeline renderer (PRD §10) — timers, wake lock, offline locked plan; technique-library hooks (operation → explanation/video)
- [ ] Browser-automation test of the two-account loop (dev dependency, outside pinned core)

## M3 — Operator layer & ingestion assist

Gate: Claude runs the demo end-to-end and adds a recipe from pasted text without
corrupting a library; cross-surface contract tests green.

- [ ] MCP tools at capability parity (full-object writes only, no partial patches)
- [ ] Recipe-format conversion protocol (Claude-assisted — PR-5); operator instructions (successor to v1 `SKILL.md`)
- [ ] Cross-surface contract tests: CLI/HTTP/MCP same engine objects

## M4 — Beta

Gate: 3+ external households each cook and eat ≥1 real week.

- [ ] Rest-of-week replan (PRD §4.4)
- [ ] Manual feedback capture; findings scoped into post-M4
- [ ] Seed-content decision executed (OQ-C1); barefoodtim-derived corpus stays quarantined

## Post-M4 (sequenced)

Coach mode (PR-6 first candidate) → budget optimization → ingestion automation →
actuals capture → payments.

---

## Pending owner decisions

- PR-1..PR-6 ratifications (PRD §12.2) — none block M0
- OQ-D1 (founder real targets) — blocks M1.6 only
- OQ-T1/OQ-P1 — block M2 planning
