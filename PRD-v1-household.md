# mealplan — PRD & Build Document

| | |
|---|---|
| **Version** | 1.0 (2026-08-05) |
| **Owner** | Devon (product, ground truth, library curation) |
| **Implementer** | Claude Code, working in this repo |
| **Status of this repo** | Working prototype. Treat it as the *reference implementation*, not scaffolding — the solver behavior encoded here was validated empirically this week and the build must preserve it. |
| **Prime directive** | Turn the prototype into a durable application whose first-class operator is Claude (via MCP, CLI, and skill), with Devon and Jimbo as the humans it serves. |

---

## 0. Summary

`mealplan` is a constraint-based batch-cooking planner for a two-person household with
wildly asymmetric macro targets (~4,700 kcal/day vs ~2,900 kcal/day), disjoint food
exclusions (soy/peanut/garbanzo vs dairy), a shared grocery budget, real shelf-life
physics, and a hard requirement that the food be *good* — the anti-"chicken, rice,
green beans" system. It is modeled on the component-cooking philosophy of Tim Laielli
(@barefoodtim): cook flavorful components in batches, then each person self-assembles
plates day by day and logs macros by weight.

The core insight, proven in prototype: this is **two different problems** —

1. **Which components to cook this week** — small, combinatorial, fuzzy objective
   (shared perishables, variety, cook time, budget). Solved with greedy + local search
   over a cheap structural score, then LP-verified.
2. **How many grams of what, to whom, on which day** — continuous, exact. A linear
   program. Heuristics demonstrably fail here because composite dishes weld macros
   together at fixed ratios.

The build target is: one Python core engine, four thin adapters (CLI, HTTP+web UI,
MCP server, Claude skill), YAML files as the database, tests that lock in the
domain findings below, and a recipe-ingestion workflow so the library compounds
over months.

---

## 1. Context

### 1.1 The household

Two people, one kitchen, two ~90-minute intended cook sessions per week (see §5.8
for why that budget is currently fictional), shopping done by Devon in one trip.

| | jimbo | devon |
|---|---|---|
| Daily targets (g) | 235p / 157f / 588c (≈4,705 kcal) | **PLACEHOLDER** 180p / 95f / 320c (≈2,855 kcal) |
| Meals/day | 2 | 3 |
| Hard exclusions | soy, peanut, garbanzo | dairy |
| Tolerance | ±5% per macro | ±7% per macro |

> ⚠️ Devon's targets in `library/people.yaml` are invented placeholders. Every
> downstream number (cost split, volume floor, feasibility) shifts when the real
> ones land. This is **Open Question OQ-1** and it blocks nothing structurally,
> but the shipped defaults must be clearly marked placeholder until replaced.

There may be a third eater ("Gemma" appeared once in dictation — possibly a
transcription slip for Jimbo). **OQ-2.** The data model already supports N people;
the build must not bake in two-person assumptions anywhere (UI person tabs, budget
splits, attribution all iterate over `people.yaml`).

### 1.2 Why generic meal-prep tools fail here

- Macro trackers assume separated foods; this household eats **composite dishes**
  (a burrito is protein+fat+carb at a fixed ratio). The standard heuristic — "set
  protein, set fat, float the starch" — mathematically requires separable
  components and collapses on real food. This is *why the LP exists*.
- Jimbo's constraint profile is inverted from the norm: at 4,700 kcal the binding
  constraint is **carbohydrate mass**, not calories or fat (§5.2). Tools built for
  cutting diets have no concept of this.
- Allergen handling must distinguish **structural** ingredients (cheese *in* nachos —
  dish is out for Devon) from **accent** ingredients (cheese *on* a burrito — Devon
  gets 0g). §4-I3 is the data-model answer; no variant machinery exists or is wanted.
- Waste optimization requires purchase-unit awareness (you cannot buy 37g of
  cilantro) and shelf-life-vs-cook-day scheduling (§5.4).

### 1.3 What exists today (prototype inventory)

| File | Role | State |
|---|---|---|
| `plan.py` | Solver + CLI (`doctor`, `menu`, `week`, `shop`, `all`, `frontier`) | Working, validated. ~6s full pipeline. |
| `serve.py` | Localhost HTTP server (stdlib only), real solver behind every call | Working. Endpoints: `POST /api/plan`, `POST /api/replate`, `GET /api/frontier`, `GET /api/library`. |
| `app.html` | Single-file web UI, 3 tabs (Plan / Eat / Shop & Cook), dark+light, validated palette | Working. Screenshotted and reviewed. |
| `library/ingredients.yaml` | ~70 ingredients: per-100g macros, allergen tags, pack sizes, prices, perishability, shelf life | Seeded. **Prices are estimates** (OQ-3). |
| `library/components.yaml` | 26 components across 4 cuisines, macros derived from ingredients | Seeded from @barefoodtim free posts + gap-filling house recipes. |
| `library/people.yaml` | People, targets, exclusions, settings, budget policy | Working; Devon targets placeholder. |
| `SKILL.md` | Claude operating instructions v1 | Needs v2 rewrite against the new interfaces (§9.4). |

Known prototype debt (fix in M0/M1, see §13): monolithic `plan.py`; no tests; no
schema validation on YAML; server holds no state but rereads YAML per request
(fine, keep); `history`/`pantry` not yet implemented; recipe method text not stored.

---

## 2. Goals / Non-goals

### Goals (v1.0)

1. **G1 — Claude-operable end to end.** Every capability reachable via MCP tools and
   via CLI with `--json`, so any Claude surface (Claude Code in-repo, Claude Desktop
   via MCP, Cowork via device bridge running CLI) can drive it without screen-scraping.
2. **G2 — Preserve validated solver behavior** under a test harness (§12) before any
   refactor. The findings in §5 become named regression tests.
3. **G3 — Week-over-week machinery**: pantry carryover, menu history with repeat
   penalties, dislikes-vs-excludes, plan persistence.
4. **G4 — Output layer**: scaled recipe cards with method text, shopping list in real
   store units, macro-tracker export (per-100g custom foods).
5. **G5 — Library compounding**: a recipe-ingestion protocol (Claude-driven) with
   validation, USDA-assisted macro lookup, and yield calibration, so adding a dish
   found on TikTok is a 2-minute operation that cannot corrupt the library.
6. **G6 — Honest accounting**: cook time modeled per-batch (§5.8), budget as policy
   not hardcode, cost attribution by consumption, waste surfaced never hidden.

### Non-goals (v1.0)

- Multi-week global optimization (each week solves independently; history informs, doesn't constrain).
- Micronutrients (fiber/sodium/etc.). Candidate v1.1: fiber floor per person.
- Cloud anything: no accounts, no sync, no deploy. Localhost + files + git.
- Grocery-delivery/store APIs (Instacart etc.). v2 candidate.
- Native mobile app. The web UI must be responsive; that suffices.
- Photo/vision food logging.

---

## 3. Users & agents

| Actor | Interface | Needs |
|---|---|---|
| **Devon** | Web UI, and Claude conversationally | Plan the week, swap dishes, shop from phone-glanceable list, log macros by weight, add recipes he finds. |
| **Jimbo** | Web UI (Eat tab) | See his day, self-assemble to his macros, nudge portions, never touch config. |
| **Claude (operator)** | MCP tools, CLI `--json`, skill | Generate/critique plans, ingest recipes, run diagnostics, explain infeasibility in plain language, edit library files safely. |
| **Claude Code (builder)** | This repo | Implement per this PRD; keep §4 invariants; extend tests first. |

---

## 4. Design constitution — invariants

These are load-bearing decisions with empirical or architectural justification.
Violating any of them requires updating this PRD first, with rationale.

- **I1 — Two-problem decomposition.** Menu selection = search over a cheap structural
  score, LP-verify the shortlist. Portioning = LP. Never merge them: the full-week
  MILP (days × people × batch integers × variety binaries) **timed out at 2 minutes**;
  the decomposition solves in ~6 seconds with equivalent quality (§5.7).
- **I2 — Macros are derived, never hand-entered.** A component is an ingredient list
  in grams + a cooked `yield_g`. Per-100g macros are computed. If macros look wrong,
  the ingredient list or yield is wrong. No override field. Ever.
- **I3 — Accents are their own components; structural allergens live inside the
  component.** Burrito = scramble + tortilla + cheddar(accent) → Devon gets 0g cheddar.
  Nachos = one component tagged `dairy` → solver never serves it to Devon. There is no
  variant system; the data model is the answer. Cooking corollary: push exclusions to
  the finishing step so one batch stays universal until plating.
- **I4 — Hard vs soft preferences never merge.** `exclude` (allergen tags) is
  infeasibility-hard. `dislikes` (component ids) is a soft objective weight (currently
  6×). "Not in the mood" must never be encoded as "can't eat."
- **I5 — Palatability bounds are load-bearing.** Every component carries
  `serve_g: {min,max}`; discrete items carry `unit_g` and snap to whole units
  (two-pass: solve continuous → snap discrete → re-solve rest). Without bounds the LP
  prescribes 750g of tortilla (11 tortillas). REG-03.
- **I6 — Every infeasibility is explained, directionally.** Elastic constraints report
  `p 12g SHORT` vs `f forced 9g OVER` — these are opposite problems with opposite
  fixes. A bare "INFEASIBLE" is a bug. The `doctor` command is the product's honesty
  organ; it runs real ablations, not vibe checks.
- **I7 — Grams internally, store units at the boundary.** All solver math in grams.
  Shopping lists render packs ("7 × 32 oz"). Purchase rounding to `pack_g` happens in
  exactly one function.
- **I8 — YAML files are the database.** Human-diffable, git-versioned,
  Claude-editable. No SQLite/ORM. Every write is schema-validated first (M1). Plans
  persist as dated YAML artifacts.
- **I9 — Paywall policy.** Recipe ingestion never circumvents paywalls or robots.txt.
  If a source is paywalled, ask Devon to paste the text. (Established behavior;
  keep it.)
- **I10 — No silent caps or hidden drops.** If the solver relaxes something (variety
  cap ladder, tolerance), the output says so. If a menu can't satisfy someone, name
  who and by how much.
- **I11 — Stated-vs-derived separation in reporting.** Ground truth (targets,
  exclusions, prices Devon supplies) is authoritative input; derived numbers carry
  their assumptions (e.g., "at estimated prices").

---

## 5. Empirical findings the build must preserve

Each finding below is a named regression scenario (§12.3). These were discovered by
running the solver, not by reasoning, and several contradict intuition — including
intuitions this project's own authors had earlier in the week.

### 5.1 Composite dishes force the LP
With any composite-dish library, protein/fat/carb cannot be tuned independently.
At target 200p/90f with an all-fatty-protein menu, fat is **forced 24g over** just
to reach protein. Heuristics produce this failure silently; the LP names it. (REG-01)

### 5.2 Jimbo's binding constraint is carbohydrate, not fat
588g carbs/day must be physically chewed. Fat density does nothing for it. To
deliver 588g of carbs: flour tortillas 1,153g of food; cooked fusilli 1,734g;
jasmine rice 2,205g; refried pintos 3,108g. **Same carbs, 4.4 lb/day swing** on
starch choice (cooked rice is ~72% water; tortillas ~30%). Menu rule derived:
≥3 starches eligible for him, and carb ceiling headroom
`Σ(serve_max × carb density) ≥ 1.45 × carb target`. (REG-02)

### 5.3 Lean anchors: at least two, staggered shelf life
With only rich mains there is no way to add protein without overshooting fat.
One lean anchor is insufficient because of §5.4: if the only lean protein keeps
3 days, the day before cook-day-2 has no lean protein and goes infeasible.
Rule: ≥2 lean-anchor mains, not all expiring together. `doctor` proves the
current requirement by ablation (strip mains leanest-first until each person
breaks). (REG-04)

### 5.4 Shelf-life valleys around cook days
Cook days `[0,3]` (Sun/Wed) strand day 7; `[0,4]` (Sun/Thu) works with 3–5-day
components. Availability rule: component cooked in session s is edible on day d
iff `0 ≤ d − start(s) < keeps_days`. Empty days must render as an explained hole
("past shelf life by day 7: shrimp, guacamole…"), never as a silent zero row. (REG-05)

### 5.5 Variety caps belong on mains, not starches
Capping starch repetition starves the back half of the week (days 5–7 went empty).
Nobody gets bored of rice. Cap mains/sauces at `max_days_same_component`; exempt
starches; relax via ladder (strict → +1 → uncapped) rather than emit an empty day. (REG-06)

### 5.6 Budget doesn't bind — and waste rises as budget falls
Sweeping the ceiling $200→$650: actual spend stays $264–$338. Structural floor
≈ $264/wk (below it, macros+exclusions+variety are unsatisfiable); plateau ≈ $340
(above it, money buys nothing). Perishable waste at $200 ceiling: 2,988g; at $320:
2,188g — **cheap menus lean on bulk packs and throw more away.** The product's
budget story is therefore a *frontier*, not a number. Cost attribution: Jimbo eats
~62% of calories, ~57% of cost (bulk = cheap calories). Leftovers split by
consumption share — both paid for them.

### 5.7 Performance envelope (hard requirements)
- Full-week MILP: **forbidden** (timeout >120s observed). Decompose per I1.
- Day-plate LP: milliseconds. Full pipeline (`menu` + week + costing): ≈6s.
- UI solve round-trip budget: **p95 < 10s** on an M-series Mac; debounce dials ~400ms.
- CBC is not reentrant: serialize solves behind a lock (present in `serve.py`; keep).
- Determinism: seed all randomness; CBC version pinned in lockfile; golden tests
  assert within tolerance bands, not exact grams.

### 5.8 Cook time scales with batches, and the honest number is ~2× the wish
45 batches/week across 12 components. Counting each recipe once said "172 min";
modeling marginal batches at `batch_time_factor: 0.45` of the first says
**≈5h44m/week (two ~2h50m sessions)** vs the stated 3h budget. The number is
configurable and calibratable (§11.6); the UI flags overruns rather than hiding
them. Session totals = sum of per-session batch counts (fixed prototype bug:
components were double-listed in both sessions; batches now split by which days
they feed).

### 5.9 The volume floor is real and only liquids move it
Binary search over `max_daily_mass_g` with the full library: Jimbo's floor
≈ **2,121 g/day (4.7 lb)**; Devon's ≈ 1,215g. A 2,000g cap for Jimbo is infeasible
every day of the week. Dense carbs are the biggest in-library lever (§5.2);
below ~4.7 lb the only remaining lever is **liquid calories** (juice, shakes with
oil, honey in coffee) — deliberately absent from v0, specced in §11.5.

---

## 6. Architecture

### 6.1 One core, thin adapters

```
                    ┌───────────────────────────────┐
                    │        core engine            │
                    │  mealplan/ (importable pkg)   │
                    │  pure functions over dataclasses │
                    └──────┬──────┬──────┬──────┬───┘
                           │      │      │      │
                    CLI (plan) HTTP+UI  MCP    skill
                    --json    serve.py server  SKILL.md
                                       (stdio)
```

- **Core** (`mealplan/` package): `model.py` (schemas/dataclasses + validation),
  `engine.py` (plate LP, doctor, menu search, week builder), `costing.py`
  (purchase, attribution, frontier), `io_yaml.py` (load/save with schema checks),
  `timefmt.py`/`units.py` (human pack sizes, minutes). No adapter imports another
  adapter; all import core only.
- **Adapters** contain zero solver logic. If an adapter needs a computation, it
  moves into core.

### 6.2 Repo layout (target)

```
mealplan/
  mealplan/              # core package (M1 extracts from plan.py)
    __init__.py  model.py  engine.py  costing.py  io_yaml.py  units.py
  adapters/
    cli.py               # `mealplan` console entry; every command has --json
    server.py            # stdlib HTTP; serves web/app.html + /api/*
    mcp_server.py        # stdio MCP; tools in §9.3
  web/
    app.html             # single-file UI (keep single-file; it's a feature)
  library/
    ingredients.yaml  components.yaml  people.yaml
    pantry.yaml          # NEW §7.4
    history.yaml         # NEW §7.5
  plans/                 # NEW — dated plan artifacts, git-tracked
    2026-W32.yaml
  tests/
    test_invariants.py  test_regressions.py  test_engine.py
    test_api.py  test_cli_json.py  golden/
  SKILL.md               # v2, §9.4
  PRD.md                 # this document
  pyproject.toml         # pinned deps: pyyaml, pulp (CBC), pytest; nothing else in core
```

### 6.3 Why YAML-as-database (I8)

Claude is a first-class operator (G1). Files Claude can read, diff, edit, and git-
revert beat any binary store for this use. Concurrency needs are one household;
the server rereads on each request (measured: negligible vs solve time) and writes
go through validated save functions. Git history doubles as the audit log Devon
already keeps for everything else in his life.

### 6.4 Failure & concurrency posture

- One solve at a time (lock). Queued requests fine; UI disables Re-solve while pending.
- YAML schema violation → refuse write, return structured errors (never partially write).
- Solver infeasible → HTTP 200 with `feasible:false` + directional misses (it's a
  *result*, not an error); engine exceptions → 500 with traceback in dev mode.
- All library writes atomic (write temp + rename), preceded by validation, followed
  by an automatic `doctor` diff summary in the response.

---

## 7. Data model

All schemas validated on load AND before every write (M1). Field tables list only
semantics that aren't obvious; the prototype files are the concrete examples.

### 7.1 `library/ingredients.yaml`

Per-100g macros for raw/as-purchased ingredients, plus how the store sells them.

| Field | Type | Semantics |
|---|---|---|
| `kcal,p,f,c` | float | per 100g, as purchased |
| `tags` | list | allergen/exclusion tags (`dairy`, `soy`, `peanut`, `garbanzo`, `wheat`, `sesame`, `treenut`, `fish`). Component tags are the UNION of ingredient tags — derived, never declared on components. |
| `perishable` | bool | false ⇒ exempt from waste optimization and shared-ingredient constraint ("seasonings are free") |
| `pack_g` | int | purchase unit. Purchasing = `ceil(need/pack_g)` in exactly one function |
| `keeps_days` | int | fridge life from purchase; drives what can share a week |
| `cost` | float | $ per pack. **Estimates until receipt calibration (OQ-3/§11.7)** |
| `usda_fdc_id` | int? | NEW, optional provenance for macro values |
| `liquid` | bool? | NEW §11.5; enables `mass_factor` default on components using it |

### 7.2 `library/components.yaml`

A component = one thing cooked in a batch, later weighed onto plates.

| Field | Type | Semantics |
|---|---|---|
| `id,name,cuisine` | str | id is stable snake_case; renames require a migration note in the plan history |
| `role` | enum | `main \| starch \| veg \| accent \| drink`(NEW) |
| `anchor` | enum? | `lean` marks low-fat protein mains; doctor's ablation consumes this |
| `ingredients` | map | ingredient_id → grams (raw). THE source of macros (I2) |
| `yield_g` | int | cooked weight of one batch. Dominant error source → calibration flow §11.6 |
| `serve_g.min/max` | int | palatability bounds (I5) |
| `unit_g` | int? | discrete unit (tortilla 71g, meatball 40g); portions snap to multiples |
| `batch_g` | int | portioning granularity when cooking |
| `keeps_days` | int | after cooking |
| `freezes` | bool | future lever; v1 records it, week builder ignores it |
| `active_min` | int | hands-on minutes for FIRST batch; extra batches cost `batch_time_factor` × this (§5.8) |
| `mass_factor` | float=1.0 | NEW; effective mass = grams × factor for `max_daily_mass_g` (drinks ≈0.3) §11.5 |
| `method` | list[str]? | NEW §11.3 — terse imperative steps; scaled quantities rendered per batch count |
| `source` | str | provenance ("barefoodtim 013 — soy swapped", "house", "store") |

### 7.3 `library/people.yaml`

As today: per-person `targets{protein,fat,carb}` (grams/day — kcal always derived),
`tolerance`, `meals_per_day`, `min/max_components_per_day`, `exclude` (tags, hard),
`dislikes` (component ids, soft ×6 weight), `max_daily_mass_g` (nullable).

`settings`: `days`, `cook_days` (list of 0-indexed day starts, 1–3 sessions),
`max_days_same_component` (mains only, I5.5), `max_batches_per_component`,
`active_min_budget`, `batch_time_factor`, `min_lean_anchors`.

`budget`: `mode: shared|per_person|by_consumption|off`, `total`, `per_person{}`.
Never hardcoded in engine; CLI/API can override per run.

### 7.4 `library/pantry.yaml` — NEW (M2)

What's on hand before shopping.

```yaml
pantry:
  - ingredient: white_rice_dry
    grams: 1400
    acquired: 2026-08-03        # effective keeps = keeps_days - age
  - component: birria_chuck     # cooked leftovers can carry too
    grams: 400
    cooked: 2026-08-02
```

Semantics: `purchase()` deducts pantry stock before computing packs. Pantry stock
enters menu costing at $0 marginal (already paid) so the optimizer *naturally
prefers burning it* — no special "use up leftovers" logic. Cooked-component
entries join the week with their remaining shelf life. After a plan is accepted,
predicted leftovers are written back as pantry candidates (user confirms).

### 7.5 `library/history.yaml` — NEW (M2)

```yaml
weeks:
  - week: 2026-W31
    menu: [birria_chuck, cilantro_lime_rice, ...]
```

Repeat penalty in menu scoring: for each candidate main appearing in history,
add `w_repeat × decay^(weeks_ago)` (defaults: w=1200, decay=0.5, horizon 4 weeks;
mains and accents only — starches exempt, same philosophy as §5.5). Pinning a
dish overrides the penalty — "unless we really want to" is a pin, not a config.

### 7.6 `plans/<ISO-week>.yaml` — NEW (M2)

Frozen artifact of an accepted plan: inputs hash (library file hashes + overrides),
chosen menu, per-person per-day portions, batches per session, shopping list with
per-item cost, computed totals. Reproducible (same inputs hash ⇒ same plan given
pinned solver version). Accepting a plan appends to history and stages pantry
writeback. The UI's shopping checkboxes are ephemeral; the artifact is not.

---

## 8. Core engine — functional spec

Function signatures are indicative; keep the prototype's proven logic, extracted
and typed.

### 8.1 Load & derive
`load() -> (Ingredients, Components, People, Settings)`. Validates schemas,
derives per-100g macros + tags per component (I2/I3), rejects unknown ingredient
refs with exact locations. Load failure = structured error listing every problem,
not first-error-wins.

### 8.2 Plate LP — `plate(person, comps, ids, *, weights, tol, locked, day_mass_cap)`
One person × one day. Decision vars: grams per eligible component (+ on/off binary
enforcing serve min/max). Elastic macro bands at ±tolerance with slack vars;
objective = 10,000×Σslack + tiny weighted-grams tiebreak (variety randomization
enters through `weights`). `locked{id:grams}` pins portions (Eat-tab rebalance).
Mass cap uses `Σ grams×mass_factor`. Discrete handling: solve → snap `unit_g`
components → re-solve continuous rest (two-pass, I5). Returns `(ok, portions,
misses)` where misses are signed (+forced-over / −short, I6).

### 8.3 Doctor — `doctor(comps, people, settings) -> Report`
(a) Full-library feasibility per person with directional misses and a tolerance
ladder (would it clear at ±8/10/15%?). (b) Ablations: strip mains leanest-first
(by p/f ratio) until each person breaks → "needs ≥K lean-ish mains"; count
starches; carb-ceiling check vs 1.45× (§5.2); lean anchors' shelf-life stagger
(§5.3). (c) Every failure names the *class* of missing component. Doctor is run
automatically after any library write and its diff is included in the response.

### 8.4 Menu search — `choose_menu(..., n, seed, must, exclude) -> (menu, info, feasible, broke)`
Phase 1: 6 random restarts × local search (swap moves) on the **cheap score** —
no LP inside the loop. Score terms: perishable-waste estimate, active minutes via
`cook_minutes(estimate_batches(...))` (§5.8 — batches, not recipes), cuisine
variety reward, role minimums per person (≥3 mains, ≥3 starches, ≥2 accents
eligible), lean-anchor count ≥2 with p/f ratio vs `1.25 × person p/f need`,
carb-ceiling ≥1.45×, budget ceiling penalty + mild spend preference, history
repeat penalty (§7.5), dislikes weight. Phase 2: LP-verify shortlist best-first;
return first fully-feasible menu, else best candidate + who broke and how (I10).
`must` (pins) are unswappable; `exclude` removed pre-search.

### 8.5 Week builder — `build_week(comps, people, settings, menu, pantry)`
Day-by-day per person. Availability per §5.4 (cook-day sessions + keeps_days,
pantry cooked items join with residual life). Candidate plates: k diverse LP
solves (randomized weights, seeded); pick min Σ(prior-use²) for variety. Caps:
mains-only day cap with relax ladder (§5.5), weekly gram cap per component
(`yield_g × max_batches`). Empty day ⇒ explained hole (REG-05). Output feeds
demand → batches.

### 8.6 Batches, sessions, cook time
Per-session demand = Σ portions on days that session feeds. Batches per session =
`ceil(session_demand / yield_g)`. `cook_minutes(batches) = Σ active_min ×
(1 + f×(b−1))`, f = `batch_time_factor`. Report per-session and total; flag vs
`active_min_budget` (never silently fit).

### 8.7 Purchasing & costing
`purchase(comps, ing, chosen, batches, pantry)` → rows (need, packs, pack_h
human unit, leftover, perishable, keeps, cost) after pantry deduction, rounding
in this one place (I7). `menu_cost` for search estimates uses
`estimate_batches` (calorie-demand-scaled — REQUIRED; the constant-batch version
produced a flat fake frontier). `attribute(weeks, bought)` splits actual spend by
consumption share incl. leftovers. `frontier(lo,hi,step)` sweeps shared ceiling →
points{budget, spend, dishes, cuisines, waste, feasible}.

### 8.8 Determinism & limits
Everything seeded; no wall-clock reads inside engine. Guards: menu search
≤ ~10k cheap evaluations; LP count per full pipeline ≤ ~400; document both.

---

## 9. Interfaces

### 9.1 CLI (`adapters/cli.py`, console entry `mealplan`)

Every command supports `--json` (machine mode): stdout is a single JSON document,
all logs to stderr. Exit codes: `0` ok (including feasible plans), `2` result
computed but infeasible (JSON carries directional misses), `3` schema/validation
error, `4` bad arguments.

| Command | Purpose | Key flags |
|---|---|---|
| `mealplan doctor` | Feasibility + ablation report | `--json` |
| `mealplan menu` | Choose the week's components | `--n --seed --force a,b --exclude c,d --budget 550\|devon=320,jimbo=240 --json` |
| `mealplan week` | Full plan from a menu | `--menu a,b,c` or search; same overrides; `--json --out plan.md` |
| `mealplan shop` | Shopping list only | inherits; `--json` |
| `mealplan frontier` | Budget sweep | `--range lo:hi:step --json` |
| `mealplan accept` | Freeze current solve → `plans/<week>.yaml`, append history, stage pantry writeback | `--week 2026-W32` |
| `mealplan pantry` | `list\|add\|consume` on-hand stock | `--json` |
| `mealplan add-ingredient` / `add-component` | Validated library writes; runs doctor diff | `--file x.yaml\|--stdin --json` |
| `mealplan calibrate <component>` | Update `yield_g` from an actual cooked weight | `--cooked-weight 1350` |
| `mealplan validate` | Schema-check the whole library | `--json` |

The `--json` contract is the compatibility surface for Cowork/device-bridge use;
shapes are versioned (`"schema": "mealplan/v1"`) and tested (§12.4).

### 9.2 HTTP API (`adapters/server.py`, localhost only)

Existing endpoints stay; additions marked NEW. All POST bodies accept the same
override block: `{budget, mass{}, targets{}, tolerance{}, dislikes{}, exclude[], force[], n, seed}`.

| Endpoint | Purpose |
|---|---|
| `POST /api/plan` | Full solve. Returns menu, per-person weeks, cook (with `per_session`), shop, cost{bought,eaten,ceiling,shares}, waste, volume, `cook_minutes`, `session_minutes`, `active_budget`, feasible+broke. |
| `POST /api/replate` | One person, pinned portions → re-solved day (Eat-tab Rebalance). |
| `GET /api/frontier?lo&hi&step&n` | Budget sweep points. |
| `GET /api/library` | Components + derived macros + costs. |
| `POST /api/library/component` NEW | Validated add/update; response includes doctor diff. |
| `POST /api/accept` NEW | Persist plan artifact + history + pantry writeback prompt. |
| `GET/POST /api/pantry` NEW | Read/write stock. |
| `GET /api/plan/current` NEW | Last accepted artifact for this ISO week, if any. |

Serialize solves behind the existing lock. No auth (localhost bind only); never
bind 0.0.0.0 by default.

### 9.3 MCP server (`adapters/mcp_server.py`, stdio) — the Claude-native interface

Registered in Claude Desktop / Claude Code as `mealplan`. Tools (JSON Schema for
each input; all responses are the same shapes as §9.1 `--json`):

| Tool | Input (abridged) | Behavior |
|---|---|---|
| `plan_week` | `{n?, seed?, budget?, force?, exclude?, mass?}` | Full solve; returns plan JSON. |
| `replate_day` | `{person, menu[], locked{}}` | Re-solve one day around pinned grams. |
| `run_doctor` | `{}` | Diagnostics with directional misses + ablations. |
| `sweep_frontier` | `{lo,hi,step}` | Budget frontier points. |
| `get_library` / `get_pantry` / `get_history` | `{}` | Reads. |
| `add_component` | `{component}` | Validate → write → doctor diff. Refuses on schema/semantic errors with exact reasons. |
| `add_ingredient` | `{ingredient}` | Same. |
| `update_pantry` | `{entries[]}` | Replace/merge stock. |
| `accept_plan` | `{week}` | Freeze artifact, history, pantry writeback. |
| `calibrate_yield` | `{component, cooked_g}` | Update yield; report macro drift. |

Safety rails: tools that write require the full validated object (no partial
patches in v1); every write response embeds the doctor diff so the operator sees
consequences immediately (I6/I10); no tool shells out or fetches URLs — recipe
*research* happens in the Claude session, only structured data crosses the boundary (I9).

### 9.4 Claude skill — `SKILL.md` v2

Rewrite against the finished interfaces. Must contain, in this order:
1. **When to use what**: MCP tools if registered; else CLI `--json` via device
   bridge; UI is for humans.
2. **The two-problem mental model** (§0) in three sentences, so the operator
   reasons correctly about *which* knob fixes *which* failure.
3. **Recipe ingestion protocol** (the compounding loop, G5):
   quantities → grams (volume/weight conversions, "1 medium onion"≈150g,
   "2 cloves garlic"≈6g); yield estimation rules (braise −30%, ground meat −25%,
   rice ×3, dry pasta ×2.2 — then `calibrate` after first cook); ingredient
   existence check → `add_ingredient` with USDA FDC lookup preferred over guessing;
   `serve_g` from real plates; `unit_g` only for discrete items; role/anchor
   tagging; NEVER hand-enter macros (I2); paywalled source ⇒ ask Devon to paste (I9).
4. **Infeasibility playbook** keyed to doctor output: `p SHORT` → add lean anchor
   or raise serve_max; `f forced OVER` → menu lacks lean protein (§5.1);
   `c SHORT` → add dense starch (§5.2); empty late days → cook_days / longer-keeping
   lean anchor (§5.4); tolerance loosen = last resort, cheapest knob first.
5. **Household structural facts** (§5.2–5.3, 5.9) stated as *re-derivable* via
   doctor, not gospel.
6. **Reporting norms**: lead with what doctor flagged; never bury infeasibility
   under a pretty table (I10); state placeholder/estimate provenance (I11).

---

## 10. Web UI (`web/app.html`)

Keep: single file, no build step, no external requests, system font stack,
dark default + light toggle, palette = the three validated series hues
(light `#2a78d6/#eb6834/#1baf7a`, dark `#3987e5/#d95926/#199e70` — all-pairs
CVD-validated both modes), series colors mean **macros only** (P/F/C) — never
reuse them for cuisines on the same screen (fixed bug; keep fixed). No dual-axis
charts. Every chart hover-labeled; tables always available.

Tabs:
- **Plan** — stat tiles (groceries vs ceiling, eaten vs leftover, per-person share
  with %cost-vs-%calories, waste, hands-on 🔴-flagged when over budget, per-person
  lb/day); menu chips (pin 📌 / drop ✕, lean badge, per-100g line, $/100g); dials
  (budget, dish count, per-person mass caps, shuffle seed) — every change
  debounced ~400ms → real re-solve; frontier charts (spend / distinct dishes /
  waste vs ceiling) from live sweep.
- **Eat** — person switcher; 7 day cards: kcal + lb/day header, three macro
  meters with target tick and tolerance-aware coloring, editable gram inputs
  with instant local recompute; **Rebalance locked days** → `/api/replate` per
  edited day; infeasible day renders the explained hole (REG-05 copy).
- **Shop & Cook** — cart tiles (total, still-to-grab, honest cook time
  per-session, batch count); perishable vs pantry tables, checkboxes with
  strikethrough + running remainder; per-session cook cards (component × batch
  count, per-session minutes, shortest-keeps note).

Additions (M3–M5): recipe card view (scaled `method` per batch count, printable);
pantry editor; history view (last 4 weeks' menus, repeat penalties visible);
person editor (targets/tolerance/dislikes — writes via validated API); "Accept
plan" button → `/api/accept`; empty-day copy links the fix (§5.4). Responsive
≥ 380px wide.

---

## 11. Feature specs (new work)

### 11.1 Pantry carryover (M2) — §7.4 semantics. Acceptance: a pantry with 1.4kg
rice reduces the shopping list by exactly the deducted packs; cooked leftovers
appear in week-builder availability with residual shelf life; menu search
measurably prefers pantry-burning menus (test with a stocked vs empty pantry).

### 11.2 History & repeat penalty (M2) — §7.5. Acceptance: a main used last week
is avoided this week at equal score (test: two symmetric candidates); pin
overrides; starches exempt; horizon 4 weeks with decay 0.5.

### 11.3 Recipe cards & method scaling (M3)
`method` steps stored per component; renderer scales ingredient quantities by the
session's batch count ("×3 batch: 1,362g ground beef (3 lb)…"), renders per-session
cook cards in UI and `plan.md`, printable view. Steps stay terse-imperative; no
prose inflation.

### 11.4 Tracker export (M3)
`mealplan week --json` already carries per-100g custom foods; add `--export
tracker.csv` (name, kcal, p, f, c per 100g) formatted for MacroFactor/MFP custom-
food import. Values must round-trip: logging `g` grams of a component in the
tracker matches the plan's math within rounding.

### 11.5 Liquid calories (M4) — the only remaining volume lever (§5.9)
New role `drink`, `mass_factor` (default 1.0; drinks ≈0.3), seeded components:
orange juice (store), honey-limeade concentrate, a soy-free/dairy-free mass-gain
shake (rice milk or juice base + oil + fruit — must clear both exclusion sets).
Acceptance: with drinks enabled, Jimbo's binary-searched mass floor drops
below 2,000g effective; doctor reports the new floor; UI mass tiles use
effective mass and footnote the factor.

### 11.6 Yield calibration (M3)
`calibrate <component> --cooked-weight N`: updates `yield_g`, recomputes per-100g,
prints macro drift ("birria: 232→218 kcal/100g, −6%"), appends a provenance note.
Rationale: yield estimates are the dominant macro error source (I2 pushes all
error into ingredients+yield; ingredients come from USDA; yield is the guess).

### 11.7 Receipt calibration (M6, OQ-3)
`mealplan prices --from receipt.txt` (or Claude does the parsing conversationally
and calls `add_ingredient` updates): match line items to ingredients, update
`cost`/`pack_g`, report per-item deltas and the new frontier floor. Estimates
carry `cost_source: estimate|receipt:<date>` provenance.

### 11.8 USDA FDC helper (M6)
Optional, network-permitted *only* in this flow (everything else offline): given
an ingredient name, fetch candidate FDC entries, present per-100g macros for
confirmation, store `usda_fdc_id`. Never auto-accepts.

---

## 12. Testing & quality

Framework: pytest. CI: GitHub Actions if the repo gets a remote; otherwise a
`make test` target Devon can run. **M0 writes the harness against the prototype
BEFORE extraction (G2)** — extraction is done when the same tests pass against
the package.

### 12.1 Property tests (every generated plan)
- No component carrying an excluded tag ever appears in that person's portions (I3).
- Every portion within `serve_g` bounds; `unit_g` portions are exact multiples (I5).
- Daily macros within ±tolerance for every non-hole day; holes carry explanations (I6/REG-05).
- Session batch splits sum to totals; `made ≥ need` per component; shopping packs
  cover ingredient need after pantry deduction (I7).
- Attribution shares sum to bought total (±$0.01). Mass caps respected in
  effective-mass terms.
- Determinism: same inputs + seed ⇒ identical plan.

### 12.2 Schema & validation tests
Unknown ingredient ref, negative grams, missing yield, anchor on a non-main,
component-declared tags (forbidden — derived only), bad enum values → structured
errors, no partial writes.

### 12.3 Named regressions (§5) — REG-01 fat-forced-over on all-fatty menu ·
REG-02 carb ceiling / ≥3 starches · REG-03 eleven-tortilla palatability ·
REG-04 lean-anchor ablation counts · REG-05 shelf-life valley day-7 explained
hole with cook_days [0,3] · REG-06 starch-exempt variety cap fills days 5–7.
Plus: frontier is non-flat and non-increasing in waste as budget rises within
the tested band; full-week-MILP guard (pipeline completes < 30s in CI).

### 12.4 Contract tests
Golden JSON files for CLI `--json` and each HTTP/MCP tool (schema-versioned);
UI smoke via Playwright: three tabs render, dial change triggers solve, gram
edit updates meters locally, Rebalance round-trips, zero console errors.

### 12.5 Performance tests
Full pipeline p95 < 10s (26-component library, n=12, M-series or CI-equivalent);
`plate()` p95 < 150ms; frontier 17 points < 25s.

---

## 13. Milestones & acceptance

Each milestone is shippable; do them in order; tests are the gate.

| M | Scope | Done when |
|---|---|---|
| **M0** | Test harness over the prototype as-is; pyproject with pinned deps; `make test` | §12.1/12.3 pass against `plan.py` unmodified |
| **M1** | Extract core package + adapters; CLI `--json` everywhere; schema validation on load/write; `validate` command | Same tests green against package; contract goldens recorded; prototype files become thin shims or are deleted |
| **M2** | Pantry, history, plan artifacts, `accept`; repeat penalty in search | §11.1/11.2 acceptance; `plans/` round-trip reproducible by inputs hash |
| **M3** | Method text + scaled recipe cards; tracker export; yield calibration; UI recipe/pantry views | Print a session's cards for the current week; CSV imports cleanly; REG suite still green |
| **M4** | MCP server + SKILL.md v2; liquid-calorie components | All §9.3 tools callable from Claude Desktop against a live library; Jimbo effective-mass floor < 2,000g with drinks enabled |
| **M5** | UI polish: person editor, history view, accept-plan flow, responsive pass | Playwright suite green incl. mobile viewport |
| **M6** | Receipt + USDA calibration flows | Prices carry provenance; frontier re-derived from a real receipt |

---

## 14. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Price estimates wrong → budget findings shift | Provenance field + receipt calibration (M6); UI labels "estimated" until then (I11) |
| `yield_g` guesses dominate macro error | Calibration flow (§11.6); SKILL yield rules; drift report on every calibrate |
| CBC/PuLP behavior drift across versions | Pin versions; tolerance-band goldens, not exact-gram asserts; document HiGHS (`highspy`) as tested fallback in M1 |
| Devon's placeholder macros mislead real use | Loud placeholder marking in UI + doctor header until OQ-1 resolves |
| Solver nondeterminism / flaky tests | Seeds everywhere; no wall-clock in engine; lock serializes CBC |
| Scope creep toward a full MILP "because cleaner" | I1 is constitutional; the 2-minute-timeout receipt is in §5.7 |
| Third eater appears (OQ-2) | N-person iteration already required everywhere; add a person = edit people.yaml, zero code |

---

## 15. Open questions (inputs owed, none block M0–M1)

| # | Question | Owner | Blocks |
|---|---|---|---|
| OQ-1 | Devon's real macro targets (current values are invented) | Devon | Truthful Eat tab, cost split, volume floor |
| OQ-2 | "Gemma" — transcription slip for Jimbo, or third eater? | Devon | people.yaml contents only |
| OQ-3 | A recent grocery receipt to calibrate prices (store: Publix?) | Devon | M6 quality; frontier realism |
| OQ-4 | Paid subscriber to @barefoodtim? If yes, paste 006/007/008/010/012 for proper ingestion (currently approximated/partial per I9) | Devon | Library fidelity for those 5 dishes |
| OQ-5 | `batch_time_factor` reality check after first real cook day (0.45 assumed) | Devon | Honest time accounting |
| OQ-6 | Liquid-calorie preferences (juice? shakes? neither?) before seeding §11.5 | Devon+Jimbo | M4 seed choices |

---

## Appendix A — Current empirical numbers (2026-08-05 library, estimated prices)

Jimbo floor ≈2,121 g/day (4.7 lb); Devon(placeholder) ≈1,215 g/day. Budget floor
≈$264/wk, plateau ≈$340, current plans $322–$345 bought / $226–$241 eaten.
Attribution ≈ jimbo 57% cost / 62% kcal. Honest cook time ≈5h44m/wk at factor
0.45 (45 batches, 12 components). Carbs-to-mass: tortilla 1,153g … pintos 3,108g
per 588g carbs. Waste 2,188g@$320 vs 2,988g@$200. All shift with OQ-1/OQ-3.

## Appendix B — Glossary

**Component** one batch-cooked, weighable thing · **Accent** small finishing
component, per-person omittable (I3) · **Lean anchor** low-fat protein main
enabling protein-without-fat · **Doctor** diagnostic + ablation report ·
**Frontier** budget→outcome sweep · **Hole** explained infeasible day ·
**Plan artifact** frozen accepted week in `plans/`.
