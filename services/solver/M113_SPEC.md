# M1.13 — The Dish Layer: Final Design Spec

**Status:** ratified by synthesis over three proposals (Dish-Native Day MILP; Lineup-Projected Dish Dealing; Skeleton-then-Solve) and two judge verdicts. Chosen base: **SKELETON-THEN-SOLVE** (deterministic zero-LP dish assignment + one dish-blocked LP per person-day), with the **continuous servings scalar and within-dish LP mechanics grafted from the Dish-Native MILP**, and migration/lint/capacity grafts from LPDD. This spec is the implementation contract for the implementing agent.

## 0. Decision and rationale

The owner's correction is the requirement, verbatim: *"These aren't meals — they're random ingredients served together… how is that following an actual recipe I gave you?"* A meal is **one dish, portioned within its per-serving ratio bands, plus compatible sides — never a free mix of the week's pool.** This *supersedes any framing of meals as component pools* (PRD §4.0).

**Ruling on the hard question (1) — where dish assignment happens:** dish structure moves **INTO the solve**. All three panelists and both judges converged on the structural fact, verified against `engine.py`: `plate()`'s per-component binaries `b_i` (engine.py:250–262) have **no term coupling shells to picadillo** — a free-pool day solve generically produces proportions that fit **no** dish partition, and a post-hoc dealer can only flag that, not fix it. LPDD (pool solve + dealing-time structure) was rejected by both judges as base: its named residues (`dish_member_dropped` = gorditas without shells, `dish_overflow_side`, `dish_ratio_unmet`) are the owner's complaint shipped with receipts. The dealer's co-occurrence assumption is false by decree; dish identity must be a constraint the solver sees.

**Between the two dish-in-the-solve designs**, the split is only *where dish SELECTION lives*: inside CBC (P1's ~30–70-binary day MILP) or in a deterministic zero-LP skeleton (P3). Chosen: **the skeleton**, for the reasons that decided M1.9 and still hold (combined judge scores: P3 17.4, P1 17.2):

- **Identity guarantee is equal.** In both designs dish ratio bands are *hard LP bounds* — the owner's requirement cannot be violated by either, only honestly missed at the macro layer with flags. The architectures differ only in assignment *quality*, and a skeleton misjudgment degrades to a P6-explained macro miss, never to a non-dish meal.
- **Risk asymmetry, one gate before M1.6.** The dish LP here has a binary count comparable to today's `plate()` (side/accent semicontinuity only — dish choice is fixed before CBC ever runs). P1's wider MILP puts combinatorial logic where it is untestable without CBC and widens the pinned-env golden's sensitivity surface — the project's two most expensive currencies (the ratified M19 decisive factors).
- **House pattern (M19 §0, ratified):** conservative deterministic base, ambitious escape hatch pre-specced, **data-forced escalation**. The dish-binary day MILP is the documented upgrade (§14), with its formulation written down and its trigger bound to measured flag counts — not folklore.
- **The synthesis graft that closes P3's main weakness:** Judge 2's own graft list flagged P3's integer-servings gap (a target stranded between s and s+1). This spec **replaces integer servings with P1's continuous servings scalar `t` inside the LP** — legal at zero binary cost precisely because the skeleton has already fixed *which* dish. The skeleton picks dish identity; the LP picks *how much*, continuously, coordinated across slots by the day tolerance. Judge 1's core objection to P3 ("skeleton picks servings on kcal-only arithmetic") is thereby largely dissolved: the only remaining heuristic exposure is dish *choice*, mitigated by the retry ladder, capacity-aware scoring, and instrumented escalation.

**Accepted from Judge 2's dissent as a hard requirement:** retry-count and `dish_band_binding` rates are instrumented on synthetic fixtures **before** M1.6 (§13), so the P1 escalation can be forced by data a milestone early if the skeleton is weaker than argued.

The moat survives with structure added: components remain the batch units; demand, batching, shopping, costing, session attribution, and freezer bridging are **unchanged in code and in kind** — same pots, different macros.

## 1. Architecture and pipeline

**Mode key = presence of `dishes.yaml` in the library.** Absent → the entire layer is dormant: `choose_menu` + `build_week` + the M1.9 dealer run **byte-identical** (all existing goldens survive; this is the permanent heritage mode, not a shim). Present → the dish path runs for the whole household (dishes are plan content, PRD §4.0; dish identity does not depend on meal slots).

New module: `services/solver/mealplan/dishes.py` — `Dish` load/validate, availability, eligibility, closure, lint, implicit-dish synthesis, the skeleton, `plate_dishes()`, `DISH_WEIGHTS`, and the escalation sketch in the module docstring.

Dish-mode pipeline per week:

1. `choose_menu` selects a **dish menu** (§3); the component closure derives.
2. Per person-day: `skeleton()` assigns one dish (+ side candidates, + accents) to each resolved slot (§4) — zero-LP, RNG-free, deterministic.
3. Per person-day: `plate_dishes()` — **one LP** (plus the standard discrete-snap re-solve) portioning within-dish per person (§5). Meals emerge directly from the solve; the day plate is the derived Σ over meals.
4. `demand[c] += grams` over all meal items exactly as `build_week` does today (engine.py:1088–1091). Everything downstream of demand — `costing.py`, `purchase`, session attribution (M0.4), freezer bridging, the M1.10 portioning matrix — consumes component grams and **never learns what a dish is**.

`diverse_plates` (engine.py:913) and `deal_day` (meals.py:119) survive untouched on the heritage path. `resolve_meal_slots` (model.py:219), the MealDay shape (M19 §4), `equalize_interchangeable`, family_style rendering, and all flag-consuming artifacts are shared by both paths.

## 2. `dishes.yaml` — exact schema consumption and validation

Consume the data steward's draft schema **as-is** (`data/dishes-draft/dishes.yaml`):

```yaml
dishes:
  gorditas_de_picadillo:
    components:                       # required members, per-SERVING bands
      gordita_shells: {base_g: 120, min_g: 90, max_g: 150}
      picadillo:      {base_g: 150, min_g: 110, max_g: 200}
    accents: [salsa_verde, queso_fresco]   # optional layer, droppable per person
    compatible_sides: [mexican_rice, refried_beans]
    meal_affinity: [breakfast]        # optional; slot-name labels
```

`model.py` gains a `Dish` dataclass mirroring this. Semantics:

- **Core member** = `components` entry with `min_g > 0`. A member authored `min_g: 0` is droppable per person (same dish without it).
- **Accents** are the optional layer — devon's "no queso" is a dropped accent (`a[queso]=0`), dish intact. Accents attach only to their dish.
- **Cuisine** is *derived* from the dish's main-role member's `cuisine` (no new required field; an optional additive `cuisine:` key is a non-blocking request back to the steward, consumed if present).
- **`meal_affinity`** labels match person slot *names* only. When a person's slot names don't include a dish's affinity labels, affinity is **inert** for that person with a load-time `affinity_slot_mismatch` note — breakfast semantics are never guessed (M19 §11.2 precedent).

**Validation** (`io_yaml`, all-errors reporting, exit 3):
- Every component/accent/side id resolves in the corpus; `accents` disjoint from `components`.
- `min_g ≤ base_g ≤ max_g` per member; all positive except `min_g ≥ 0`.
- **`dish_band_exceeds_serve` (graft, P1, both judges):** `min_g > serve_g.max` of the component → **error** with both numbers (one serving cannot exist inside the authored absolutes). `max_g > serve_g.max` → **warning** (the per-meal cap will truncate the band).
- Discrete (`unit_g`) members: `[min_g, max_g]` must admit ≥1 grid point at `t = 1` → else error (the `plate()` unit-alignment precedent; the snap pass handles alignment at `t ≠ 1`).
- **`dish_band_loose` lint (graft, P2, both judges):** warn when authored bands permit degenerate ratios — worst-case pairwise ratio error `(max_i/min_j)/(base_i/base_j)` above `DISH_BAND_LOOSE_RATIO` (3.0, P9). Hard bands are the identity guarantee; this lint keeps "in-band" meaning something.
- `meal_affinity` labels from a validated label set (any slot name in the household plus the reserved trio breakfast/lunch/dinner).
- Dead-config registry extended: every new field consumed or reserved.

**Availability / eligibility (pure arithmetic):**
- Dish `j` is **available on day d** iff EVERY core member is `available_on(d)` (engine.py:926; freezer bridging applies per component). A dish dies with its shortest-lived core member — `dish_unavailable{dish, day, component}` names the killer.
- Dish `j` is **eligible for person p** iff no core member carries a tag p excludes (`eligible`, engine.py:175). An excluded accent or `min_g: 0` member drops that member only; an excluded core member kills the dish for that person — `dish_excluded_by{person, dish, component, tag}`.

## 3. Menu selection over dishes

`choose_menu`'s chosen set becomes **dish ids**; the two-phase shape (cheap structural score run hot, LP-verify the shortlist; engine.py:848–907) survives verbatim. The **component closure** — union of chosen dishes' members ∪ accents ∪ `compatible_sides` — derives for everything cost/time/batch-shaped: `estimate_batches`, `purchase`, `cook_minutes`, `menu_cost` run on the closure unchanged (components are the batch units, so shared components consolidate in the estimate exactly as at cook time). `n` means dishes; a `must`-list component id maps through dish membership with a named warning.

`score_menu` (engine.py:735) translations, weight-for-weight against `SCORE_WEIGHTS`:

- **Role floors:** `role_floor_mains` → ≥3 dishes on the menu (every dish carries a main); `role_floor_starches` → ≥2 starches **in the side closure** (the "starches expire mid-week" lesson, engine.py:799–803, now checked against what can actually be *served* as sides); `role_floor_accents` → ≥2 over dish accents.
- **Per-person floors:** `person_mains_floor` → ≥3 **eligible dishes** (core-tag conjunction — sharper: one exclusion kills a whole dish, the score must see it); `person_starch_floor` → ≥3 eligible starches in the side closure; `person_accents_floor` → ≥2 eligible accents/veg (sides are now the fine-tuning lever).
- **Lean anchors (adopt P3's aggregate test — both judges endorsed it as fixing a real blind spot):** precompute each dish's per-serving achievable macro range from its bands (pure arithmetic at load). A dish is a **lean anchor for person p** iff its max-protein/min-fat achievable ratio ≥ `need_ratio × 1.25`. Floor = `settings.min_lean_anchors` lean *dishes*; `uncovered_lean_day` recomputed on **dish** availability (a lean main whose salsa dies day 3 no longer covers day 4 — strictly more honest). This makes the "composite dishes force fat over" capability (PRD §9) native.
- **Carb headroom** (day-correct, M0.11 form): worst-day ceiling = Σ over available+eligible dishes of `T_MAX_j · Σ_c max_g[c]·carb/100` + Σ available compatible sides' `serve_max·carb/100` ≥ target, else `carb_headroom_shortfall`.
- **Cuisine variety:** dish cuisine (derived, §2).
- **NEW — slot coverage** (`slot_coverage` weight, P9): for each person with affinity-bearing slots, every day needs ≥1 available eligible dish matching each such slot.
- **NEW — capacity (graft, P2/P3, Judge 2 graft 5):** scored penalty `slot_capacity_unmet`-style, zero-LP arithmetic: per person-day, Σ over a feasible slot lineup of dish max-scale kcal (`T_MAX_j` at `max_g`) + side serve-maxes must clear the day target. Menus that cannot feed jimbo are penalized **during search**, not merely diagnosed after.
- **Phase-2 verify:** per shortlist candidate, per person: skeleton-assign a representative day-0 availability set and run one `plate_dishes` feasibility solve (analogue of today's per-person `plate` verify, engine.py:891–903), returning who broke and which dish constraint bound — feeding doctor.

`doctor()` gains an **arithmetic-only dish section** (zero-LP, before any build — P6): eligible-dish count per person with the killing exclusions named; per-day dish availability with the shortest-lived core member named per dead dish; lean-dish coverage; dish carb headroom; and **`slot_target_unreachable`** (graft, P3/Judge 1 graft 4): per person, per slot — max achievable kcal from eligible dishes at `T_MAX` plus compatible-side serve maxima vs slot target. The jimbo check is load-time arithmetic, never a solve-time surprise. The existing `meal_layer` doctor section (engine.py:~690–729) re-bases its mains/side-mass checks on dishes in dish mode.

## 4. The skeleton — deterministic dish assignment (zero-LP)

`skeleton(person, dishes, menu, comps, settings, week_state)` — dealer-class discipline: RNG-free, every ordering an explicit sort key, byte-testable without CBC.

Per person, per day, per slot in fixed order (slots from `resolve_meal_slots`, model.py:219 — unchanged):

1. **Candidate set** `D_s`: dishes available on `d` (§2 conjunction), eligible for `p`, affinity-matched to the slot when the slot name is a recognized label (else affinity inert).
2. **Assignment score** (min wins; ties → dish id asc):
   - `used_days(dish)²` week-variety term (the same shape `build_week`'s picker uses today, engine.py:1073);
   - same-day repeat penalty — no repeated dish across a day's slots (PRD Amendment 2's variety obligation); when `|D| < n` slots force repeats, cap at `ceil(n/|D|)` with `variety_unmet{dishes_available, slots_required, repeat_cap}` declared up front (the M19 §3.0 precheck-regime vocabulary, computed on dishes);
   - `meal_affinity` mismatch penalty;
   - **capacity-fit term:** distance between the slot's kcal share and the dish's achievable kcal range `[T·Σmin_g·kcal/100 … T_MAX·Σmax_g·kcal/100]` — the skeleton's only macro arithmetic; the LP does the real portioning;
   - batch-coherence bonus for dishes whose components are already demanded this week;
   - dislike multipliers (any core member in `person.dislikes`).
3. **Side candidates:** nominate ≤ `SIDE_CANDIDATES` (3, P9) from the dish's `compatible_sides`, ranked by (predicted deficit-macro density, availability on `d`, `used_days`, id asc). Compatibility is **authored data only** — never inferred (inference would resurrect free mixing with extra steps).
4. **Accents** attach atomically from the dish's own `accents` list (eligible ones only).

**Retry ladder:** if `plate_dishes` returns infeasible for the day (§5), the skeleton retries with the next-ranked assignment for the binding slot, ≤ `ASSIGN_RETRIES` (3, P9). Exhausted → explained hole `no_dish_assignable{day, slot, tried, reasons}` (per-dish blockers named), through the existing explained-hole path. The relax-tier ladder concept (engine.py:1061–1064) maps to widening `used_days` caps tier-by-tier before retries burn.

**People without slots** (dish mode): uniform machinery, one implicit unlabeled day-block — skeleton may assign up to `DISHES_PER_DAY` (3, P9) dish blocks; each portioned within its own bands; **dish integrity holds unconditionally** ("never a free mix" — PRD supersession clause); no per-meal macro bands, day tolerance only; family_style rendering says "today: gorditas (1.8 servings) + rice; carnitas tacos (1.2)". Mixed households compose freely. People *with* slots in a **no-dish** library keep the M1.9 dealer unchanged.

## 5. The dish-blocked LP — `plate_dishes()` (one LP per person-day, stage `plate-dish`)

Variables and constraints (dish selection is FIXED by the skeleton — no dish binaries):

- **`t[s] ≥ DISH_T_MIN`** (0.5, P9) — continuous servings scalar per slot's assigned dish; **`t[s] ≤ T_MAX_j`**, derived = `min over core c of serve_g.max[c] / base_g[c]`. The authored palatability absolutes cap the scale-up; bounds never scale by appetite (the lard-beans ruling, `SCALING` dormant, engine.py:119–132).
- **Core grams `x[c,s]`:** `min_g[c,j]·t[s] ≤ x[c,s] ≤ max_g[c,j]·t[s]` — LINEAR (t continuous, band constants). *This is devon's gordita: more picadillo within the band.* Droppable members (`min_g=0`) simply have a zero lower bound.
- **Per-MEAL serve caps (graft, P1 / Judge 2 graft 1):** `x[c,s] ≤ effective_serve_bounds(c, p).max` per slot — `serve_g`'s semantics finally land where the owner authored them (per serving, not per day), retiring the M19 §3.3 `sub_serving` workaround at its root. (`sub_serving` diagnostic retained for the heritage path only.)
- **Accents:** semicontinuous `{0} ∪ [serve_min, serve_max]` via one binary each, attached to their dish's slot. "No queso" = binary 0, dish intact.
- **Sides:** per slot, semicontinuous grams for each nominated candidate (binary each; ≤ `SIDES_PER_SLOT_MAX` (2, P9) nonzero per slot), bounded by the component's own `effective_serve_bounds` and the `MIN_SUBPORTION_G` floor (reused from `MEAL_WEIGHTS`). Sides are the **balancing fluid** — per-person macro divergence lands on sides and within-dish bands; same pots. A side shared by two dishes consolidates in batching automatically because demand is summed per component.
- **Day macro tolerance:** soft, slack-dominant — today's `plate()` contract verbatim (targets ± person tolerance, over/under slacks, engine.py:270–277); `max_daily_mass_g` cap unchanged (engine.py:264).
- **Per-slot macro bands:** soft at `T_m/n ± MEAL_BAND` (reused from `MEAL_WEIGHTS`, 0.30 P9), slack weight strictly below day slack, above tie-breaks.
- **Objective** (registry hierarchy, `SCORE_WEIGHTS` style): `plate_slack_dominance` × day slack ≫ meal-band slack ≫ dislike-weighted gram tie-break ≫ seeded epsilon perturbation on side/accent binaries (deterministic per `(component, slot)` hash of the explicit seed — the M19 §10 device) so symmetric side choices can't flap on CBC tie-breaking.
- **Two-pass discrete snapping** (same shape as `plate()`, engine.py two-pass): pass 1 solves; pass 2 fixes binaries, snaps `unit_g` components to whole units clamped into `[t*·min_g, t*·max_g] ∩ serve caps`, re-solves remaining continuous grams. Snapping can shift grams within a dish but can never change which dish — no identity flapping.

**Output:** `MealDay` records (M19 §4 shape) emitted **directly from the solve** — each meal gains a `"dish": dish_id` field (additive; heritage-path meals carry `"dish": null`). The day plate is the derived Σ over meals. `compose_day()` is the ONE canonical producer (P10): dish mode → solve-native meals; no-dish libraries → the M1.9 dealer, byte-identical.

**Infeasibility** (hard set empty at runtime despite validation): return to skeleton retry (§4).

## 6. Band-conflict precedence — a hard ruling, not a weight fight

1. **DISH RATIO BANDS AND PER-MEAL SERVE CAPS ARE HARD.** They are recipe identity plus the same class of authored palatability absolutes the owner ratified in the lard-beans revocation ("more than this much of one dish is gross, period"). They never carry slack. An engine that relaxes them re-creates "random ingredients" quantitatively.
2. **DAY MACRO TOLERANCE is soft with dominant slack** — the existing contract: the solve ships the best reachable day and reports signed misses (`ok=False` path, exit-2 semantics unchanged).
3. **PER-MEAL BANDS are softest** — `MEAL_BAND` slack, reported as `band_unmet`.

When they fight, dish integrity wins and the flags say why **with receipts** (graft, P1 / Judge 2 graft 2): `band_unmet` keeps its M19 shape and gains `responsible_dish` + `binding_bound`, read off the frozen pass-2 LP's **active constraints** (cheap and exact). New day-level flag `dish_band_binding{macro, dish, component, bound, delta_g}` when a day miss is attributable to a hard band: *"protein short 22g — carnitas tacos maxed at 2.4 servings (carnitas at 180g/serving max)."* Nothing silently widened; the failure explains itself at the layer it occurs; the actionable fix (wider band, another dish, richer sides) is named. Escalation reuses `BAND_ESCALATION_THRESHOLD` (0.20, P9): >20% of person-days with `band_unmet`/`dish_band_binding` in an M1.6-class real week forces the owner decision with flag counts as receipts.

Note the failure mode **inverts** vs the dealer, exactly as ordered: the dealer produced in-band days with incoherent meals; this design produces coherent dishes whose macro bands may miss, flagged.

## 7. Big-eater policy (jimbo, ~1500 kcal/meal) — ordered ladder, nothing silent

1. **Scale the dish within bands** — default. `t[s]` is continuous; a 1500-kcal slot pulls `t` up, preserving per-serving ratios (that *is* "portioned within its ratio bands" at scale). Ceiling: the authored `serve_g` absolutes via `T_MAX` and the per-meal caps. Appetite buys more servings until palatability caps bind; bounds never scale by appetite.
2. **Hearty compatible sides** absorb the remainder — the LP routes surplus into rice/beans/tortillas listed as compatible, each within its own serve band. This is why `compatible_sides` authoring quality matters for big-eater households; doctor's dish section says so when side headroom is thin.
3. **Two dishes in one slot — explicit opt-in only, never automatic.** Per-person/per-slot config `max_dishes_per_slot` (default 1; validated; dead-config registry). When set to 2, the skeleton assigns a second, cuisine-compatible dish to the slot when capacity arithmetic requires it, flagged `second_dish_added{slot, dishes, kcal_gap}`; both instances carry full band integrity in the LP ("carnitas tacos AND a quesadilla" — two real dishes, never a mix). Default stays 1 because PRD §4.0 is verbatim "one dish + compatible sides"; widening is a household choice in the Amendment-2 mold.

When 1+2 can't reach the slot target and 3 isn't enabled, the day ships with `meal_underfilled{slot, shortfall_g_by_macro, binding_caps, options}` naming the binding serve caps and the three remedies. **Owner question (decision-forcing, per house style):** should chronic `meal_underfilled` auto-enable second dishes? Put on the owner's desk with the capacity arithmetic as trigger evidence — not decided by the engine.

## 8. Conservation — what is conserved

- **Day totals remain the authoritative macro contract:** targets are daily, tolerance is the person's, misses are day-level, exit-2 semantics unchanged — the day tolerance is a constraint IN the one solve, coordinating across slots.
- **The day plate becomes a derived aggregate:** meal structure moves into the solve; `Σ meals ≡ day plate` is an arithmetic identity. The dealer's hard conservation assert becomes definitional — **kept as a cheap sanity check**, joined by the new load-bearing structural assert: *every meal's non-side items are exactly its assigned dish's members within `[t·min_g, t·max_g]` ∩ serve caps.*
- **Downstream interface conserved bit-for-bit:** `demand[c]` summed from meal items feeds costing/purchase/session-plan/portioning-matrix exactly as today; those layers never learn dishes exist. Cross-dish consolidation of shared components (one rice pot feeds two dishes) is automatic.

**Consumers:** lockplan — `MealDay` (+ `dish` field) serializes into plan.yaml; the inputs-hash covers `dishes.yaml` automatically (verbatim inputs snapshot); `verify-plan` re-runs skeleton+LP and compares. **Replate** — re-solve the person-day dish LP with pins clamped into dish bands; **slot-qualified pins become natural** (the variable is `x[c,s]`) — M19's `slot_pin_unsupported` restriction lifts in dish mode. A dish veto re-runs the skeleton for affected days then re-solves; no alternative → `no_alternative_dish` explained. **Interchangeable containers** (M1.9-IC): unchanged — permutes whole composed meals across days within a slot; dish identity travels with the meal.

## 9. Migration and inertness

- **Inertness boundary:** no `dishes.yaml` ⇒ byte-identical pipeline (guard test, M1.9-style golden). This is the corpus's migration path and permanent heritage mode.
- **`--implicit-dishes` synthesis (graft, P2 — both judges):** during the steward's reconstruction window, synthesize one dish per legacy main — main + its `pairs_with` accents as `accents`; all eligible starch/veg as `compatible_sides`; `min_g/base_g/max_g` lifted from `serve_g` min/midpoint/max — each flagged `implicit_dish` in doctor so the steward's real reconstruction work stays measurable against the shim. A main in no dish (and not synthesized) → `component_unreachable` load-time **warning** (not error — incremental authoring must not brick a library): unschedulable as a meal anchor, excluded from menu candidacy, fix named.
- **Orphan sides — `dish_layer: permissive | strict` settings enum (graft, P3):** `permissive` (default until M1.6, P9): starch/veg referenced by no dish's `compatible_sides` may serve as sides of any dish, flagged `orphan_side`. `strict`: unlisted sides unservable. The documented migration ratchet is flipping the default to `strict` once the steward's `compatible_sides` coverage lands.
- **Orphan accents** (in no dish's `accents` list): `orphan_component` warning, unservable (accents were always dish-bound).
- Additive-only requests back to the steward: optional `cuisine:` per dish; per-member unit-grid alignment inherits from the component. Nothing blocking.

## 10. Failure / explanation catalog (complete; P6/P8)

Meals always ship as real dishes; degradations carry numbers; nothing silently widened, dropped, or relaxed.

| flag | layer | payload |
|---|---|---|
| `dish_band_exceeds_serve` | load (error) | dish, component, min_g, serve_max |
| `dish_band_loose` | load (warn) | dish, worst-case ratio, threshold |
| `component_unreachable` | load (warn) | component, fix |
| `implicit_dish` | load (warn) | synthesized dish, source main |
| `orphan_side` / `orphan_component` | load/deal (warn) | component |
| `affinity_slot_mismatch` | load (note) | dish, labels, person slots |
| `slot_coverage_unmet` / capacity penalty | menu score | person, day, slot |
| `slot_target_unreachable` | doctor (arithmetic) | person, slot, achievable_kcal, target |
| `dish_unavailable` | skeleton | dish, day, killing component |
| `dish_excluded_by` | skeleton | person, dish, component, tag |
| `variety_unmet` | skeleton (reused M19 shape) | dishes_available, slots_required, repeat_cap |
| `no_dish_assignable` | skeleton (explained hole) | day, slot, tried, per-dish blockers |
| `no_alternative_dish` | replate | day, vetoed dish |
| `second_dish_added` | skeleton (opt-in mode) | slot, dishes, kcal_gap |
| `meal_underfilled` | solve | slot, shortfall_g_by_macro, binding_caps, options |
| `dish_band_binding` | solve (day-level) | macro, dish, component, bound, delta_g |
| `band_unmet` | solve (reused M19 shape) | + responsible_dish, binding_bound |

Heritage-path flags (`composition_unsatisfiable`, `sub_serving`, `subportion_floor_relaxed`, …) unchanged.

## 11. Constants — `DISH_WEIGHTS` registry in dishes.py

Mirrors `SCORE_WEIGHTS`/`MEAL_WEIGHTS`: named, documented, override-mergeable, **every value provisional (P9)**: `ASSIGN_RETRIES` 3 · `SIDE_CANDIDATES` 3 · `SIDES_PER_SLOT_MAX` 2 · `DISHES_PER_DAY` 3 (slotless) · `DISH_T_MIN` 0.5 · `DISH_BAND_LOOSE_RATIO` 3.0 · variety/affinity/capacity/batch-coherence skeleton weights · `slot_coverage` + capacity penalties merged into `SCORE_WEIGHTS`. Reused: `MEAL_BAND`, `MIN_SUBPORTION_G`, `BAND_ESCALATION_THRESHOLD` from `MEAL_WEIGHTS`. `max_dishes_per_slot` and `dish_layer` are validated schema/settings fields in the dead-config registry.

## 12. LP delta, determinism, instrumentation

- **LP delta: bounded, net NEGATIVE for dish-mode weeks.** Today each person-day-tier runs `diverse_plates`' manufacture loop — up to k×8 = 80 `plate()` calls × 2 CBC solves (engine.py:916–923) — because composition choice lived outside the LP. Dish mode replaces that with ≤ 2 CBC invocations per person-day per skeleton attempt (LP + snap re-solve), ≤ `ASSIGN_RETRIES`+1 attempts, typically 2 solves total; variety pressure moves into the skeleton's arithmetic. Binary count per solve ≈ sides+accents (≤ ~12) — comparable to today's `plate()`. **No-dish libraries: exactly zero delta.**
- **Instrumentation:** new `SOLVE_COUNTS` stage `plate-dish` + timing stages `dish-skeleton` via `solve_stage`/`instrument.py`; new BASELINES.md rows via `make baseline` (provisional labels, never test-asserted). **Pre-M1.6 requirement (Judge 2 dissent, accepted):** record skeleton retry counts and `dish_band_binding` rates on synthetic dish fixtures so the §14 escalation can be data-forced early.
- **Determinism — exposure narrows:** the skeleton is dealer-class RNG-free; dish mode *deletes* an RNG consumer (`diverse_plates`' `rng.uniform` draws) for dished corpora. The seed's remaining dish-mode job is the epsilon tie-break on side/accent binaries. CBC is the same determinism surface `plate()` already has; the pinned-reference-env determinism golden extends to dish output. Same inputs + seed ⇒ byte-identical plan.
- **Suite budget:** dish fixtures only where tests target the dish layer (M19 §9 rule, reapplied); fast tier covers all combinatorial logic with zero CBC.

## 13. Escalation and downgrade (M19 §10 house pattern — spec, not folklore)

Write BOTH into `dishes.py`'s module docstring:

- **Escalation (up): the dish-binary day MILP** (P1's formulation, design work done): `y[j,s]` anchor binaries, `Σ_j y[j,s] = 1` with hole-slack, per-instance `t ≤ T_MAX·y`, linear band coupling, `used_days²` as linear costs on `y`, seeded epsilon tie-breaks — dish selection moves from skeleton into the solve, behind the same `plate_dishes` seam. **Criterion (data-forced):** M1.6-class real week shows skeleton `ASSIGN_RETRIES` exhaustion or `no_dish_assignable` on > 5% of person-days, or `dish_band_binding`/`band_unmet` above `BAND_ESCALATION_THRESHOLD` — decision forced onto the owner's desk with flag counts as receipts.
- **Downgrade (down): the dish-coupled continuous-`s` pool LP** (P2's hatch — one `s_d` per dish, linear member coupling, no new binaries) if `plate-dish` blows a named BASELINES.md multiple or CBC shows non-optimal terminations on real weeks.

**Measuring stick:** the M1.6 real week is the gate — the founder week must be authorable as steward dishes and solve with flag counts at-or-below the dealer baseline, and the owner must recognize dinner.

## 14. Test plan

**Fast tier (unit, zero CBC, milliseconds):**
1. Schema validation: band ordering, `dish_band_exceeds_serve` (error+warn split), unresolved ids, accents-disjoint, unit-grid at t=1, `dish_band_loose`, affinity label set; dead-config registry for every new field.
2. Availability conjunction: dish dies with shortest-lived core member; freezer bridging per member; `dish_unavailable` names the killer.
3. Eligibility: core-tag kill vs accent/optional-member drop; `dish_excluded_by`.
4. Closure derivation: components ∪ accents ∪ compatible_sides; must-list mapping warning.
5. Skeleton: determinism (two calls byte-identical); variety `used_days²` + same-day repeat + `variety_unmet` regimes; affinity assignment; side-candidate ranking; capacity-fit ordering; retry-ladder ordering (mocked infeasibility); slotless `DISHES_PER_DAY` blocks.
6. Band algebra: `T_MAX` derivation; per-meal serve-cap intersection; hard-set-empty detection arithmetic.
7. `--implicit-dishes` synthesis shape; `permissive|strict` orphan-side behavior; `component_unreachable`/`orphan_component`.
8. Menu-score translations: lean-dish achievable-ratio test (composite fat-heavy dish is NOT lean); dish carb headroom; slot coverage; capacity penalty; role/person floors on dishes.
9. Doctor dish section arithmetic incl. `slot_target_unreachable`.
10. **Inertness golden:** no dishes.yaml ⇒ pipeline byte-identical (guards everything).

**Slow tier (pipeline, CBC — dish fixtures only where the test targets the layer):**
1. One end-to-end dish fixture: build → skeleton → solve → lock → verify-plan round trip; dish golden on the pinned reference env; **structural asserts:** every meal = one dish within bands ∩ caps; `Σ meals == day plate`; demand equals component sum.
2. Band-conflict fixture: dish bands force a flagged day miss → `dish_band_binding` with correct bound + delta from active constraints; `band_unmet` + `responsible_dish`.
3. Big-eater fixture: `t` scale-up to `T_MAX`, side absorption, `meal_underfilled` when capped; opt-in `max_dishes_per_slot=2` → `second_dish_added`, both instances in-band.
4. Replate: slot-qualified pin honored; dish veto → re-skeleton; `no_alternative_dish`.
5. `make baseline` records `plate-dish` + `dish-skeleton` stages; retry/flag-rate instrumentation report (pre-M1.6 evidence).

## 15. Open questions (P9, ratify at M1.6)

1. `MEAL_BAND` 0.30 and `DISH_T_MIN` 0.5 — against the real week.
2. Auto-second-dish on chronic `meal_underfilled` — **owner decision**, evidence = capacity arithmetic + flag counts.
3. `dish_layer` strict-ratchet timing — after steward `compatible_sides` coverage.
4. Whether `meal_affinity` gains reserved semantic labels beyond slot-name matching — owner call, rendering-adjacent.
5. Dish `cuisine` as authored field vs main-derived — steward/owner.

**Key file anchors for the implementer:** `engine.py` — `plate()` :205 (LP formulation + two-pass snap to mirror), `SCORE_WEIGHTS` :92, `effective_serve_bounds` :142, `eligible` :175, `max_daily_mass_g` :264, day-slack :270–277, doctor meal section :690–729, `score_menu` :735, `choose_menu` :848, `diverse_plates` :913 (heritage-only survivor), `available_on` :926, `replate` :966, `build_week` :1001 (branch point; demand accumulation :1088–1091). `model.py` — `Person` :164, `resolve_meal_slots` :219, new `Dish` dataclass. `meals.py` — `deal_day` :119 (heritage path, untouched), `MEAL_WEIGHTS` :41, `dealability_penalty` :505 (heritage-only), `equalize_interchangeable` :529. New module: `services/solver/mealplan/dishes.py`. Validation: `io_yaml.py`; instrumentation: `instrument.py`; consumers: `lockplan.py`, `artifacts.py`. Data: `data/dishes-draft/dishes.yaml` (steward draft, concurrent — consume §2 schema as-is).