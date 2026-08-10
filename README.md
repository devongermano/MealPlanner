# MealPlanner monorepo

Constraint-based batch-cooking planner. The authoritative documents live at the
repo root:

- **[PRD.md](PRD.md)** — product requirements v2.1 (§8 engine spec, §9 test strategy, §11 gates)
- **[TASKS.md](TASKS.md)** — execution tracker; M0 (engine correctness) is current
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — decided stack and monorepo layout

Layout (per ARCHITECTURE.md):

| Path | What | Arrives |
|---|---|---|
| `services/solver/` | Python engine package + CLI (`mealplan`) | **M0 (now)** |
| `examples/` | founder household config — dev corpus, not shipped, not a test fixture | M0 |
| `apps/api/` | NestJS API | M2 |
| `apps/web/` | Angular web app | M2 |
| `packages/contracts/` | generated TS types + JSON schemas | M2 |
| `mealplan/` | **v1 prototype — untouched reference implementation** until the M0 parity gate | frozen |

## Method fragments & technique library (Track E, draft content)

`data/methods-draft/` holds composable method-step fragments for every
`examples/` component (stations, active/passive, provisional durations,
temp-keyed oven steps) and `data/techniques/techniques.yaml` maps each step
`operation` to a taught technique (video placeholders pending owner recordings).
Draft **content** awaiting the M1.10 schema — the engine does not read these
yet. Schema + integration contract: `data/methods-draft/README.md`; lint:
`python3 data/methods-draft/lint.py`.

Dev (M0/M1 needs only a Python venv):

```sh
.venv/bin/pip install -e services/solver
make test
.venv/bin/mealplan doctor --library examples
```
