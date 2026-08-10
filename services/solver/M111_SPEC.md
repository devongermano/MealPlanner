# M1.11 SPEC — Target profiles: day-type cycling anchored to the plan date

Implementation contract. Owner provenance (PRD §5.2 promotion note, verbatim):
*"some people scale macros, more on lifting days, and less on weekends. So
macros are actually in that case, a weekly target, not a daily one."*
PRD ships it as: **profile-based cycling — named day-types + week map anchored
to the plan date; flat daily grams stay valid as shorthand** (TASKS M1.11).

Design bar inherited from M1.9/M1.13: the layer is **inert** for anyone who
does not author it (byte-identical pipeline, both goldens unregenerated), the
resolution lives in **one** function (P10), validation is all-errors, the
engine never reads a wall clock, and every provisional number/behavior is
named.

---

## 1. Scope

**v0 IS:** per-person named macro profiles + a weekday map; each plan day
resolves to exactly one flat `{protein, fat, carb}` gram target before it is
solved; everything downstream of resolution (plate LP, dish LP, meal dealer,
serve-bound scaling, tolerance bands) is unchanged code operating on the
resolved day targets.

**v0 IS NOT:** cross-day coupling. The LP still solves each person-day
independently; the *weekly* total is **emergent** — the sum of the 7 resolved
days — and is **reported** on plan.md and the eat sheets (see §4, §9). Letting
the solver shift grams between days against a weekly budget is the documented
escalation, already named in PRD §5.2 ("free weekly allocation", declared
later, opt-in successor). This spec states that explicitly so nobody "helpfully"
couples days in M1.11.

---

## 2. Schema (people.yaml, per person)

```yaml
people:
  devon:
    targets: {protein: 180, fat: 70, carb: 220}     # REQUIRED, unchanged — the BASE day
    target_profiles:                                 # NEW, optional
      lift:    {protein: 200, fat: 70, carb: 300}
      weekend: {protein: 150, fat: 75, carb: 150}
    week:                                            # NEW, required iff target_profiles
      mon: lift
      wed: lift
      fri: lift
      sat: weekend
      sun: weekend
    # tue/thu absent -> those days use `targets` (the base day)
    tolerance: 0.05
```

Rules:

- `target_profiles`: non-empty mapping `name -> {protein, fat, carb}` (grams,
  same shape and same validation as `targets`). Profile names are free-form
  non-empty strings; **no reserved names** (nothing plays the "default" role —
  absence does, see next rule).
- `week`: mapping from weekday key to profile name. Keys are the fixed enum
  `mon tue wed thu fri sat sun` (`model.DAY_KEYS`, ISO order, Monday first —
  matching `datetime.date.weekday()` = 0). **Partial maps are legal**: a
  weekday not listed resolves to `targets`. Values must name a defined
  profile.
- `target_profiles` and `week` come **together or not at all** (either one
  alone is a validation error — §6). A person with neither keeps today's
  shape **verbatim** (flat daily grams as shorthand, per PRD).
- `targets` stays REQUIRED for everyone (`PERSON_REQUIRED` unchanged). It is
  the base day-type and the anchor-free fallback every legacy read still
  needs (doctor summaries, menu scoring — §5).

### Why this shape (alternatives rejected)

- **7-entry `week` list positional on plan day 0** — rejected: "lifting days"
  and "weekends" are *calendar* weekdays in the owner's sentence; a positional
  list silently rotates meaning when the plan starts on a different weekday.
  Weekday-name keys are start-day invariant.
- **Mandatory 7-entry week map** — rejected: it forces authors to invent a
  `base`/`rest` profile duplicating `targets`, or forces `targets` optional
  (which breaks every legacy read and `PERSON_REQUIRED`). Partial map +
  base fallback is the incremental shape: author *only the exceptional days*.
  The fallback is explicit absence semantics, documented and validated — not
  breakfast-style guessing (nothing is inferred from names; `sat`/`sun` get
  weekend macros only if the author says so).
- **Inline per-day macro maps in `week`** (no named profiles) — rejected:
  repeats macro triples across days, loses the day-type *name* the doctor and
  sheets report ("day-type 'lift' infeasible" beats "wed infeasible").
- **`targets: weekly` grand-total grams** — rejected for v0: that is the
  free-weekly-allocation escalation (cross-day LP), explicitly deferred.

---

## 3. Anchoring (plan date → weekday)

- **The anchor is data**: `anchor = plan_date.weekday()` (int 0–6, Monday=0),
  derived exactly once at the CLI boundary in `cli._run` (and in
  `cli._solve_from_snapshot` from the snapshot's `plan_date`). The engine
  never touches a clock; the determinism static scan is unaffected.
- Day resolution: plan day `d` (0-indexed) has weekday
  `DAY_KEYS[(anchor + d) % 7]`.
- **`lock`**: already requires `--date` (cli.py:793) — nothing changes; the
  snapshot already carries `plan_date` (lockplan.build_snapshot), people.yaml
  is embedded verbatim, so profiles + anchor round-trip through
  `verify-plan` byte-stably with **zero new snapshot fields**. For
  no-profile libraries the snapshot is byte-identical to pre-M1.11 (same
  argument M1.9 §meals and M1.13 §dishes used).
- **`week` / `frontier` without `--date`**: allowed exactly as today **unless
  any person authors `target_profiles`** — then it is a structured
  `CliError("date_required", …, EXIT_USAGE)`, mirroring the M1.8 pantry
  precedent verbatim (cli.py:352–358: dated pantry stock already requires
  `--date`). Rationale: a silent "assume Monday" default would make the same
  YAML mean different real-world weeks depending on when it is *actually*
  shopped — the plan would be deterministic but wrong, which is worse.
  Existing users (no profiles) see no new requirement: **a missing date
  cannot crash anyone it doesn't concern.**
- **`doctor`**: needs **no date**. Feasibility is checked per *distinct
  day-type* (§7), which is a set of macro maps — anchor-free by construction.

---

## 4. Semantics

- Each person-day is solved against `resolve_targets(person, d, anchor)` —
  a flat gram map. Tolerance, serve-bound scaling (`person_scale`), mass cap,
  meal-slot shares all apply to the *resolved* day exactly as they apply to
  `targets` today, because they receive a person whose `targets` IS the
  resolved map (§5).
- The **weekly target is emergent**: `weekly = sum over d in 0..days-1 of
  resolve_targets(person, d, anchor)`. It is REPORTED (plan.md person header
  + eat-sheet header, §9) with the label "(emergent — days are solved
  independently)". **No LP constraint spans days.** Escalation path: PRD §5.2
  free weekly allocation.
- Serve-bound scaling note: `person_scale` becomes day-correct for free (a
  200p lift day scales bounds harder than a 150p weekend day). This is the
  intended reading of M1.7's "the person's kcal" once the person's kcal is a
  day-type property. Named consequence, covered by a test (§11 T8).

---

## 5. Resolution — P10, one function

All in **model.py**, beside `resolve_meal_slots` (the established
one-resolution-point pattern):

```python
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # ISO, Monday=0

def week_day_label(person, day_index, anchor) -> Optional[str]:
    """The day-type NAME plan day `day_index` resolves to, or None for the
    base day. THE one weekday-math site (anchor + day_index) % 7."""

def resolve_targets(person, day_index=None, anchor=None) -> dict:
    """THE canonical per-day target resolution (P10). No profiles -> returns
    person["targets"] — the SAME object (identity), which is the inertness
    proof. Profiles present and (day_index is None or anchor is None) ->
    ValueError (an engine-side bug guard; the CLI's date_required fires
    first for real users)."""

def person_for_day(person, day_index=None, anchor=None):
    """Day-view person: identical person when resolution returns the base
    map (object identity — zero churn, byte-identical behavior); otherwise
    a copy whose raw["targets"] is the resolved map (Person.from_raw for
    Person inputs, dict copy for plain mappings). Never mutates the input."""

def weekly_targets(person, days, anchor=None) -> dict:
    """The emergent weekly sum — sum of resolve_targets over the plan days.
    Reporting only (plan.md / eat sheets / calorie-share table)."""
```

**Injection strategy (the load-bearing decision):** `build_week`,
`build_week_dishes`, `deal_week`, `replate*`, the doctor and the renderers
call `person_for_day` and hand the *day-view person* down. Every read of
`person["targets"]` **inside** `plate`, `plate_dishes`, `skeleton_day`,
`deal_day`, `person_scale`, `effective_serve_bounds` etc. is then already
correct **with zero diff** — the day-view person's `targets` is the resolved
map. Threading `day_index`/`anchor` through every solver signature was
rejected: ~30 call sites, huge diff, and every future `targets` read would
have to remember to resolve (a standing P10 leak). With injection, the only
code that may call `resolve_targets`/`person_for_day` is the day-loop layer —
grep-enforceable.

### 5.1 Call-site inventory — every current `person["targets"]` read

Line numbers from the current tree (m0-test-harness @ M1.13/M1.12 landed).

| # | Site | Disposition |
|---|------|-------------|
| 1 | engine.py:136 `person_scale` | unchanged code; receives day-view person → day-correct serve bounds |
| 2 | engine.py:238, 271, 337, 358 `plate` (empty-usable miss, LP bands, solver-failure misses) | unchanged; day-view person |
| 3 | engine.py:388 `binding_macro` | unchanged; doctor calls it per distinct day-type view (§7) |
| 4 | engine.py:407 `volume_floor` | unchanged; per distinct day-type view |
| 5 | engine.py:482 `carb_headroom` | unchanged; per distinct day-type view |
| 6 | engine.py:555, 587 `doctor` per-person report | iterates distinct day-types; lines labeled with day-type name + weekdays (§7) |
| 7 | engine.py:825–846 `score_menu` (carb-headroom penalty, need_ratio) | **KEEPS base `targets`** — P9-provisional (§12 P-1): menu search is week-level and pre-anchor; per-day-type feasibility is the doctor's job (§7). Byte-inert by construction |
| 8 | engine.py:1022 `build_week` | **injection point**: per day `p_day = person_for_day(p, d, anchor)` feeds `diverse_plates`/`plate`/`dealability` scoring; new kwarg `anchor=None` |
| 9 | engine.py:987 `replate` | new kwarg `anchor=None`; resolves its `day` to a day-view person |
| 10 | dishes.py:405 slot share, 470 day_kcal (`skeleton_day`) | unchanged; day-view person from build_week_dishes |
| 11 | dishes.py:587→751/768/962/978/1061/1073 `plate_dishes` (`targets = person["targets"]`) | unchanged; day-view person |
| 12 | dishes.py:1129 `build_week_dishes` | **injection point**, new kwarg `anchor=None` (mirror of #8) |
| 13 | dishes.py:1210 `_slotless_blocks`, 1268 `replate_dishes` | day-view person; `replate_dishes` gains `anchor=None` |
| 14 | dishes.py:1402/1424/1462/1536 `score_menu_dishes` + choose fallback | **KEEPS base `targets`** (same P-1 ruling as #7) |
| 15 | dishes.py:1603–1661 `doctor_dish_section` | per distinct day-type, labeled (§7) |
| 16 | meals.py:140 `deal_day` slot targets (and 466 `_finish_meal` via param) | unchanged; `deal_week` (meals.py:631) gains `anchor=None` and passes `person_for_day(person, d, anchor)` at its existing `enumerate(weeks[pname])` loop (meals.py:644) |
| 17 | artifacts.py:778–786 `render_eat_sheet` header + per-day `_macro_status`/targets | gains `anchor=None`; no profiles → header **verbatim** today's bytes; profiles → §9 rendering; per-day status uses resolved targets |
| 18 | cli.py:196–198 plan.md person header | no profiles → verbatim; profiles → base + profile lines + emergent weekly line (§9) |
| 19 | cli.py:245–247 calorie-share table | uses `weekly_targets(person, days, anchor)`; no profiles → weekly = 7×daily so all ratios (the only thing printed) are bit-identical — golden-gated |
| 20 | costing.py:403 `estimate_batches` kcal-need heuristic | **KEEPS `targets` × days** — P9-provisional (§12 P-2): explicitly "crude, cheap, monotone"; real batch counts come from build_week demand, which is already day-type-correct via #8. Zero churn, inert |

Threading summary — new `anchor=None` keyword on: `engine.build_week`,
`engine.replate`, `dishes.build_week_dishes`, `dishes.replate_dishes`,
`meals.deal_week`, `artifacts.render_eat_sheet`, `artifacts.render_artifacts`,
`cli.render`, plus derivation in `cli._run` / `cli._solve_from_snapshot`.
Default `None` everywhere: an anchor is only *required* (ValueError guard /
CLI date_required) when someone actually authored profiles.

---

## 6. Validation (io_yaml.validate_people_doc — all-errors, never first-error-wins)

Shared helper first: extract today's inline targets-macro check
(io_yaml.py:399–405) into `_macro_map_issues(where, m)` and call it for
`targets` (same codes/`where` strings — message-byte parity, existing
validation tests must pass untouched) and for each profile. New rules, each
appended to the one issues list:

| code | trigger |
|------|---------|
| `profiles_without_week` | `target_profiles` present, `week` absent — dead config (M0.5 posture: unconsumed authored data is an error) |
| `week_without_profiles` | `week` present, `target_profiles` absent — labels are undefined |
| `bad_target_profiles` | `target_profiles` not a non-empty mapping; or a profile name not a non-empty string; or a profile body not a mapping |
| `missing_field` (reuse) | profile missing `protein`/`fat`/`carb` — `where` = `person 'x', target_profiles.lift.protein` |
| `unknown_field` | profile body has keys beyond the three macros (mirrors the meal-slot "unexpected field(s)" pattern, io_yaml.py:493–502) |
| `bad_week` | `week` not a non-empty mapping |
| `bad_week_day` | week key not in `mon..sun` (message lists the enum — catches `monday`, `Mon`, ints) |
| `unknown_profile` | week value is not a defined profile name (or not a string) |
| `dead_profile` | profile defined but referenced by no week entry (M0.5 dead-config: authored, never consumed) |

All rules run in one pass; a document tripping five of them reports five
issues (asserted, §11 T-V8). Dead-config registry (tests/test_dead_config.py):
`target_profiles` and `week` register LIVE (consumed by
`model.resolve_targets`).

---

## 7. Doctor — feasibility per DISTINCT day-type, anchor-free

For each person, `distinct_day_types(person)` (model.py, beside the
resolvers) returns an ordered list of `(label, targets)`:

- `("base", person["targets"])` — included iff any weekday resolves to base
  (no profiles at all, or a partial week map);
- one entry per profile, in `target_profiles` authoring order, each carrying
  the weekday keys that map to it.

`engine.doctor` and `dishes.doctor_dish_section` loop these instead of the
single person: `binding_macro`, `volume_floor`, `carb_headroom` (and the dish
reachability checks at dishes.py:1603–1661) run once per **distinct** type —
cost is O(distinct types) ≈ 2–3 extra LP-bearing checks per person, not O(7)
— and no anchor is needed because the check is per type, not per calendar
day. Report lines carry **named gaps**:

```
- **devon — day-type 'lift' (mon, wed, fri)**: protein binds first; volume floor 2140g …
- **devon — day-type 'base' (tue, thu)**: no macro binds — targets are met …
```

A person with no profiles produces exactly one type ("base") and the loop
degenerates to today's single pass — the report is **byte-identical** (the
"day-type" phrase only renders when `len(types) > 1`).

---

## 8. Inertness + goldens

The inertness chain, each link independently tested:

1. `resolve_targets` with no profiles returns `person["targets"]` **by
   identity** (T1);
2. `person_for_day` then returns **the same person object** (T1) — so
   build_week/deal_week/plate see literally the objects they see today;
3. `score_menu`, `estimate_batches` are untouched code;
4. renderers branch on profile presence — no-profile headers/lines are
   today's bytes verbatim (T-A1);
5. the lock snapshot gains **no fields**; people.yaml is embedded verbatim, so
   no-profile snapshots and their sha256 are byte-identical (same M1.9/M1.13
   argument).

Gate: **both goldens — tests/golden/solo_lifter_pipeline.json AND
solo_dishes_pipeline.json — pass UNREGENERATED.** Any diff is an M1.11 bug by
definition, never a "regenerate" chore.

New fixture: `tests/fixtures/cycling_lifter/` — synthetic clone of
solo_lifter whose person adds `target_profiles: {lift, weekend}` and
`week: {mon: lift, wed: lift, fri: lift, sat: weekend, sun: weekend}`
(tue/thu deliberately absent → exercises base fallback). Lift protein
strictly above base, weekend strictly below — so day-type effects are
sign-testable, not tolerance-noise.

New e2e module `tests/test_targets_e2e.py` registered in
`tests/conftest.py::SLOW_MODULES` with the usual one-line justification
comment (resolution itself is pure — its unit tests are FAST-tier in
`tests/test_target_profiles.py` by construction).

---

## 9. Artifacts & reporting

Profiles present (all lines ONLY then — otherwise verbatim today):

- **Eat sheet header** (artifacts.py:785): base line as today, plus one line
  per profile (`- lift (mon, wed, fri): 200g protein / …`), plus
  `Weekly total (emergent): …g protein / …g fat / …g carb (… kcal) — days
  are solved independently.`
- **Eat sheet day headings**: `## Day 3 (wed — lift) — 2870 kcal`; base days
  show the weekday only (`(tue)`), keeping "base" out of the reader's face.
  Weekday is derived from the anchor — data, not clock.
- **plan.md person header** (cli.py:197): same treatment + the weekly
  emergent line; per-day lines gain the same `(wed — lift)` tag.
- **Calorie-share table** (cli.py:245): computed from `weekly_targets` — the
  honest denominator once days differ.
- Per-day `_macro_status` and error bars evaluate against the resolved day's
  targets (automatic — they already receive the day totals plus the sheet's
  person, which is rendered per-day).

Locked plan: no schema change (`PLAN_SCHEMA_VERSION` stays 1). Portions
already lock per-day grams; profiles live in the verbatim people.yaml
snapshot; `verify-plan` re-solves through the same anchor derivation (§3).

---

## 10. Weights / thresholds

M1.11 introduces **no new numeric weights**. The named provisional *behaviors*
are registered in §12; there is nothing to tune, only rulings to ratify.

---

## 11. Test plan

FAST — `tests/test_target_profiles.py` (pure model/validation, no LP):

| id | test | proves |
|----|------|--------|
| T1 | `test_resolve_no_profiles_is_identity` | base map returned by identity; `person_for_day` returns the same object (inertness link 1–2) |
| T2 | `test_resolve_anchor_math_all_starts` | for all 7 anchors × 7 days, resolution matches hand-computed `(anchor+d)%7` weekday label (kills off-by-one/mod mutants — mutation-tested non-vacuity) |
| T3 | `test_partial_week_falls_back_to_base` | unlisted weekday → `targets`, listed → profile |
| T4 | `test_profiles_without_anchor_raises` | profiles + `anchor=None` → ValueError (engine bug guard) |
| T5 | `test_person_for_day_swaps_targets_only` | day-view raw differs only in `targets`; input person unmutated; tolerance/slots/exclude preserved |
| T6 | `test_weekly_targets_sum` | emergent sum = Σ resolved days; no-profile person → 7 × daily |
| T7 | `test_week_day_label_none_for_base` | label None on base days (drives §9 rendering branch) |
| T8 | `test_person_scale_day_correct` | `person_scale(person_for_day(...))` differs lift vs weekend day (named §4 consequence; kills silent-base-scale mutants) |
| T-V1..V7 | one test per §6 rule (`profiles_without_week`, `week_without_profiles`, `bad_target_profiles`, profile `missing_field`, profile `unknown_field`, `bad_week_day`, `unknown_profile`, `dead_profile`) | each code fires with the right `where` |
| T-V8 | `test_validation_all_errors_accumulate` | one document tripping 5 rules yields 5 issues in one pass (never first-error-wins) |
| T-V9 | `test_targets_validation_bytes_unchanged` | a legacy bad-targets doc produces the exact pre-M1.11 issue list (the `_macro_map_issues` extraction is refactor-pure) |
| T-C1 | `test_week_requires_date_with_profiles` | in-process `cli.main` on cycling_lifter without `--date` → exit 2 + `date_required` (raised pre-solve → fast); solo_lifter without `--date` still runs |
| T-D1 | dead-config registry rows for `target_profiles`/`week` (extends `test_dead_config.py`) | fields are LIVE |

(Determinism: the existing static scan in `test_determinism.py` covers the new
code paths automatically — no clock/hash imports to add, nothing to write.)

SLOW — `tests/test_targets_e2e.py` (register in SLOW_MODULES):

| id | test | proves |
|----|------|--------|
| T-E1 | `test_cycling_changes_exactly_intended_days` | full week on cycling_lifter, `--date` a Monday: each day's plate macro totals sit inside the *resolved* day's ±tol band; lift-day protein > weekend-day protein. (Deliberately NOT "base days byte-equal the no-profile run" — build_week couples days through `used_days`/`used_g` variety state, so per-day equality is not promised.) |
| T-E2 | `test_anchor_rotation_shifts_pattern` | same fixture, `--date` a Tuesday: the lift/weekend day-index pattern shifts by exactly one — solve-count-free assertion on resolved targets + band membership |
| T-E3 | `test_equal_profiles_are_plate_inert` | cycling variant whose profiles NUMERICALLY equal base: solved `weeks` equal the no-profile run's (proves the plumbing itself injects nothing — non-vacuous inertness beyond identity short-circuit) |
| T-E4 | `test_lock_verify_roundtrip_profiles` | `lock --date` then `verify-plan`: hash stable, re-solve matches (anchor round-trips through the snapshot) |
| T-E5 | `test_profiles_add_zero_week_solves` | `engine.solve_counts()` equal for T-E3's pair — resolution costs no LP (solve-count budget, not wall clock) |
| T-E6 | `test_dishes_pipeline_cycling` | dish-mode week on a cycling variant of the dishes fixture: per-day totals track resolved targets (covers injection point #12) |
| T-D2 | `test_doctor_day_type_sections` (in `test_doctor_diagnostics.py`, already SLOW) | doctor on cycling_lifter, NO date: one labeled section per distinct day-type with weekday lists; solve count grows by exactly (distinct_types−1) × per-person doctor checks; no-profile person's report byte-identical |
| gate | existing golden/capability tests | both goldens pass UNREGENERATED |

---

## 12. Named provisional rulings (P9 registry)

- **P-1 `score_menu`/`score_menu_dishes` use base `targets`** (call sites #7,
  #14). Menu search is week-level and runs identically for every anchor; the
  doctor's per-day-type feasibility (§7) is the safety net for "menu can't
  reach lift-day protein". Escalation if the M1.6 real week shows menus that
  score well but doctor-fail a day-type: score against the max-need distinct
  type.
- **P-2 `estimate_batches` uses base `targets` × days** (call site #20).
  Explicitly a crude monotone ceiling heuristic; true demand comes from
  build_week, which is day-type-correct. Escalation: `weekly_targets` kcal.
- **P-3 partial-week base fallback** (§2). If real households author full
  7-day maps anyway and the fallback confuses, the ratchet is a lint warning
  nudging explicit coverage — never a semantic change.

## 13. Out of scope / documented escalations

- Cross-day coupling / free weekly allocation (PRD §5.2 — declared later,
  opt-in successor). The weekly number on the sheets is a REPORT.
- Per-day tolerance / mass-cap profiles (targets only in v0).
- Calendar exceptions ("this specific Friday is a rest day") — belongs to the
  M2 propose→veto loop, not the library.
- `settings.days != 7`: resolution is `(anchor + d) % 7` for any day count;
  the emergent weekly report sums the actual plan days. No 7-day assumption
  anywhere in the resolver.
