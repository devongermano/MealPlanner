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
| `tools/fdc/` + `data/fdc/` | vendored USDA FDC raw-ingredient macro corpus (Track D) | done |

Dev (M0/M1 needs only a Python venv):

```sh
.venv/bin/pip install -e services/solver
make test
.venv/bin/mealplan doctor --library examples
```

## Track D — USDA FDC macro corpus

`data/fdc/extract.json` vendors per-100g protein/fat/carb for ~2.9k plausibly-raw
single ingredients from USDA FoodData Central (SR Legacy + Foundation Foods,
public domain), with full source provenance in `data/fdc/MANIFEST.yaml`.
Tooling in `tools/fdc/`: offline candidate search for ingestion-time
disambiguation (`index.py`), a standalone `ingredients.yaml` plausibility +
FDC-deviation linter (`lint_ingredients.py`), and the reproducible
download/transform pipeline. Provenance grammar: `usda_fdc:<fdc_id>@<snapshot>`.
See [tools/fdc/README.md](tools/fdc/README.md).
