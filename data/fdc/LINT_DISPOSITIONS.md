# FDC lint dispositions

Every finding `tools/fdc/lint_ingredients.py` reports against
`examples/ingredients.yaml`, dispositioned once so future runs have a rationale
instead of a wall of unexplained warnings.

Snapshot: `sr2018-04+fnd2025-12-18` (see `MANIFEST.yaml`).
Pass date: 2026-08-09. Findings dispositioned: **35** (16 warnings + 19 unmatched).

**Expected steady state after this pass:**

```
0 error(s), 13 warning(s), 19 unmatched, 41 clean FDC matches
```

A run that differs means something moved — a corpus edit, or a snapshot upgrade.
Diff against this file before assuming the lint is wrong.

---

## The standing rule

The lint ranks candidates **by name only**; it cannot tell that "drained solids"
and "solids and liquids" are different foods, so its top pick is often the wrong
sibling of the right food. Disposition therefore turns on one question:

> **Does the best candidate name the same food — same cut, same preparation
> state, same canning/draining state?**

- **Yes, and the macros disagree** → correct the corpus to the FDC value and
  record `usda_fdc:<id>@<snapshot>` in a comment above the entry.
- **No** → find the entry that *does* name our food. If our value agrees with it
  within the lint's threshold, the finding is a false positive: record the id
  here and change nothing.
- **The food is not in the extract at all** → record that, with the reason, and
  mark the value as an unverified estimate.

Two things this rule deliberately refuses to do:

1. **Flip between USDA releases.** SR Legacy and Foundation Foods sometimes carry
   two analyses of the identical food (garlic: 33.06 vs 28.2 g carb; ground
   turkey 93/7: 8.34 vs 9.59 g fat). Our value matching one of them is not an
   error, and rewriting it to the other buys nothing. The id we cite is the entry
   our number actually tracks.
2. **Adopt a neighbouring product's numbers.** Hot Italian sausage is not mild
   Italian sausage; gochugaru is not cayenne. Where the corpus disagrees with a
   near-neighbour, that is an owner question (below), not a silent edit.

---

## A. Corrected (3)

Comparator names the same food; the deviation was genuine. Provenance comments
are in `examples/ingredients.yaml` above each entry.

| ingredient | was (p/f/c) | now (p/f/c) | FDC entry | why |
|---|---|---|---|---|
| `dried_chiles` | 10.6 / 8.2 / 50.0 | 10.6 / 5.8 / 69.9 | `168570` Peppers, hot chile, sun-dried | Protein already matched this entry to the decimal while fat and carb did not — a partially-copied record. |
| `chorizo_pork` | 20.0 / 29.0 / 2.0 | 13.6 / 25.1 / 3.8 | `173859` Sausage, pork, chorizo, link or ground, raw | Old values look like cured Spanish chorizo; the corpus buys raw Mexican chorizo, which is what this entry is. |
| `ricotta` | 11.3 / 13.0 / 3.0 | 7.8 / 11.0 / 6.9 | `323444` Cheese, ricotta, whole milk | Matched no FDC ricotta: part-skim protein (11.39) against whole-milk-ish fat. Whole milk is this corpus's default. |

Ripple: `dried_chiles` is used in `picadillo` (15 g), `birria_chuck` (40 g) and
`shrimp_al_pastor` (20 g) — all small fractions of their batches, so derived
per-100g macros moved by at most 0.5 g (picadillo carb 10.0 → 10.3 and fat
10.1 → 10.0, birria carb 4.1 → 4.6, shrimp carb 5.9 → 6.4). `chorizo_pork` and `ricotta` are in the
ingredient library but used by no component, so they ripple nowhere.
Engine suite green before and after: 234 passed, 2 skipped.

## B. Verified — wrong sibling picked, no change (16)

Our value already agrees with the entry that actually names our food. The
`usda_fdc` column is the id our number tracks; it is the migration input for
when the schema grows a real provenance field.

| ingredient | ours (p/f/c) | correct entry | what the lint compared instead |
|---|---|---|---|
| `black_beans_canned` | 6.0 / 0.3 / 16.6 | `175188` Beans, black turtle, mature seeds, canned — 6.03/0.29/16.55 (exact) | `2644285` "drained **and rinsed**" (19.81 c). Draining concentrates the solids. |
| `pinto_beans_canned` | 5.4 / 0.6 / 15.8 | `175201` Beans, pinto, canned, **solids and liquids** — 4.6/0.56/15.18 | `174286` "drained solids" (20.22 c). Same food, different water. |
| `cashews` | 18.2 / 43.9 / 30.2 | `170162` Nuts, cashew nuts, raw (SR) — 18.22/43.85/30.19 (exact) | `2515374` the Foundation analysis of the same food. Release variance. |
| `garlic` | 6.4 / 0.5 / 33.0 | `169230` Garlic, raw (SR) — 6.36/0.5/33.06 (exact) | `1104647` Garlic, raw (Foundation, 28.2 c). Release variance. |
| `ground_turkey_93` | 19.0 / 8.0 / 0.0 | `172850` Turkey, ground, 93% lean, 7% fat, raw (SR) — 18.73/8.34 | `2514747` the Foundation analysis of the identical product. |
| `chicken_wings` | 18.3 / 14.1 / 0.0 | `172390` Chicken, broilers or fryers, wing, meat and skin, raw — 17.52/12.85 | `2727568` Foundation wings (10.64 f) — a leaner sampling. See owner checks. |
| `pork_shoulder` | 17.0 / 16.5 / 0.0 | `167843` Pork, fresh, shoulder, **whole**, separable lean and fat, raw — 17.18/17.99 | `169187` "Shoulder **breast**, boneless" — a lean cut at 3.4 g fat. Not a Boston butt. |
| `coconut_milk` | 2.0 / 21.3 / 2.8 | `170173` Nuts, coconut milk, **canned** — 2.02/21.33/2.81 (exact) | `170172` the **raw** (fresh-expressed) sibling. |
| `corn_frozen` | 3.2 / 1.2 / 19.0 | `168398` Corn, sweet, yellow, frozen, kernels **cut off cob** — 3.02/0.78/20.71 | `168400` kernels **on** cob (23.5 c) — cob mass changes the per-100g. |
| `parmesan` | 38.0 / 28.0 / 4.1 | `170848` Cheese, parmesan, hard — 35.75/25.0/3.22 | `172206` "Parmesan cheese **topping, fat free**" (5 g fat). |
| `cheddar_shred` | 24.9 / 33.1 / 3.1 | `328637` Cheese, cheddar — 23.3/34.0/2.44 | name-match only; macros were never compared. |
| `mozzarella_shred` | 22.2 / 22.4 / 2.4 | `170845` Cheese, mozzarella, whole milk — 22.17/22.14/2.4 (exact) | `169051` mozzarella **nonfat**. |
| `tomato_crushed_can` | 1.6 / 0.3 / 7.3 | `170501` Tomatoes, crushed, canned (SR) — 1.64/0.28/7.29 (exact) | correct food, flagged only for a partial name score. |
| `neutral_oil` | 0.0 / 100.0 / 0.0 | `172336` Oil, canola — 0/100/0 (exact) | `330458` Oil, coconut. Every culinary oil is 100 g fat; the choice is cosmetic. |
| `fusilli_dry` | 13.0 / 1.5 / 74.7 | `169736` Pasta, dry, enriched — 13.04/1.51/74.67 (exact) | `2710820` Bulgur. "Fusilli" is not FDC vocabulary. |
| `orzo_dry` | 13.0 / 1.5 / 74.7 | `169736` Pasta, dry, enriched — same entry | as above; orzo is a pasta shape, not a distinct food. |

## C. Absent from the extract (16)

No entry names this food. Values stand as **unverified label or reference
estimates** — not wrong, just uncited. Each row says why the food is missing,
because the reasons differ and only some are fixable.

**Filtered out by the extract's own rules** (`tools/fdc/transform.py`):

| ingredient | reason | lint's nonsense match |
|---|---|---|
| `honey` | "Sweets" is not in `CATEGORY_ALLOWLIST` | Dressing, honey mustard, fat-free |
| `brown_sugar` | same | Sugar-apples (sweetsop), raw |
| `chicken_stock` | "Soups, Sauces, and Gravies" not allowlisted | Chicken, ground, raw |
| `flour_tortilla` | "Baked Products" not allowlisted | the dry **tortilla mix** (67 c) — our value is a hydrated finished tortilla (51 c). Different food, not a bad number. |
| `corn_tortilla` | same | Corn, sweet, white, raw |
| `panko` | same | (none) |
| `fish_sauce` | `COOKED_BLOCKLIST_RE` blocks "sauce" | Fish, haddock, raw |
| `hot_sauce` | same | Peppers, hot chili, red, raw |
| `orange_juice` | `COOKED_BLOCKLIST_RE` blocks "juice" — see the note below | Oranges, raw (whole fruit) |

**Genuinely not in SR Legacy or Foundation Foods** (they live in FDC's Branded
Foods dataset, which this pipeline deliberately does not vendor, or nowhere):

| ingredient | note |
|---|---|
| `coconut_aminos` | branded product; no generic entry exists |
| `nutritional_yeast` | fortified branded product. Lint matched `167717` Yeast **extract spread** — that is Marmite, a different food |
| `gochugaru` | Korean coarse chili flake. Closest analogue `170932` Spices, pepper, red or cayenne (12.01/17.27/56.63): our protein and carb sit within 3% of it, our fat is well under. Not the same pepper or grind, so no correction. Even if cayenne's fat were right, it would move `smashed_cucumber` — the only component using gochugaru, at 5 g in a 620 g batch — by 0.09 g fat per 100 g |
| `mexican_crema` | no crème-fraîche-class entry. Our 30 g fat sits between `171257` sour cream (19.35) and `2346386` heavy cream (35.56), which is the right place for crema |
| `rice_vinegar` | distilled and cider vinegars are present (`172237`, `173469` at 0.04 and 0.93 g carb); rice vinegar specifically is not. Ours at 0.8 g carb is consistent with the class |
| `italian_sausage_hot` | FDC has no **hot** variant. Closest is `171631` Sausage, Italian, pork, mild, raw (13.9/24.26/2.97). See owner checks |
| `spices` | a synthetic aggregate carrying `negligible: true`, not a real food — no comparator can exist. Lint matched `170919` Spices, cardamom, which is why this warning is permanent and expected |

---

## Owner checks owed

Two numbers this pass could not settle from USDA alone. Both are answered by
reading a package the household already buys.

1. **Hot Italian sausage protein.** Corpus says 16.0 g; USDA's *mild* Italian
   sausage says 13.9 g — 15% apart, and chili flakes cannot raise protein. One
   of the two is a brand difference and one is a guess, and the package settles
   it. This one matters: `sausage_sugo` is 908 g of sausage in a 1,500 g batch,
   so a 2 g/100g error moves the sugo's protein density by about 12%.
2. **Chicken wings fat.** Corpus says 14.1 g. USDA has two answers for the same
   food: 12.85 (SR Legacy, broiler/fryer) and 10.64 (Foundation, newer
   sampling). Ours is within tolerance of the older figure. Worth a check only
   because wings are weighed gross and already carry an `edible_fraction`
   adjustment.

## Tooling follow-up (not actioned — outside this pass's scope)

`COOKED_BLOCKLIST_RE` blocks any description containing `juice`, which is
correct for "Beef, cooked, ... au jus" but wrong for **orange juice** — a raw,
as-purchased ingredient this corpus buys by the carton and uses in two
components (`orange_chicken` 250 g, `carnitas` 200 g). The blocklist term earns
its place elsewhere; the fix is probably an explicit allow for
`Fruits and Fruit Juices` entries whose description begins with a juice name,
decided by whoever owns `tools/fdc/`.

## Re-running

```
python3 tools/fdc/lint_ingredients.py examples/ingredients.yaml
python3 tools/fdc/index.py "<ingredient name> raw"   # ranked candidates for one item
```

After a snapshot upgrade (`MANIFEST.yaml` changes), the ids above keep their old
`@<snapshot>` label — they remain true statements about where a number came
from. Re-resolving an ingredient against a new snapshot is a deliberate,
per-ingredient decision, never a side effect of upgrading.
