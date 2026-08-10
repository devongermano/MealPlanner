# tools/fdc — vendored USDA FoodData Central macro corpus

The raw-ingredient ground-truth authority for mealplan. Per PRD §6 the *recipe*
format is the contract and recipes are ground truth for cooked components; this
directory supplies the layer beneath that: authoritative per-100g macros for
**raw, as-purchased single ingredients**, vendored offline from USDA FoodData
Central (public domain), so ingredient macros are never invented — not by a
human typing from memory, and not by an LLM during ingestion (the LLM doctrine:
models convert and disambiguate, they never originate nutrition numbers).

## What lives where

| Path | Committed? | What |
|---|---|---|
| `tools/fdc/download.py` | yes | Fetches SR Legacy + Foundation Foods bulk CSVs into `/tmp/fdc-cache` (raw dumps are **never** committed) and writes the manifest |
| `tools/fdc/transform.py` | yes | Filters + flattens the cache into the committed extract; deterministic output |
| `tools/fdc/index.py` | yes | Offline ranked search over the extract (pure functions, no network) |
| `tools/fdc/lint_ingredients.py` | yes | Standalone linter for a mealplan-format `ingredients.yaml` (parses YAML directly; never imports the mealplan package) |
| `data/fdc/MANIFEST.yaml` | yes | Source URLs, release labels, retrieval date, sha256 per archive |
| `data/fdc/extract.json` | yes | The vendored corpus: ~2.9k entries, < 1 MB |
| `/tmp/fdc-cache/**` | **no** | Raw USDA zips + extracted CSVs |

Extract entry shape (per 100 g, grams):

```json
{"fdc_id": 171077, "description": "Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw",
 "data_type": "sr_legacy_food", "category": "poultry", "fdc_category": "Poultry Products",
 "p": 22.5, "f": 2.62, "c": 0.0}
```

Filtering: a curated FDC-category allowlist (meats, poultry, seafood, produce,
grains/dry, dairy & eggs, fats, legumes, nuts/seeds, spices; sausages only when
explicitly raw/fresh) plus a cooked/processed-state description blocklist.
kcal is deliberately **not** stored — Atwater 4/9/4 from macro grams is the one
canonical computation (PRD P3).

## Ingestion-time flow (what this is for)

When a recipe is converted into the mealplan format (human or Claude-assisted,
M3's PR-5 protocol), every raw ingredient line goes through disambiguation:

1. Query the offline index with the ingredient name:
   `python tools/fdc/index.py "chicken thigh raw"`
2. Present the ranked candidates (description + fdc_id + p/f/c). A human — or
   the operator LLM under the ingestion protocol — picks one. The tool ranks;
   it never auto-commits a choice.
3. The chosen entry supplies the per-100g macros, and the ingredient records
   provenance:

   ```
   usda_fdc:<fdc_id>@<snapshot>      e.g.  usda_fdc:2646170@sr2018-04+fnd2025-12-18
   ```

   `<snapshot>` is the `snapshot:` value in `data/fdc/MANIFEST.yaml` — it names
   the exact source releases, so any macro value can be traced to a reviewable
   line in a specific USDA publication.
4. `lint_ingredients.py` runs over the resulting `ingredients.yaml` as a gate:
   physics checks (p+f+c ≤ 100 g/100g, Atwater density in [0, 900] kcal/100g,
   no negative macros) are ERRORs; a >15% deviation from the best FDC candidate
   is a WARN for human review (deviations are often legitimate — label vs. lab
   values, regional cuts — which is why it warns instead of failing).

## Snapshot upgrade procedure

SR Legacy is frozen (final release 2018-04). Foundation Foods gets new
releases; to upgrade:

1. Bump the Foundation URL + release in `DATASETS` in `download.py`.
2. `python tools/fdc/download.py` (writes a fresh `data/fdc/MANIFEST.yaml`).
3. `python tools/fdc/transform.py` (rewrites `data/fdc/extract.json`; output is
   sorted one-entry-per-line precisely so this diff is reviewable).
4. Review the diff: entry count and per-category counts printed by transform,
   plus the actual macro changes on entries our corpora reference.
5. Re-run `lint_ingredients.py` against the live ingredient files; triage new
   warnings.
6. Commit manifest + extract together, in one reviewed commit. Existing
   provenance strings keep their old `@<snapshot>` label — they remain true
   statements about where a number came from; re-resolving an ingredient
   against the new snapshot is an explicit, per-ingredient decision, not a
   side effect of upgrading.

## What later main-chain integration consumes

This track is data + tooling only (the `services/solver` package is untouched).
The later integration work consumes:

- **`data/fdc/extract.json`** — stable schema: `snapshot` + `entries[]` with
  `fdc_id / description / data_type / category / fdc_category / p / f / c`.
- **`index.search()`** — pure ranked lookup for ingestion-time disambiguation
  (importable as a module; no I/O beyond loading the extract it is handed).
- **The provenance grammar** — `usda_fdc:<fdc_id>@<snapshot>` as the source
  field on ingredient records when the schema grows provenance (PRD §8.1 /
  P9's provenance direction).
- **`lint_ingredients.py`** — runnable in CI against any ingredients file
  today, before any schema integration lands.

## Dev

```
python -m venv .venv-d && .venv-d/bin/pip install pyyaml pytest
cd tools/fdc && ../../.venv-d/bin/python -m pytest tests/   # offline; committed fixtures only
```
