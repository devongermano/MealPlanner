# M0 Phase 5 fix notes (2026-08-09)

Disposition of the verified Phase 5 gate findings, with the golden-file
justification the fix process requires.

## Golden files: NOT regenerated — and why that is correct

`tests/golden/solo_lifter_pipeline.json` still byte-matches the recorded
golden after every fix (verified: full suite green on the reference
environment, including `test_determinism_golden_full_pipeline_byte_stable`
and the exact solve-count baseline). No fix changes engine behavior for any
input that was previously valid AND canonical:

- **cook_days canonicalization** (blocker): `Settings.from_raw` and
  `sessions_for` now sort/dedupe cook_days like shop_days. Behavior changes
  ONLY for unsorted/duplicated cook_days lists — inputs whose previous
  "earliest session" semantics were YAML-list-order-dependent, i.e. the bug
  itself. Every fixture, the examples corpus, and the golden all declare
  sorted cook_days, so their outputs are bit-identical.
- **Validation additions** (type gaps, `bad_unit_g`, budget mode/period):
  strictly reject-more; previously-valid well-typed documents load
  identically.
- **SCORE_WEIGHTS registry** (PRD §8.3): pure refactor — identical values,
  now named/overridable; scores and LP objectives are unchanged.
- **P8 relax-tier surfacing**: rendering-only; engine untouched; the flag
  text appears only when the relaxation ladder actually engaged (never on
  the golden pipeline, whose diag records tier 0 everywhere).
- Everything else was docs, tests, or explicit deferrals.

## Fixes, by finding

1. **Validation type gap (blocker, io_yaml)** — non-numeric ingredient
   `p/f/c/cost/keeps_days/pack_g`, component `yield_g/active_min/keeps_days`,
   and `serve_g.min/max` now raise structured `bad_number` errors in the one
   all-errors pass (no more raw `TypeError` from `derive_component`).
   Tests: `test_phase5_fixes.py` (incl. the exact `p: "high"` repro).
2. **cook_days canonicalization (blocker, costing/model)** — see above.
   Test: `test_cook_days_order_never_changes_session_attribution` ([0,4] vs
   [4,0] vs [4,0,4] produce identical attribution/batches/minutes).
3. **Per-person serve_g scaling (blocker, engine)** — NOT implemented;
   converted from silent omission to an explicit owner-visible deferral:
   PRD Appendix B item 2 + TASKS.md M1.7.
4. **Dead-config gate hole (test_dead_config)** — the registry gate now
   scans Budget and Pantry too; `Budget.period` and `Pantry.cooked` are
   documented entries in `model.RESERVED_FIELDS` (period additionally
   validated to its one known value, `week`); budget `mode` is now an
   enum-validated field (`by_consumption` documented as the
   attribution-only, no-ceiling mode in `costing.budget_ceiling`).
5. **unit_g never validated (io_yaml)** — `bad_unit_g` error for
   non-numeric/nonpositive unit_g; the M0.8 grid guarantee can no longer be
   silently skipped.
6. **§8.3 magic score constants (engine)** — all ~16 constants moved into
   the named, provisional, overridable `engine.SCORE_WEIGHTS` registry
   (menu score + plate objective); `score_menu(score_weights=...)` merges
   overrides. Values unchanged. Tests pin the registry surface and that an
   override changes exactly its term.
7. **P8 silent relaxation (cli)** — `_run` now passes `diag` to
   `build_week`; `render` flags each relaxed day inline and summarizes all
   relaxed person-days up top. End-to-end CLI test included.
8. **PRD §5.2 errata** — recorded in PRD Appendix B item 1: menu selection
   is deliberately cost-aware as built; `frontier` retained as a dev tool.
9. **SKILL.md tolerance advice (mealplan/SKILL.md)** — playbook reordered to
   the §8.3 decided ordering (structural fixes first, tolerance the labeled
   last resort), making the M0.17 checkbox true.
10. **Pantry half-built (costing)** — `acquired`-age rule explicitly
    deferred: PRD Appendix B item 3, TASKS.md M1.8, and a DEFERRED note in
    `purchase()`'s docstring; `cooked` reserved (see 4).
11. **Prototype import blocks retirement (test_extraction_smoke)** — the
    parity fixture now SKIPS when `mealplan/plan.py` is gone, removes the
    prototype from `sys.path` immediately after import, and drops the module
    from `sys.modules` at teardown (no more session-long shadowing).
12. **Corpus-pinned tests** — behavioral pins on the live examples corpus
    replaced by properties/lints or synthetic fixtures: volume-floor bands
    (doctor tests), freezable-meat list, spices/chicken_wings values,
    mango_jalapeno_wings 495, turkey_meatballs pin tests (now a synthetic
    `balls` library), picadillo/birria --force pin (now self-adapting), the
    min_lean_anchors==2 corpus pin, and the examples solve budget (now a
    deliberate 2x explosion guard).
13. **Golden policy half-implemented** — `tests/refenv.py` implements the
    pinned-reference-environment gate; byte compare and exact solve counts
    skip/band-relax off-reference; a new all-platform properties test
    carries the guarantee everywhere (tests/golden/README.md updated).
14. **Untested engine behaviors** — new behavioral tests for: dislikes soft
    down-weighting, a binding `max_batches_per_component`, budget mode
    `per_person` (`budget_ceiling`), `costing.attribute`, and the
    `active_min_budget` over-budget score term (`test_phase5_fixes.py`).

## Deferred minors (recorded, not fixed — sweep at M1 start, TASKS M1.0)

> **SWEPT 2026-08-09 (M1 Phase 1):** every item below except the two
> explicitly M2-scoped ones (nearest-prior-trip per-trip assignment; price
> provenance / plan-group schema fields) was fixed in TASKS M1.0 — see
> `services/solver/M1P1_NOTES.md` for the disposition and the golden/baseline
> deltas of the phase.

- plate() structural-failure fallback reports misses as positive full targets
  (reads as "forced OVER" when the person is SHORT of everything) — sign
  semantics of the no-usable-components branch.
- A pin on a component the person cannot eat is dropped with a bare continue,
  no structured warning (inconsistent with pin_adjusted/locked_unavailable).
- tolerance accepts any value (e.g. -0.5) — needs range/type validation.
- CLI runs the full doctor synchronously before every command (~2.7s) vs the
  §8.3 never-blocking posture (matters at the M2 service surface).
- P1 residue: founder-derived numbers (588g carb rationale) quoted in engine
  comments as design justification.
- Raw-freshness uses nearest-prior-trip for all ingredients ("trip that buys
  it" simplification) — per-trip shopping assignment arrives with multi-trip UX.
- §8.1 fields not yet in the model, no deferral recorded elsewhere: ingredient
  price provenance (estimate|receipt) and person plan-group membership — both
  land with M2 schema work.
- Dead-config gate matches field names in comments/docstrings (weak reference
  check); runtime concentration in choose_menu-heavy tests (~prune candidates);
  test_perf_budget imports from test_capabilities (importlib-mode coupling).
