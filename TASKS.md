# TASKS

Execution tracker for `PRD.md` v2.1. The PRD's §11 gates are the authority; this file
is the working checklist. Statuses: `[ ]` todo · `[~]` in progress · `[x]` done ·
`[?]` blocked (say on what). Line references are to the prototype as of commit
`4851dd1`; evidence for every defect is in `PRD-SCRUTINY.md`.

Later milestones stay coarse on purpose — they get expanded when the prior gate is
green, not before.

---

## M0 — Engine correctness *(current)*

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
- [ ] M0.14 Instrumentation: LP-solve counts and stage timings; record baselines on reference machine (PRD §8.5)
- [ ] M0.15 Synthetic fixture households + libraries in `tests/fixtures/` (single person; several-person; conflicting exclusions; extreme targets; discrete-unit edges) — founder household is NOT a fixture (PRD §9)
- [ ] M0.16 Capability test suite (PRD §9 names): composite-dish fat-forcing; binding-macro id; shelf-life valley → explained hole; variety caps don't starve late days; unit snapping in bounds; excluded tags never served; determinism golden (pinned reference env)
- [ ] M0.17 Dev-corpus cleanup: fix tolerance advice in `people.yaml:4-5` + `SKILL.md:106` (structural fix first, tolerance last — PRD §8.3); remove `[0,3]` code-default for `cook_days` (config-required); unify `n` defaults; move founder library to `examples/`; delete dead code (`assign_week`, unused imports)

## M1 — Headless demo loop

Gate: §3 demo from a fresh config file with format docs at hand; founder household
runs a real week on it (needs OQ-D1: real targets).

- [ ] M1.1 The three deliverables as human-readable artifacts: shopping list, per-session cook plan with scaled recipes, per-day per-person eat sheets
- [ ] M1.2 Relaxed mode: household-unit rendering + widened tolerance profile with honest error bars
- [ ] M1.3 Locked-plan artifacts: immutable, inputs-hash (incl. seed + pantry), keyed by primary-trip date; open-format export
- [ ] M1.4 n=1..4 exercised via fixtures; CLI `--json` contract versioned
- [ ] M1.5 Interactive-latency targets set from M0 baselines (provisional labels)
- [ ] M1.6 🥘 **Real-week gate:** founder household cooks and eats one week from M1 output

## M2 — Collaborative web app *(service posture begins)*

Gate: two-account household completes propose → veto → lock → deliver; eaters see
only the locked plan. Stack decided → `ARCHITECTURE.md` (NestJS/Angular/Supabase/
Render/Turbo). Blocked-on-decisions: OQ-P1 (channel), PR-2/PR-3.

- [ ] Contract-codegen pipeline: pydantic → OpenAPI/JSON Schema → generated `packages/contracts` TS, CI drift test — **blocks all other M2 API work**
- [ ] Solver service: FastAPI wrapper, stateless, Render-private (`ARCHITECTURE.md`)
- [ ] Service API (NestJS) wrapping solver; Supabase Auth (JWT verify in Nest); per-household isolation (authz in Nest, RLS safety net); transactional writes + audit trail; Postgres via Prisma (YAML stays interchange/fixture format)
- [ ] Household setup flow; propose → veto → lock loop; deliverable views; day rebalance; pantry stock UI; responsive
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
