# mealplan — Product PRD & Engine Spec

| | |
|---|---|
| **Version** | 2.1 (2026-08-09) — v2.0 draft revised same-day after adversarial review of the draft itself (40 findings, all dispositioned) |
| **Working title** | `mealplan` (naming open — OQ-N1) |
| **Owner** | Devon (product decisions, content curation) |
| **Implementer** | Claude Code, in this repo |
| **Replaces** | v1.0, preserved as `PRD-v1-household.md`. v1 was one household's configuration written as if it were a product spec; `PRD-SCRUTINY.md` documents the 91 findings (19 critical, 47 major, 25 minor) that killed it. This document is scoped so that no fact about any particular household appears as a product fact — and so that no implementer proposal masquerades as an owner decision (§12.2). |

---

## 0. The product in one paragraph

**mealplan** is for households that lift — couples, families, roommates who share a
kitchen but not macro targets. You batch-cook a shared menu of real-food components;
the solver portions the *same pots* differently per person so everyone hits their own
numbers, around their own exclusions. Input: recipes in the mealplan format, plus each
person's targets and constraints. Output: a shopping list, a batch cook plan, and
per-person per-day assembly sheets. It works for one person (a solo lifter meal-prepping
to macros); it is *uniquely* valuable the moment two people with different targets share
a kitchen.

## 1. Customer & wedge

**Primary customer:** cohabiting people who track macros — couples, families, roommates
— who batch-cook or want to. They currently either cook twice, eat the same plate and
miss their numbers, or run two separate meal-prep systems in one kitchen.

**Secondary customer (declared, sequenced later):** coaches building weekly plans for
clients. Devon's read: coaches "could use this amazingly." Same engine, different
topology — clients don't share pots. Per Devon's fork decision ("Both, eventually,"
shared first — recorded from the 2026-08-09 discovery session, as are all owner
quotes in this document), coach mode is *proposed* as the first candidate for
post-beta scope (§12.2, PR-6).

**The wedge (hypothesis, not yet verified):** mainstream planners we're aware of
(Eat This Much, Mealime, macro-tracker planning features, meal-prep services) plan
per-person or family-averaged; we have not found one that solves *one shared batch
menu × divergent per-person targets × disjoint exclusions*. A proper competitive scan
is owed before this claim appears in marketing copy (OQ-M1). The engine capability
itself is real — v1's prototype demonstrated the per-day LP over shared components.

**Funnel shape:** n=1 works and is the low-friction entry; n≥2 shared-pot solving is
the moat and the retention.

## 2. Product principles

The constitution. Changing one requires updating this document first, with rationale.

- **P1 — Instance data never in the product.** No household's people, targets,
  exclusions, schedule, or tastes appear in code, tests, defaults, or docs as product
  facts. Every household is data. The founder household lives in `examples/` as a demo
  dataset and real-use config; capability tests use synthetic fixtures only (§9).
  *(This is the lesson of v1.)*
- **P2 — Two-problem decomposition.** Menu selection is combinatorial search over a
  cheap structural score with LP verification; portioning is an exact linear program.
  Never merged. (Measured on the dev corpus: the merged full-week MILP timed out at
  2 minutes; the decomposition solved in seconds. Scale-general performance targets
  are set by measurement in M0 — §8.5.)
- **P3 — Macros are derived, never hand-entered.** A recipe is ingredients-in-grams plus
  a cooked yield; per-100g macros are computed. kcal is computed by Atwater (4/9/4) from
  macro grams everywhere — ingredient-level label kcal is not stored (v1 carried two
  contradictory kcal accountings).
- **P4 — Accents split exclusions at plating.** Finishing components are their own
  recipes so one batch stays universal until the plate; structural allergens live inside
  the component and hard-exclude it. No variant machinery.
- **P5 — Hard constraints and soft preferences never merge.** Exclusions (allergen/diet
  tags) are infeasibility-hard and permanent config. Dislikes and weekly vetoes (§4.3)
  are soft weights. "Not this week" is never encoded as "can't eat."
- **P6 — Every infeasibility is explained, directionally.** Who, which macro, short or
  forced-over, and what *class* of recipe fixes it. A bare "infeasible" is a bug.
- **P7 — Grams internally, human units at every boundary.** Solver math in grams;
  shopping in store packs; and in relaxed mode (§4.1), plates in household units.
- **P8 — No silent caps, drops, or relaxations.** If the solver relaxed something, the
  output says so. If an input was ignored, that's a defect (v1 shipped a `--force` flag
  and five config fields that were silently dead).
- **P9 — Provisional defaults are labeled.** Any constant not yet calibrated by real
  usage carries `provisional` provenance in code and product copy. This labels
  *constants awaiting calibration*; it is not a license to skip justifying design
  decisions — those still need stated rationale.
- **P10 — One canonical computation per quantity.** Any number shown on any surface
  (web, API, CLI, MCP, export) comes from exactly one engine function. (v1's CLI and
  server disagreed on batch counts, cook minutes, and cost for the same plan.)

## 3. The demo — north star

Devon's statement of the demo (paraphrased from discovery, 2026-08-09): *given one or
more persons' macros, generate a meal plan that can be prepped for the week to those
macros, with a shopping list, prep plan with recipes, and sheets that say what to eat.*

Every milestone (§11) is judged against whether it moves this demo. A proposed
sharpening — "a stranger reaches it in ten minutes without documentation" — is an
implementer proposal (§12.2, PR-4) and is operationalized at M2/M4, not before.

## 4. How it works — modes, roles, and the weekly loop

### 4.0 The meal-prep model (owner decision, 2026-08-09 — supersedes daily self-assembly)

Devon, verbatim intent: *"Each person gets n meals a day. You prep on x days of the
week… I portion the meals for both people on those days. I just don't cook a bunch of
rice and portion it every day."*

The product's model is **classic meal prep**: on each cook day, dishes are cooked in
batches and immediately **portioned into per-person, per-meal containers** covering
the days until the next session. Eaters grab a container; nobody weighs plates daily.
Consequences:
- `meals_per_day` is a **live engine input**: each person's solved day is dealt into
  n composed meals (a main plus sides that read as food — never a bucket of one
  component), each meal near day÷n macros within a band (provisional).
- The eat sheet is meal-structured per day for the prepped period.
- The cook plan carries a **portioning matrix**: per session, per component — which
  containers (person × day × meal) receive how many grams.
- Precision/relaxed modes (§4.1) govern how portioning is *measured at packing time*,
  not a daily weighing ritual. Day-level rebalance (§4.4) remains for mid-week edits.

**Amendment (owner, same day):** both serving models are first-class options —
`serving_model: portioned | family_style`, set **per person**:
- **portioned** — the meal-prep model above: per-meal containers packed on cook day
  (portioning matrix on the cook plan; meal-structured eat sheet).
- **family_style** — v1's component-cooking heritage as a named mode: batches stay in
  shared containers; the eat sheet guides how much to take (per meal when
  `meals_per_day` is set, per day otherwise); precision/relaxed governs measuring at
  serving time.
The two models share the entire solve — engine, batching, shopping, shelf life are
identical; only cook-plan instructions and eat-sheet rendering differ. A household
may mix models freely.

**The dish layer (owner correction, 2026-08-09, late):** *"These aren't meals —
they're random ingredients served together… how is that following an actual recipe
I gave you?"* Components are the COOKING decomposition of recipes; the recipes'
ASSEMBLY is a first-class layer that ingestion must preserve: a **dish** is a named
combination of components with per-serving ratio bands and optional accents
(gorditas de picadillo = shells + picadillo + salsa + queso). A meal is **one dish,
portioned within its bands, plus compatible sides** — never a free mix of the
week's pool. Menu selection picks dishes; components derive for batching/shopping
(unchanged — components remain the batch units, shared components consolidate
across dishes at cook time); the LP portions within-dish per person (same pots,
different macros — the moat survives, structured). Meal-time affinity (breakfast)
attaches to dishes. This supersedes any framing of meals as component pools.

**The cook-plan bar (owner, 2026-08-09):** *"thoughtless for someone to meal prep in
the fastest way possible — here's the ingredients, here are the exact steps, and how
to parallelize it."* The compiled cook script's mature form is a single interleaved
**timeline** (timestamps, timers for passive waits, "meanwhile" structure, portioning
injected where hands are free), scheduled greedily over stations and cook-attention
(passive-first; assemblies topologically first), on provisional durations calibrated
from real cook days. Zero decisions left to the cook. (Station-grouped blocks are the
fallback rendering until durations exist.)

**Amendment (owner, same day):** cook-plan style is a preference —
`cook_plan_style: recipe | timeline`. Recipe style renders classic per-dish blocks;
timeline renders the interleaved optimized stream; both are views of the same
compiled session, and the scheduler runs ONLY for timeline users (modular — nobody
pays for optimization they didn't order). **Shared-prep consolidation** serves both
styles: identical prep operations merge across dishes at compile time ("dice 380g
onion — 150g → picadillo, 150g → carnitas, 80g → scramble") — shopping-list-style
aggregation one layer down. Oven sharing is temperature-keyed in the greedy tier:
same-temp steps co-reside ("steaks finish at 425 because the veggies already made it
a 425 oven"); temp transitions and hold-window juggling stay CP-tier, later.

**Amendment 2 (owner, same day):** the serving model is configurable **per meal
slot**, not just per person — e.g. breakfast `family_style` ("have this much
cereal"), lunch and dinner `portioned`. Person-level `serving_model` is the default
slots inherit. Meal variety is an obligation of the meal layer: a day's meals must
differ from one another where the menu allows (no repeated main across slots), atop
the no-single-component-meals rule. **Interchangeable containers** (macro-equivalent
meals within a slot across days, so any container is grabbable) is a named opt-in
candidate for the meal layer — it trades variety for flexibility, so it is a
household choice, never a default.

### 4.1 Precision mode and relaxed mode (per person)

- **Precision** (kitchen scale): portions in grams, tight tolerance. The scale is
  *strongly preferred* and the product says so — but never required.
- **Relaxed** (no scale): portions rendered in household units — whole items ("3
  tortillas"), volume approximations ("about 2 cups rice"), fractions of a batch
  ("a third of the container"). Tolerance widens (default ±12%, provisional) and the
  sheets show honest error bars rather than false precision.

Mode is per *person*, not per household — one eater weighs, another wings it, same pots.
Internally both are grams (P7); relaxed is a rendering + tolerance profile.

### 4.2 People, accounts, and roles

Roles: **planner** (configures people, approves plans, shops), **cook** (sees prep
plan; often = planner), **eater** (sees their sheets, vetoes, nudges portions). The
role names and split are implementer design, refinable. One person can hold all
roles (n=1). The data model iterates over people everywhere — no
two-person assumptions (v1's UI hardcoded two).

Per-person accounts exist from the first hosted deployment (M2): the collaborative
loop below requires knowing who vetoed and who to notify. (Devon's words were "would
be cool for everyone to have an account"; treating accounts as a requirement rather
than a nicety is flagged as a proposal — §12.2, PR-2. M0–M1 are local-first and have
no accounts.)

### 4.3 The weekly loop (collaborative)

1. **Propose** — planner (or the operator layer, §10) generates a candidate plan.
2. **Review window** — eaters can **veto** dishes or flag portions before the plan
   locks. A veto is a *this-week* soft removal (P5) that triggers a re-solve;
   allergies never travel this path. Default: eaters are notified when the window
   opens, 48h before the plan's primary shopping trip (§8.2) (both the notification
   default and the timing are provisional; Devon floated it as "maybe" — §12.2,
   PR-3; channel is OQ-P1).
3. **Lock** — plan freezes into an immutable artifact: menu, portions, batches,
   shopping list, cost snapshot, veto history. What everyone sees for the rest of the
   week is the *locked plan* — never a fresh solve (v1's UI re-solved on every page
   load, so an eater's Tuesday view could show a menu nobody shopped).
4. **Shop / Cook / Eat** — the three deliverables: shopping list in store units, batch
   cook plan per session with scaled recipes, per-day per-person assembly sheets.
   Sheets can group a day into meal slots if the person configures a meal structure
   (presentation-level in v1 — §8.1).

### 4.4 Adjust & recovery

- **Day rebalance (M2):** an eater's day re-solved around pinned portions,
  constrained to that day's actually-available components (v1's replate ignored
  shelf life). Pinned grams are clamped to serve bounds and unit grids by default;
  a deliberate out-of-bounds pin is allowed with an explicit warning (§8.3).
- **Rest-of-week replan (M4):** re-solve the remaining days from current reality —
  burned batch, restaurant night, store stock-out. The loop is not credible without
  this recovery path; it ships in M4, after the happy path works.
- **Honesty about the loop's limits:** v1.0 of the product is *open-loop* — it does
  not capture what was actually eaten, and pantry forward-writes are predictions.
  Actuals capture (and everything it unlocks: real leftovers, real attribution,
  adherence trends) is declared post-M4 scope, informed by manual feedback collected
  during the M4 beta. Stating this plainly is the disposition of v1-scrutiny's
  "open-loop" finding — not a claim that v2 closed the loop.

## 5. Scope

### 5.1 v1 in

- Macros p/f/c (+ derived kcal) per person; tolerance per person.
- Exclusions as user-editable tag sets, with common diet presets (dairy-free,
  gluten-free, nut allergies, vegetarian, vegan, pescatarian, …) as starting points —
  presets are conveniences over the same tag machinery, not special cases.
- Shared-household mode, n ≥ 1.
- Shelf-life-aware scheduling: cook sessions, per-component cooked shelf life,
  raw-ingredient freshness at cook time (§8.2), explained holes.
- Cook-time honesty: batch-scaled hands-on minutes per session, flagged against the
  household's stated budget, never silently fit.
- Waste awareness: purchase-unit rounding after pantry deduction, perishable leftover
  surfaced. Pantry (§8.1) ships incrementally: schema + empty-pantry semantics from
  M0; stock tracking UI from M2.
- Variety: repeat caps within a week; cross-week history informs, doesn't constrain.
- Precision + relaxed modes; the propose → veto → lock → deliver loop (§4.3);
  day rebalance at M2 and rest-of-week replan at M4 (both §4.4).
- Surfaces (§10): web app and operator layer at **capability parity** per Devon's
  "both, equal weight" decision — parity holds from M3 onward; the sequencing that
  gets there (web loop at M2, operator parity at M3) is an implementer proposal
  (§12.2, PR-1).

### 5.2 Declared later (designed-for, not built)

- **Coach mode / plan groups** — proposed *first candidate for post-M4 scope*
  (§12.2, PR-6; Devon: coaches "could use this amazingly"; his fork decision
  sequenced shared-household first). The schema isolates shared-pot assumptions
  behind a plan-group concept (§8.1) so this mode is additive, not a rewrite.
- **Budget optimization**, including budget↔outcome sweeps. It matters ("you can't
  have wagyu every night") and it returns; near-term, cost pragmatism comes from
  recipe selection itself. The data model keeps prices; the v1 solver reports cost
  but does not optimize on it.
- **Ingestion automation.** Scraping/importing recipes from the web into the format.
  Devon: "figure that out later; right now, use my existing corpus." v1 documents the
  format; M3 proposes a Claude-assisted conversion protocol as a stepping stone
  (§12.2, PR-5); automated scraping is post-M4.
- **Free weekly allocation** — targets as a pure weekly budget with the solver
  choosing the per-day split within bands (couples days inside one optimization).
  Opt-in successor to profile-based cycling, which was promoted INTO v1 by owner
  decision 2026-08-09: *"some people scale macros, more on lifting days, less on
  weekends — macros are actually a weekly target, not a daily one."* Profile-based
  cycling (named day-types + week map anchored to the plan date; flat daily grams
  stay valid as shorthand) ships in M1 (TASKS M1.11).
- **Liquid-calorie support** — semantics for the `drink` role and effective-mass
  factors for the daily mass cap. Until then `drink` is a reserved enum value.
- **Component assembly DAG** — components consuming components (`uses:` — a seared
  base feeding multiple dishes), generalizing the accent model into a dependency
  graph with transitive macro derivation, batch sizing, shopping rollup, and
  topological cook order. Flat authoring is the current workaround. Station/attention
  metadata on method steps (grill/stove/oven, active/passive) ships with the compiled
  cook script (M1.10); full makespan scheduling of a session is a named someday.
- Actuals capture / closed-loop planning (§4.4); community recipe sharing;
  micronutrients/fiber; native mobile apps; payments infrastructure (§7).

### 5.3 Non-goals (v1 and foreseeable)

Photo/vision food logging; grocery-delivery integration; multi-week global
optimization; medical nutrition claims of any kind.

## 6. Content strategy

- **Dev corpus:** the existing ~70-ingredient / 26-component library. Development,
  demos, and the founder household's real use only.
- **Distribution blocker, named now:** part of the corpus derives from @barefoodtim's
  Substack — some recipes from free posts, several approximated from paywalled ones
  (v1's OQ-4). All of it is **quarantined from anything shipped or seeded** to other
  users. A shippable seed library needs original or licensed content (OQ-C1).
- **The format is the contract:** ingredients in grams + cooked yield + serving
  metadata (§8.1). Everything else — scraping, AI conversion, community content — is
  tooling that produces documents in the format. Format documents carry
  `schema_version` and validate on every write (§8.1); a malformed recipe cannot
  corrupt a library.
- **Recipes are ground truth, never rendered** (owner framing, 2026-08-09): library
  recipes are derivation inputs — ingredients, yields, composable method-step
  fragments. What users follow is the **compiled cook script** synthesized per plan:
  aggregate demand across meals/days/people → batches per session → assembled steps
  + portioning instructions. Method text is therefore stored as composable steps,
  not prose. Corollary: the product never republishes a source recipe — rendered
  artifacts are always plan-specific compilations.

## 7. Business posture

Eventually paid: **generous free tier + premium add-ons**. Tier boundaries are
deliberately undecided (OQ-B1) — with one guardrail already: the primary customer is
couples/families/roommates, so **household size is a poor paywall**; premium candidates
are instead coach mode, ingestion automation, budget features, and advanced analytics.
Obligations on v1 *now*: the architecture supports accounts and per-household data
isolation at the first hosted deployment (M2), and full plan/library export in open
formats (no lock-in) exists from M1. Pricing and billing are deferred.

## 8. Engine spec

The engine is the asset: a pure, deterministic, importable library with no I/O beyond
what it's handed. Everything below either survived v1 scrutiny or corrects a confirmed
v1 defect (Appendix A maps findings → dispositions).

### 8.1 Data model

**Shared semantics:** `keeps_days: N` means *usable at ages 0..N−1 days* — the same
strict-inequality convention for raw and cooked shelf life, stated once here. Days are
0-indexed everywhere, including docs and tests (v1 mixed conventions).

- **Ingredient:** per-100g macros (p/f/c; kcal derived, P3), allergen/diet tags,
  purchase `pack_g` + price (provenance-labeled: `estimate` vs `receipt`), `perishable`,
  raw `keeps_days`, optional `edible_fraction` *(new — bone-in items: portions are
  weighed gross, macros apply to the edible fraction; v1 overstated wing protein ~50%)*.
- **Component (recipe):** ingredients in grams, cooked `yield_g`, `role`
  (`main|starch|veg|accent|drink`), optional `anchor: lean`, per-person-scalable
  `serve_g` bounds *(new: bounds scale with a person's kcal, provisional scaling
  curve — v1 shared one bound across a 1.65× kcal spread)*, optional `unit_g`
  (validated: bounds must be unit-aligned — v1's snap could silently violate bounds),
  cooked `keeps_days` *(defaults in shipped content follow food-safety guidance;
  households may override as data, and overrides beyond guidance are labeled in
  product surfaces, not hidden)*, `freezes` *(live, not decorative: a frozen
  half-batch extends availability; v1 recorded it, ignored it, then told users to
  freeze things)*, `active_min`, method steps, source/provenance.
- **Person:** targets (grams/day, static in v1 — §5.2), tolerance, mode (§4.1),
  exclusion tags, dislikes, optional daily mass cap *(v1 semantics: raw grams;
  effective-mass factors for liquids arrive with the drinks feature)*, optional meal
  structure (presentation-level grouping of a day's sheet into meals; the v1 engine
  plans days, not meals), plan-group membership *(v1: exactly one shared-household
  group; the field exists so coach mode is additive — §5.2)*.
- **Pantry:** on-hand ingredient stock (grams, acquired date — age reduces effective
  raw `keeps_days`) and cooked leftovers (grams, cook date — join availability with
  residual life). Purchasing deducts pantry stock before rounding to packs. Empty
  pantry is a valid state with well-defined semantics from M0; stock-tracking flows
  arrive with M2.
- **Plan artifact:** immutable on lock — inputs snapshot + hash, menu, per-person
  per-day portions, per-session batches, shopping list with costs, veto history.
  Reproducible: the hash covers *all* inputs including seed and pantry state, and
  accepting a plan writes forward (pantry predictions, history) without mutating the
  artifact (v1's accept mutated its own inputs, making its reproducibility claim
  circular). Artifacts are keyed by **shop date** (§8.2).
- All documents carry `schema_version`. All writes validate first, atomically, with
  every error reported (not first-error-wins).

### 8.2 Canonical computations (P10 — one function each)

- **Availability:** component cooked in session *s* is edible on day *d* iff
  `0 ≤ d − start(s) < keeps_days`, extended by freezing (§8.1).
- **Session attribution:** a day's demand is fed by the **earliest** session whose
  batch is still within shelf life (economy over freshness; provisional). Batches per
  session = ceil(session demand / yield). This one definition feeds cook plans,
  minutes, purchasing, and cost on every surface (v1's two surfaces disagreed).
- **Raw freshness (new):** shopping trips are data — a household has one or more per
  week. An ingredient may be cooked in session *s* only if
  `session_start − trip_day < raw keeps_days` for the nearest prior trip that buys
  it, unless frozen on arrival (then a thaw note appears in the cook plan). Menu
  search and diagnostics both enforce it; v1 planned 6-day-old raw shrimp without
  noticing. *Deliberate, labeled simplification:* cooking is treated as a shelf-life
  reset (kill step); cooked life is not additionally discounted by input age.
- **Purchasing:** grams → packs happens in exactly one function, after pantry
  deduction. **Cook minutes:** first batch full `active_min`, marginal batches at
  `batch_time_factor` (provisional), summed per session.
- **Week keying:** a plan's **primary trip** is its first shopping trip; the plan is
  keyed by the primary trip's date (e.g., `2026-08-09`), and the veto window closes
  before the primary trip. This sidesteps v1's ISO-week-vs-week-start straddle bug
  and stays well-defined for multi-trip weeks. Plans are stored as engine documents;
  the runtime store is the service's database (§10), with file export always
  available.

### 8.3 Solver & diagnostics

- **Plate LP** (per person × day): grams per eligible component with on/off binaries
  for serve bounds; elastic macro bands at ±tolerance; the optional daily mass cap
  (§8.1) enforced as a plate constraint; discrete items solved continuous → snapped
  to units *clamped within bounds* → re-solved. Signed misses out.
- **Pinned portions** (day rebalance, §4.4): pins are clamped to serve bounds and
  unit grids by default; an explicit out-of-bounds pin is honored with a warning and
  excluded from bound-property guarantees.
- **Menu search:** seeded local search on a cheap structural score, LP-verified
  shortlist. Score terms are configurable weights with `provisional` provenance —
  v1's magic penalties (4000/6000/15000…) don't get to hide as design. All score
  estimates (cost, waste, time) are computed at one consistent estimated-batch scale
  (v1 mixed 1-batch waste with estimated-batch cost inside the same score).
- **Diagnostics ("doctor"):** per-person feasibility with directional misses;
  binding-constraint identification (*which* macro binds for *this* person — the
  generic capability behind v1's "carbs bind at 4,700 kcal" household fact);
  ablation to find how many lean anchors *this* library+household needs;
  **volume-floor identification** (search over the mass cap to report the minimum
  daily food mass a person's targets require — the generic form of v1's reproduced
  4.7 lb/day finding, which stays a capability, not a dead field); carb/starch
  headroom derived from the actual availability math rather than v1's unexplained
  1.45× fudge (any remaining heuristic is labeled per P9). Diagnostics run on demand
  and after library writes — asynchronously, never blocking an interactive solve.
- **Infeasibility playbook ordering** (product decision, resolving v1's three-way
  contradiction): suggest the cheapest *structural* fix first — add the missing class
  of recipe, move a cook day, raise a serve bound someone would actually eat.
  Loosening tolerance is the explicit, labeled last resort, because it redefines
  success instead of fixing the plan. (The dev corpus's contrary comments in
  `people.yaml` and `SKILL.md` get corrected in M0 cleanup.)
- **Determinism:** every random draw flows from an explicit seed; no `hash()`, no
  wall clock (v1's same-seed runs produced different plans; reproduced twice).

### 8.4 Contracts

Engine API is versioned (`mealplan/v2`); CLI `--json`, HTTP, and MCP all serialize the
same engine result objects (P10), and §9 includes cross-surface contract tests that
prove it. Write operations accept full validated objects in v1 — no partial patches
on any surface. Exit codes distinguish "computed but infeasible" (a result) from errors.
Solves serialize behind a lock; long-running work (bulk diagnostics, future sweeps) is
interruptible and never blocks an interactive solve (v1's frontier held the lock ~25s).

### 8.5 Performance envelope (measure, then promise)

v1 asserted performance numbers its own code contradicted (an LP-count "guard" off by
an order of magnitude; wall-clock CI assertions that flake by construction). v2 sets
the *process* instead: M0 instruments the engine (LP solve counts, stage timings) and
records measured baselines on a reference machine; M1 sets interactive-latency targets
*from those measurements*, labeled provisional; CI asserts on solve-count budgets
(deterministic) and generous median-time bands, never single-shot wall clocks.

## 9. Test strategy

The v1 test plan failed because it pinned tests to live mutable data and to features
that didn't exist. v2 rules:

- **Synthetic fixtures only.** Capability tests run against frozen, purpose-built
  fixture households and libraries in `tests/fixtures/` (single person; several-person;
  conflicting exclusions; extreme targets; discrete-unit edge cases). The founder
  household is **not** a test fixture — it lives in `examples/` for demos and real use
  (P1). Its structural situations (asymmetric targets, disjoint allergens) are
  *recreated synthetically* where tests need them.
- **Capability tests, not household regressions.** v1's REG list generalizes:
  "composite dishes force fat over when protein is capped," "binding-macro
  identification is correct," "shelf-life valley produces an explained hole,"
  "variety caps don't starve late days," "unit snapping stays in bounds," "excluded
  tags never appear in that person's portions," "same inputs + seed ⇒ identical plan."
  Each is a named test with a purpose-built fixture.
- **Golden policy, one rule:** on the pinned reference environment (one OS/arch +
  pinned solver, enforced in CI), goldens are byte-stable. Other platforms assert
  properties and tolerance bands, not bytes — cross-platform LP byte-stability is not
  a thing CBC promises, and v1's claim otherwise was falsified.
- **Cross-surface contract tests:** the same request through CLI `--json`, HTTP, and
  MCP yields the same engine result object (P10 made testable).
- **The M2 loop gate is automated:** a browser-automation test (dev dependency,
  outside the engine's pinned core) exercises propose → veto → lock → deliver with
  two accounts.
- **Perf assertions** per §8.5: solve-count budgets + median-time bands.

## 10. Surfaces & architecture posture

- **Engine:** pure Python package. No adapter contains solver logic. M1's "API" is
  this package plus its CLI — in-process, local-first, no service required.
- **Service (from M2):** HTTP API wrapping the engine; accounts, auth, and
  per-household isolation from the first hosted deployment; runtime store is a real
  database *(v1's "YAML is the database" is dead for runtime — YAML survives as the
  interchange, export, and fixture format)*. All mutations flow through validated,
  transactional API writes that record an audit trail (who, when, what changed) —
  replacing v1's "git is the audit log," which had no specified committer and never
  actually committed anything.
- **Web app:** the collaborative loop lives here; responsive; PWA-capable.
  **Cook mode** (owner, 2026-08-09): full-screen step-at-a-time rendering of the
  compiled timeline — big type, tap-next, live timers, wake lock, locked plan cached
  offline (kitchens kill connectivity). Same structured payload as the markdown cook
  plan — cook mode is a second renderer, not a second pipeline. Step fragments
  reference *operations* (dice/sear/braise); a technique library maps operations →
  explanations and, later, owner-recorded videos (owned content, reused across every
  step naming the operation). Cook-time LLM Q&A ("my braise looks dry") is
  edge-assistance per the LLM doctrine — premium candidate, never in the solve path.
- **Operator layer:** MCP server + CLI `--json` over the same engine/API, at
  **capability parity** with the web app from M3 onward (Devon's "both, equal
  weight" decision; the M2 web-only gap and the path to parity are PR-1). Claude can
  drive everything a planner can — propose, explain infeasibility, convert recipes
  into the format, run diagnostics.
- **Stack decided 2026-08-09 (resolves OQ-T1), full record in `ARCHITECTURE.md`:**
  NestJS API + Angular web + Python solver service (FastAPI wrapper over the engine);
  Supabase for Postgres + Auth only; Render hosting; Turborepo monorepo with a
  generated `packages/contracts` as the single source of cross-language types (P10
  across the language boundary). The engine and its contracts remain
  hosting-independent.

## 11. Milestones

Each shippable, in order, tests as the gate. M0–M1 are local-first; the service
posture (accounts, auth, hosted anything) enters at M2.

| M | Scope | Done when |
|---|---|---|
| **M0 — Engine correctness** | Extract engine package from the prototype; fix the confirmed defects (Appendix A); pantry semantics (empty-state); instrumentation (§8.5); synthetic fixtures; capability tests; determinism; dev-corpus advice cleanup (§8.3) | Full suite green; same inputs + seed ⇒ byte-identical plan on the reference environment; no dead config; one canonical computation per quantity; measured perf baselines recorded |
| **M1 — Headless demo loop** | Package/CLI: arbitrary household config in → the three deliverables out (shop list, cook plan, eat sheets); n=1..4; precision+relaxed; locked-plan artifacts; open-format export | The §3 demo works from a fresh config file with the format docs at hand; founder household runs a real week on it |
| **M2 — Collaborative web app** | Contract-codegen pipeline first (blocks other API work — `ARCHITECTURE.md`); service + accounts/roles, household setup, propose → veto → lock → deliver loop, day rebalance, pantry stock UI, responsive | A two-account household completes propose → veto → lock → deliver; eaters see only the locked plan; first-run experience hits the ten-minute bar if PR-4 is ratified |
| **M3 — Operator layer & ingestion assist** | MCP tools at parity; recipe-format conversion protocol (Claude-assisted, PR-5) with validation; operator instructions (successor to the v1 `SKILL.md`) | Claude runs the demo end-to-end and adds a recipe from pasted text without corrupting a library; §9 cross-surface contract tests green |
| **M4 — Beta** | 3+ external households (real ones), rest-of-week replan (§4.4), manual feedback capture | Each beta household cooks and eats ≥1 real week; findings scoped into post-M4 |
| **Post-M4 (sequenced)** | Coach mode first candidate (§5.2, PR-6); then budget optimization, ingestion automation, actuals capture, payments | — |

The **"cook a real week" gate** v1 never had appears twice: founder household at M1,
external households at M4. Solver-side validation alone is no longer called validation.

## 12. Open questions & pending proposals

### 12.1 Open questions (owner input owed)

| # | Question | Blocks |
|---|---|---|
| OQ-N1 | Product name | Nothing yet |
| OQ-C1 | Seed-library content: authored? licensed? how many recipes is "enough to start"? | M4 onboarding quality |
| OQ-B1 | Free/premium boundary and pricing | Nothing before M4 |
| OQ-M1 | Competitive scan to verify the wedge claim (§1) | Marketing copy only |
| OQ-P1 | Notification channel for the veto window (push / email / SMS) | M2 detail |
| OQ-T1 | ~~Service hosting & stack~~ **Resolved 2026-08-09** → `ARCHITECTURE.md` | — |
| OQ-D1 | ~~Founder-household real targets~~ **Dissolved 2026-08-09** — owner: targets are arbitrary by design ("arbitrary goals for arbitrary people"); `examples/` is demo data, not ground truth | — |

### 12.2 Implementer proposals awaiting ratification

Decisions this document *proposes* but Devon has not made. Each is written so a one-word
answer resolves it; until then the proposal stands as default.

| # | Proposal | Devon said | Status |
|---|---|---|---|
| PR-1 | Path to parity: web app ships the collaborative loop at M2; the operator layer reaches full capability parity at M3; from M3 onward, new capabilities land on both surfaces within the same milestone. M2 is an acknowledged web-only gap | "Both, equal weight" | Proposed sequencing, not a priority override |
| PR-2 | Per-person accounts are a requirement of the collaborative loop (not merely nice) | "Would be cool for everyone to have an account" | Proposed as requirement at M2 |
| PR-3 | Veto-window notifications default ON, 48h before shop day | "Maybe everyone gets a notification 2 days before" | Proposed default; trivially off-switchable |
| PR-4 | North-star bar: a stranger reaches the demo in ten minutes without documentation (measured at M2/M4) | Not stated by Devon | Proposed acceptance bar |
| PR-5 | M3 ships Claude-assisted recipe conversion (format + protocol) as the stepping stone; automated scraping stays post-M4 | "Figure that out later; right now, use my existing corpus" | Proposed timing |
| PR-6 | Coach mode is the first post-M4 scope item, ahead of budget optimization and actuals capture | "Coaches could use this amazingly" + "Both, eventually" | Proposed priority |

## Appendix A — v1 findings → v2 dispositions

The full 91 findings live in `PRD-SCRUTINY.md` (19 critical, 47 major, 25 minor).
Load-bearing dispositions:

| v1 finding (confirmed) | v2 disposition |
|---|---|
| CLI and server computed different batches/minutes/cost | P10; §8.2 single session-attribution definition; §9 cross-surface tests |
| Same-seed nondeterminism (`hash(pname)`) | §8.3 determinism rule; M0 gate |
| Dead config (`min_lean_anchors`, `meals_per_day`, `--force`, `freezes`, …) | P8; M0 "no dead config" gate; `freezes` and meal structure become live or die (§8.1) |
| Raw ingredients expired before session 2; no freshness constraint | §8.2 raw-freshness rule, generalized to ≥1 shopping trips as data |
| Bone-in items overstate logged macros ~50% | `edible_fraction` (§8.1) |
| Household facts as product invariants & regression tests | P1; §9 synthetic-only fixtures; founder household in `examples/` |
| Invented constants presented as findings (1.45×, ×6, 0.45, 0.3…) | P9 labeling; §8.3 derive-or-label |
| Volume-floor finding (reproduced) at risk of dying as a dead field | §8.3 volume-floor diagnostic — kept as a generic capability |
| Phones can't reach a localhost-only server | §10 service posture with auth (from M2) |
| UI re-solved on every load; no locked plan | §4.3 plan lifecycle; locked artifacts |
| Open-loop (predicted leftovers, no actuals) | §4.4 states it plainly: v1 stays open-loop; actuals capture is post-M4, informed by M4 manual feedback. Not claimed as closed |
| Replate ignored availability; locked grams bypassed bounds | §4.4 + §8.3: day-constrained rebalance; pins clamped by default, warned when deliberate |
| Accept mutated its own inputs (hash circularity) | §8.1 artifact immutability + forward writes |
| ISO-week vs week-start keying straddle | §8.2 shop-date keying |
| Two kcal accountings (label vs Atwater) | P3 Atwater-only |
| serve_g shared across divergent eaters | §8.1 per-person scaling (provisional) |
| Golden policy self-contradiction; cross-platform byte-claims falsified | §9: byte-stable on pinned reference env only; properties elsewhere |
| LP-count guard off by 10×; wall-clock CI assertions flaky by construction | §8.5 measure-then-promise; solve-count budgets + median bands |
| "Git is the audit log" with no committer; multi-writer concurrency unspecced | §10 transactional service writes with audit trail; M0–M1 local mode: atomic validated file writes |
| Frontier held the solve lock ~25s | §8.4 interruptible background work; budget sweeps deferred with budget optimization (§5.2) |
| Doctor-after-every-write cost | §8.3: on demand + async after writes |
| Tolerance advice contradiction (`people.yaml`/`SKILL.md` vs PRD) | §8.3 playbook ordering decided; dev-corpus cleanup in M0 |
| Cooked `keeps_days` beyond USDA guidance baked into architecture | §8.1: guidance-following defaults; household overrides are data and labeled, not hidden |
| Two identical PRD copies drifting | Single `PRD.md`; v1 preserved once as `PRD-v1-household.md` |
| Paywalled-source recipes in the corpus *(v1 OQ-4 / I9 risk, not a scrutiny finding)* | §6 quarantine; OQ-C1 |

## Appendix B — Errata & explicit deferrals (M0 Phase 5 review, 2026-08-09)

Recorded during the M0 gate's adversarial review so spec and code stop quietly
disagreeing. Each item is a decision, not an omission.

1. **§5.2 cost statement corrected.** §5.2 says "the v1 solver reports cost
   but does not optimize on it." As built, menu *selection* is deliberately
   cost-aware: `score_menu` penalizes budget-ceiling overage
   (`budget_overage_per_dollar`) and mildly prefers cheaper menus
   (`cost_per_dollar`) — both named provisional weights in
   `engine.SCORE_WEIGHTS`. The `frontier` budget-sweep command also survived
   extraction and is retained as a dev tool (it runs synchronously; the §8.4
   interruptible-background posture applies when it reaches a service
   surface). What §5.2 defers is budget *optimization as a product feature*
   (budget↔outcome sweeps in the product loop), unchanged.
2. **Per-person `serve_g` scaling: implemented, measured, REVOKED (owner,
   2026-08-09 — the lard-beans incident).** M1.7 shipped kcal-proportional
   scaling; the same evening a real plan legalized 720g of refried beans in a
   day (400g authored cap × 1.8 clamp) and the owner rejected it on sight.
   Ruling: authored `serve_g` bounds are per-dish palatability **absolutes**
   ("more than this much of one dish is gross, period"), already written with
   the household's biggest eater in mind — kcal-scaling them double-counts
   appetite. The mechanism remains in the engine, dormant behind identity
   defaults, testable under explicit override; any future revival requires
   per-dish opt-in data (e.g. `scalable: true`), never blanket math. The
   Appendix A disposition for "serve_g shared across divergent eaters" is
   thereby closed as **working as intended**: appetite differences are served
   by which and how many dishes, not bigger single-dish piles.
3. **Pantry `acquired`-age rule deferred to M1.** §8.1's "age reduces
   effective raw keeps_days" is validated (ISO date required) but consumed
   nowhere: `purchase()` deducts stock grams only, and `raw_freshness`
   treats deducted stock as bought fresh at the nearest prior shop day.
   Deferred to M1 (TASKS M1.8) together with the already-documented pantry
   `cooked` planning integration.
