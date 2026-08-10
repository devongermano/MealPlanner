# M1.9 — Meal Allocation Layer: Final Design Spec

**Status:** ratified by synthesis over three proposals and two judge verdicts. Chosen base: **Post-Solve Dealer (zero-LP combinatorial `deal_day`)**, with grafts from the MILP proposal (precheck, short-circuit, stage tag, escape hatch) and the meal-native proposal (doctor section, sub-serving diagnostic, rebalance ordering). This spec is the implementation contract.

## 0. Decision and rationale

Both judges independently rejected the meal-native rewrite of `build_week` (wrong milestone: contradicts PRD §4.0's dealt-from-solved-day model, moves demand/purchasing/cost and every golden one milestone before the M1.6 real-week gate). Both winners deal the **already-solved, authoritative day plate** into n meals. Between them:

- **Chosen: zero-LP greedy dealer** (Judge 1 winner, 9.2; Judge 2 runner-up, 8.2 — combined highest). Decisive factors, in order: (a) **byte-determinism with zero CBC exposure** — RNG-free pure Python, no solver tie-breaking in the meal golden, ever; (b) **exactly zero LP-solve delta** — SOLVE_COUNTS and the M0.14 baselines untouched in a suite whose budget is explicitly tight; (c) **better failure floor** — flagged-but-composed meals always ship, versus the MILP's "CBC hiccup ⇒ whole day rendered as an unallocated pool, exit 2"; (d) both designs are equally capped by plate composition, so the MILP's optimality delta is bounded ("under a tortilla") while its costs land in the project's two most expensive currencies.
- **The MILP is the documented escape hatch** (both judges grafted this pairing), behind the same `deal_day` signature, with a **named, data-forced escalation criterion** (§10).

The day solve's authority is absolute: `deal_day` never re-solves, never mutates the plate, and conserves it gram-for-gram. It can never make a day wrong — only make meals uneven, and unevenness is always flagged (P6/P8).

## 1. Architecture — the one canonical computation (P10)

New module: `/Users/devon/Desktop/MealPlanner/services/solver/mealplan/meals.py`.

```python
def deal_day(person, comps, day_plate, slots, *, config=None) -> MealDay
```

- `person`: the `Person` (dict-view) — `meals_per_day` is now LIVE (§2).
- `comps`: component map (role, per100, unit_g, cuisine, serve_g from `model.py`).
- `day_plate`: the solved `{component_id: int_grams}` for this person-day, exactly as `build_week` emits it (engine.py:949-1026; int grams, <5g items already dropped by `plate()`). **Authoritative. Never modified.**
- `slots`: resolved slot list (§2), `len(slots) == n`.
- `config`: optional overrides merged over `MEAL_WEIGHTS` (§5), same pattern as `score_menu`'s `score_weights` override.
- **No `seed` parameter.** The dealer is RNG-free by construction (all orderings are explicit sort keys; first-improvement scans in fixed order). An unconsulted seed would violate the project's own dead-config discipline (Judge 1 dissent — accepted). If multi-start polish is ever added, the seed is added then, as a live parameter.

Called once per person-day by the week pipeline **after** `build_week` returns. Every consumer — lockplan (M1.3 plan.yaml gains meal structure: meals are plan content per PRD §4.0, not rendering), eat sheets and the M1.10 portioning matrix (`artifacts.py`), and day rebalance — consumes `MealDay` records. Nothing re-derives meal structure anywhere else (P10). The portioning matrix is a pure reshape: join meal items with the canonical M0.4 session attribution → (session, component) → [(person, day, slot, grams)].

**When no person sets `meals_per_day`, the entire layer is inert and the pipeline is byte-identical to today** — all existing goldens survive unchanged.

## 2. Data model and schema changes (one commit with the dealer — dead-config registry discipline)

`model.py`:

1. `RESERVED_FIELDS` (model.py:44) → `frozenset({"period"})`. Update the M0.5 comment block and the `Person` docstring (model.py:153-155): `meals_per_day` is a live engine input per PRD §4.0.
2. `Person` gains:
   - `serving_model: "portioned" | "family_style"` — default `"portioned"` (**provisional P9**: the meal-prep model is the product's stated model; family_style is the named heritage mode). Validated enum.
   - `meal_slots: Optional[list]` — `[{name: str, serving_model?: enum, interchangeable?: bool}]`. Slots inherit the person's `serving_model` unless overridden (Amendment 2). `interchangeable` defaults false (opt-in, never default — Amendment 2).
3. Resolution rules (applied in `Person.from_raw`/io_yaml validation, the one schema-defaults layer):
   - `meals_per_day` set, `meal_slots` absent → generate slots named `meal_1..meal_n` (do not guess breakfast/lunch/dinner semantics — open question §11).
   - Both set → `len(meal_slots) == meals_per_day` or validation error (all-errors reporting, exit 3).
   - `meal_slots` set alone → `n = len(meal_slots)`.
   - Neither → no meal layer for this person.
4. Serving models change **rendering only** (PRD §4.0: "share the entire solve"; P7: grams are canonical). `portioned` → portioning-matrix rows; `family_style` → "take this much" eat-sheet phrasing via the existing M1.2 household-unit/error-bar machinery. The solve and the dealer are identical for both.
5. New load-time validation warning (`meal_side_mass_missing`): a person with `meals_per_day ≥ 2` whose eligible library has zero splittable side mass (no starch/veg they can eat, or roles misauthored all-main) — names the fix before anyone sees a flagged week.

## 3. Algorithm

### 3.0 Precheck (pure arithmetic, zero solves) — the declared regime

Before dealing, compute from the plate: distinct mains `M`, component count `C`, splittable side mass `S_g`, and the main-repeat cap `r = ceil(n / max(M, 1))`. Emit the regime as structured notes **up front** (one seam for tests to assert on):

- `n == 1` (or `meals_per_day` unset for this person): **short-circuit** — return the trivial one-meal `MealDay` (whole plate, composition rule waived at n=1, documented) **through the same output shape and code path** (a fast path of the one computation, not a second algorithm).
- `C == 0`: empty day (explained hole from build_week) → `MealDay` with zero-item meals plus the day's existing hole explanation; no new failure invented.
- `C == 1`: `meal_composition_impossible{day, reason}` — the single component is dealt across slots on its grid (the day is still eatable), every meal flagged.
- `M < n`: `variety_unmet{mains_available: M, slots_required: n, repeat_cap: r}` — declared before dealing (PRD Amendment 2's variety obligation is "where the menu allows"; this is explanation, not failure).

### 3.1 Phase A — skeleton deal (exact combinatorial)

1. **Classify.** Mains and accents are ATOMIC (each lands whole in one meal). Starch/veg are SPLITTABLE pools — divisible across meals on their emission grid (`unit_g` for discrete, whole grams otherwise) with floor `MIN_SUBPORTION_G`. Drinks atomic.
2. **Anchor mains.** Sort mains deterministically (grams desc, id asc). If `M ≥ n`: the n largest become slot-mains (distinct main per slot — intra-day variety holds by construction); surplus mains demote to protein sides of the meal with least protein so far. If `M < n`: mains split round-robin across slots on their grid (the precheck already emitted `variety_unmet`).
3. **Guarantee sides.** Every meal receives ≥1 side: side atoms first, else carved from the largest splittable pool (grid-aligned, ≥ `MIN_SUBPORTION_G`; if the remaining pool is smaller than the floor, deal it whole to one meal with a `subportion_floor_relaxed` note). A meal that would be single-component with zero side material on the entire plate emits `composition_unsatisfiable{slot, missing_role, plate_components}` — the meal still ships, loudly flagged (P6/P8), never silently a bucket.
4. **Attach accents/drinks** (never alone — structurally guaranteed since every meal already has a main): accent joins the meal whose slot-main shares its `cuisine`; no match → lowest-kcal meal; ties → lexicographic component id.

### 3.2 Phase B — macro balance (zero-LP greedy + bounded polish)

- Atoms fix each meal's base macro vector. Splittable pools are the balancing fluid: allocate in `QUANTUM_G` steps (unit_g for discrete components) — each quantum to the meal with the largest normalized macro deficit vs target `T_m/n` per macro (daily targets are per-person constants, so slot targets are constant).
- **Integerization (graft, Judge 2):** remainder grams/quanta are dealt largest-deficit-first in fixed sort order (grams desc, id asc). Per component, meal grams sum **exactly** to the day-plate grams, in int grams — a hard `assert` (the conservation invariant, §9).
- **Polish:** bounded first-improvement local search, ≤ `POLISH_MAX_ITERS` (200), fixed scan order, RNG-free. Moves: transfer one quantum between meals; swap two side atoms; swap two slot-mains. Accept iff objective `J` strictly drops. `J = Σ_meals Σ_macros max(0, |meal_macro − T_m/n| − MEAL_BAND · T_m/n)` — only violations **beyond** the soft band score, plus an epsilon within-band smoothness tiebreak.
- **Bands are SOFT and reported, never enforced by relaxation** (P8): when a band cannot be met (one 800-kcal composite main into 3 meals), emit per-meal per-macro `band_unmet{slot, macro, achieved_g, band: [lo, hi], responsible_component}` and ship the honest split. Discrete `unit_g` atoms are the expected violation source; the flag names them so the fix (smaller unit, menu change) is actionable.

### 3.3 Sub-serving diagnostic (graft, both judges)

`serve_g` is authored per SERVING but the day pool applies it to day totals (the meal-native proposal's sharpest observation — recorded, not adopted). When a dealt meal portion of a component falls below that person's `effective_serve_bounds` min (engine.py:138), attach `sub_serving{slot, component, portion_g, serve_min_g}`. Diagnostic/rendering only, zero constraints — it pre-collects the M1.6 evidence for ever changing the semantics.

### 3.4 Interchangeable containers (opt-in; separately gated sub-deliverable **M1.9-IC**)

Per Amendment 2: household/slot choice, **never default**; trades variety for grabbability. For each slot with `interchangeable: true`: cross-day post-pass permuting which composed meal occupies the slot each day + re-running Phase B quantum transfers, minimizing within-slot cross-day macro spread. Best-effort minimization, not a guarantee: report achieved spread per (slot, macro); outside `INTERCHANGE_EPSILON` → `interchange_unmet{slot, macro, spread, epsilon, days_out}` with the numbers (day-target cycling per future M1.11 makes exact equalization impossible by construction — say so). The output **explicitly states variety is subordinated for that slot** (graft, Judge 2). Forward-compat: the equivalence-class grouping key is a parameter (`(slot,)` now, `(slot, day_type)` at M1.11). **Gate:** lands only after the core dealer's conservation + determinism tests are green; may slip to M1.11 without blocking M1.9 or M1.10.

### 3.5 Upstream picker nudge (zero-LP)

In `build_week`'s candidate-picking loop (engine.py:1006-1011), when this person has `meals_per_day` set, add a dealability term to the existing `used_days²` score: prefer candidate plates with ≥ `min(M_avail, n)` distinct mains and ≥ `n × MIN_SUBPORTION_G` splittable side mass. Weights `dealability_mains` / `dealability_side_mass` live in `MEAL_WEIGHTS`. Selection among already-solved candidates — LP count untouched. Guard: term contributes 0 when `meals_per_day` unset ⇒ existing goldens byte-identical.

## 4. Output shape

```python
MealDay = {
  "meals": [ { "slot": str, "serving_model": "portioned"|"family_style",
               "items": {component_id: int_grams},      # Σ over meals == day_plate, exactly
               "macros": {p, f, c, kcal},               # Atwater, from per100
               "target": {p, f, c},                     # T_m / n
               "flags": [structured dicts]  } ],
  "notes": [structured dicts]                            # day-level: regime, variety_unmet, ...
}
```

Ordered by slot. JSON-ready (mealplan/v2 envelope; flags in the established warning-dict style with `code` + `message` + data fields).

## 5. Constants — `MEAL_WEIGHTS` registry in meals.py

Mirrors `SCORE_WEIGHTS` (engine.py:91): named, documented, override-mergeable, every value **provisional (P9)**, never inline:

| constant | default | meaning |
|---|---|---|
| `MEAL_BAND` | 0.30 | soft per-meal band, fraction of `T_m/n` per macro (range 0.25–0.35 considered; ratify at M1.6) |
| `MIN_SUBPORTION_G` | 40 | floor for a carved splittable sub-portion (no rice dust) |
| `QUANTUM_G` | 25 | Phase-B allocation step for non-discrete pools |
| `POLISH_MAX_ITERS` | 200 | local-search bound |
| `INTERCHANGE_EPSILON` | 0.10 | acceptable within-slot cross-day spread, fraction of slot target |
| `dealability_mains` | 500 | picker-nudge weight (scale of the `used_days²` score) |
| `dealability_side_mass` | 300 | picker-nudge weight |
| `BAND_ESCALATION_THRESHOLD` | 0.20 | §10 — fraction of person-days with `band_unmet` in a real week that forces the MILP escalation decision |

## 6. Failure / explanation vocabulary (complete list; P6/P8)

`variety_unmet`, `composition_unsatisfiable`, `meal_composition_impossible`, `band_unmet`, `sub_serving`, `subportion_floor_relaxed`, `interchange_unmet`, plus load-time `meal_side_mass_missing`. Rules: meals **always ship** (no unallocated pools, no holes invented by this layer); nothing is silently widened, dropped, or relaxed; every degradation carries the numbers. Eat-sheet rendering of `band_unmet` is human phrasing naming the responsible component ("dinner is the heavy meal today: +32g fat vs slot target — carnitas"), an explanation, not an error dump.

## 7. Consumers and ordering (pinned now, not decided by whoever wires it first)

- **Rebalance:** `replate()` (engine.py:914) output feeds `deal_day` exactly like `build_week` plates — re-solve the day, then re-deal it. Pins stay **day-level** in M1.9; a slot-qualified pin, if any consumer passes one, is rejected with a named `slot_pin_unsupported` warning (declared out of scope).
- **Lockplan:** `MealDay` records serialize into plan.yaml; the inputs-hash covers `meal_slots`/`serving_model`/`meals_per_day` automatically (verbatim inputs snapshot). `verify-plan`'s re-solve re-deals and compares.
- **doctor():** gains a mandatory arithmetic-only (zero-LP) per-person meal section: worst-day distinct-mains-available vs n; worst-day splittable side mass vs `n × MIN_SUBPORTION_G`; whether `T_m/n ± MEAL_BAND` is spanned by any partition of the worst-day plate's atoms (bound check, not a solve); expected `variety_unmet` day count. Meal-layer infeasibility is explained at the layer it occurs (P6), before a full build.

## 8. Solve budget, determinism, instrumentation

- **CBC delta: exactly zero.** No PuLP import in meals.py. SOLVE_COUNTS and M0.14 baselines untouched.
- **Runtime:** Phase A O(m log m) over ≤~8 components; Phase B ~(pool/quantum)×n allocations + ≤200 O(n²) polish moves — sub-millisecond per person-day; a 4-person week ≈ 28 deals, single-digit ms.
- **Instrumentation (graft):** new `meal-alloc` timing stage in instrument.py (timing only, zero solves) + a provisional-labeled row in BASELINES.md via `make baseline` — pure-Python regressions must be visible. Never test-asserted.
- **Determinism:** RNG-free; every ordering an explicit sort key; same inputs ⇒ byte-identical `MealDay`. The pinned-env determinism golden extends to meal output with no new tie-breaking surface.

## 9. Test plan

**Fast tier** (unit, no CBC, milliseconds):
1. **Conservation property test — the load-bearing invariant:** deal then re-sum equals the day plate gram-for-gram (int), over deterministic randomized fixture plates (seeded generator, wide shapes: discrete units, single-main, mains-only, tiny pools). Named suite fixture.
2. Precheck regimes: `M≥n`, `M<n`, `M=0`, `C≤1`, empty plate — assert exact regime notes.
3. n=1 / `meals_per_day=1` short-circuit: identity through the same output shape; **zero** Phase-B work.
4. Composition: every meal has exactly one main-anchor + ≥1 side where the plate allows; accents/drinks never alone; surplus-main demotion; `composition_unsatisfiable` on a mains-only plate.
5. Variety: distinct mains when `M≥n`; round-robin + `variety_unmet` when `M<n`.
6. Band flags: constructed heavy-atom plate ⇒ `band_unmet` with correct signed values and responsible component; in-band plate ⇒ no flags.
7. `sub_serving` emission against `effective_serve_bounds`.
8. Determinism: two calls byte-identical; polish move-order sensitivity guard (golden a nontrivial deal).
9. Dead-config registry: `meals_per_day`, `serving_model`, `meal_slots` (incl. `interchangeable`) all consumed; `RESERVED_FIELDS` shrunk. Validation: slot-count mismatch, enum, `meal_side_mass_missing`.
10. Legacy inertness: no `meals_per_day` anywhere ⇒ pipeline output byte-identical (guards the picker nudge too).

**Slow tier** (pipeline, CBC — **rule, promoted from the proposals: fixtures set `meals_per_day` only where the test targets the meal layer**, so the solve budget never leaks into every fixture):
1. One end-to-end: family_four fixture + `meals_per_day` → build → deal → lock → verify-plan round trip; meal golden on the pinned reference env.
2. Picker-nudge effect: with meals set, chosen plates carry ≥ min(M, n) mains (structural assertion, not a golden).
3. M1.9-IC (when gated in): interchange spread reduction + `interchange_unmet` reporting under a shelf-life-valley fixture.
4. `make baseline` records the `meal-alloc` stage.

## 10. Escape hatch and escalation (spec, not folklore)

The per-day meal MILP (hard conservation `Σ_s x[c,s] == G[c]`, composition binaries `y[c,s]`, min-split lower bounds, soft slack-dominant bands, seeded epsilon tie-breaks, one tiny CBC solve per person-day tagged `meal-alloc`) is the **documented upgrade** behind the identical `deal_day` signature. Write its sketch into meals.py's module docstring. **Escalation criterion (data-forced, not vibes):** if the M1.6 real week — or any M1.6-class real week — shows `band_unmet` on more than `BAND_ESCALATION_THRESHOLD` (20%, P9) of person-days, or chronic `composition_unsatisfiable`, the MILP decision is forced onto the owner's desk with the flag counts as receipts.

## 11. Open questions

1. **Pack-at-thaw (→ M1.10, blocking for the portioning matrix):** freezer-bridged days (`from_freezer`, engine.py:900) and M1.8 cooked-leftover days cannot be packed into per-meal containers on cook day. The portioning matrix must mark those (person, day, slot) rows pack-at-thaw — recorded here as a named spec question for M1.10, never silently packed. Interchange reuse vs `session_plan` leftover allocation order must also be pinned in M1.9-IC to avoid double-counting grams.
2. **Default slot names:** `meal_1..n` vs inferring breakfast/lunch/dinner at n=3 — owner call; pure rendering, zero engine impact.
3. **`serving_model` default = portioned** — provisional; confirm with owner (family_style is the v1-heritage household's likely setting).
4. **`MEAL_BAND` = 0.30** — ratify against the M1.6 real week (proposals argued 0.25 vs 0.35; softer chosen to avoid a flag factory before real data exists).
5. **M1.9-IC gate timing:** in-milestone after core tests green, or slip to M1.11 — implementer's call once core lands; nothing downstream blocks on it.

**Key file anchors for the implementer:** `services/solver/mealplan/engine.py` — `plate()` :201, `SCORE_WEIGHTS` :91, `effective_serve_bounds` :138, `diverse_plates` :861, `replate` :914, `build_week` :949, picker loop :1006-1011, seed-derivation scheme :1007. `services/solver/mealplan/model.py` — `RESERVED_FIELDS` :44, `Person` :148-186. New module: `services/solver/mealplan/meals.py`. Schema/validation: `io_yaml.py`; instrumentation: `instrument.py`; consumers: `lockplan.py`, `artifacts.py`.