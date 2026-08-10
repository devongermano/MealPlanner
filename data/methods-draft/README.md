# methods-draft — composable method-step fragments (Track E)

Draft cooking-method content for every component in `examples/components.yaml`.
Per PRD §6, **recipes are ground truth, never rendered**: these steps are
derivation inputs for the compiled cook script (PRD §4.0), not prose a user reads
directly. M1.10 (sheet rework) and M1.12 (timeline compiler v0) are the consumers.

## Schema (one file per component: `<component_id>.yaml`)

```yaml
schema_version: 0        # draft; M1.10 owns the ratified schema
component: <component_id> # must match examples/components.yaml
steps:
  - phase: prep | cook | finish
    text: terse imperative, ONE action, scalable phrasing
    station: prep | stove | oven | grill | none
    mode: active | passive
    duration_min: <positive int>   # PROVISIONAL, one batch
    oven_temp_f: <int>             # REQUIRED iff station: oven
    operation: <technique id>      # optional; must exist in data/techniques/techniques.yaml
```

Field semantics:

- **phase** — coarse ordering contract. Within a file, phases appear in
  `prep → cook → finish` order (a phase may be absent). The compiler may
  interleave steps *across components* freely; within a component, list order
  within a phase is the dependency order.
- **text** — one action per step, imperative, **no quantities**. Amounts are
  injected by the compiler from scaled ingredient grams ("dice 380 g onion").
  Write "the diced onion", "the soaked chiles" — definite references to the
  output of earlier steps, so shared-prep consolidation can merge identical
  operations across dishes (PRD §4.0 amendment).
- **station** — physical resource the scheduler allocates: `prep` (board/bowls/
  blender), `stove`, `oven`, `grill`, `none` (fridge/counter/no contention).
- **mode** — `active` consumes cook attention; `passive` runs on a timer with
  hands free (the greedy scheduler front-loads passive steps and injects
  portioning where hands are free).
- **duration_min** — honest single-batch estimate. **PROVISIONAL until cook-day
  calibration** (PRD §4.0: durations are calibrated from real cook days).
  Realistic, not optimistic: knife work at home-cook speed, batched searing
  counted per batch.
- **oven_temp_f** — only on `station: oven` steps. This keys temp-bucketed oven
  sharing: same-temp steps co-reside in the greedy tier (300 braises share;
  425 roasts share; 500 is the broiler bucket).
- **operation** — id into `data/techniques/techniques.yaml`. The technique
  library maps operations → explanation (and later owner-recorded video),
  reused across every step naming the operation (PRD §10 cook mode).

Store-bought components (`source: store`) carry a single trivial
`phase: prep / station: none` portioning step so the portioning matrix still
has a hook to hang container assignments on.

## Integration contract (for M1.10 / M1.12)

1. **These files are content, not schema.** M1.10 defines the ratified method
   schema on components (likely inline under the component or a `method:` ref).
   When it lands, this directory is migrated into it and deleted; ids and step
   semantics above are the migration input.
2. **Compilation:** for a session, the compiler takes each cooked component's
   steps, scales quantity mentions from batch grams, merges identical prep
   `operation`s across components (shared-prep consolidation), then either
   renders per-dish blocks (`cook_plan_style: recipe`) or greedily schedules
   over stations + cook attention (`timeline`), passive-first, assemblies
   topologically first, oven steps bucketed by `oven_temp_f`. Portioning-matrix
   rows attach after each component's `finish` steps (its `portion` step is the
   anchor).
3. **Durations are provisional** until M1.6-style real cook days produce
   calibration data; nothing may assert on them in tests.
4. **Techniques:** `operation` ids resolve against
   `data/techniques/techniques.yaml`; the lint (`lint.py`) enforces no orphans
   in either direction. `video: null` is a placeholder for owner-recorded
   clips (owned content — PRD §10).

## Lint

`python3 data/methods-draft/lint.py` from the repo root. Checks: file set ==
component ids in `examples/components.yaml` (read-only); enum validity;
`prep → cook → finish` ordering; positive durations; `oven_temp_f` present iff
`station: oven`; every `operation` resolves; no unused techniques. Prints
active/passive duration totals per component.
