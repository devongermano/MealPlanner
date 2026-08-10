# Predictions — written BEFORE the first run

A gate nobody has tested is a rubber stamp. The cheapest way to test this one
is to write down, in advance, what it must discover — then run it and score
itself. **If the gate misses these, the gate is broken and gets fixed before
one word of its output is believed.**

These were derived by reading the founder-corpus sheets by hand while
designing the gate. None of them is hinted to the cook, the robot, or the
catalog author; each has to be rediscovered mechanically.

Scored against a run of:

```
python -m kitchen_sim.run --sheets <persona>/sheets --json findings.json
```

A prediction is **HIT** only if a finding carries the stated `defect_class`
*and* names the stated subject. "Something vaguely related was mentioned"
does not count — that standard is how a gate talks itself into passing.

| # | Prediction | Expected `defect_class` | Subject |
|---|---|---|---|
| P1 | `spices` is not a purchasable object. No supermarket sells a product called "spices"; the row is an internal category that leaked onto a shopping list. | `product_not_found` | `spices` |
| P2 | Cilantro is requested by weight (`9 × 60g`, need 490g) but is sold by the bunch. Nobody weighs cilantro at the shelf. | `unit_untranslatable` | `cilantro` |
| P3 | Several pack sizes do not exist on real shelves, so the honest purchase differs from the stated one and the register total diverges from the sheet's `$415.80`. | `cost_deviation` | receipt vs stated total |
| P4 | Salt is used in cooking steps but bought by no shopping row. | `ingredient_absent_from_list` | `salt` |
| P5 | Water is used in cooking steps (rice, pasta, chile soaking) with no quantity and no source. | `ingredient_absent_from_list` *or* `assumption_required` | `water` |
| P6 | At least one requested quantity cannot be met by whole packages of a real product, leaving the plan short. | `quantity_deviation` | any |
| P7 | Items requiring refrigeration or freezing on arrival are never marked as such — shrimp keeps 2 days raw, is cooked on day 4, and the only hint is a THAW note in a different file presupposing a freeze nobody instructed. | `storage_unspecified` | `shrimp_raw` |

## Scoring the gate itself

- **P1–P4 are must-hit.** They are mechanical, unambiguous, and each is
  reachable by pure arithmetic over the sheets plus an honest store. Missing
  any one means the reconciliation is not doing its job.
- **P5–P7 are should-hit.** P5 and P7 depend on how the cook reasons about
  prose and storage; the deterministic robot may legitimately miss P7, which
  is precisely the kind of gap the LLM cook exists to cover.
- **False positives matter too.** A run that produces a hundred findings has
  not found more; it has stopped being readable. If the finding count exceeds
  ~40 for a single shopping trip, the taxonomy is too eager and needs
  tightening before the report is trusted.

## What a HIT on P1 actually proves

That the store catalog was authored independently. If the catalog had been
generated from the planner's own `ingredients.yaml`, a `spices` SKU would
exist — because the planner believes `spices` is a thing you can buy — and P1
would be structurally unreachable. P1 failing is therefore an alarm about the
*catalog*, not only about the sheets.

## Results

### 2026-08-10 · driver `naive` (deterministic robot) · founder corpus

**4/4 must-hit predictions landed. 14 findings, zero false positives.**

| # | Result | Evidence |
|---|---|---|
| P1 | **HIT** | `product_not_found` — "nothing in the cart satisfies the shopping row for 'spices'". The robot searched every shelf and came home without it. |
| P2 | **HIT** | `unit_untranslatable` — cilantro, and **ten more rows besides**: flour_tortilla, eggs, poblano, lime, avocado, pineapple, garlic, scallion, pinto_beans_canned, chicken_stock. |
| P3 | **MISS — and the product looks good for it.** | Register total $435.93 against the sheet's $415.80: **+4.8%**, inside the 10% band. I predicted the estimate would diverge materially; it did not. The planner's costing is more accurate than I gave it credit for. |
| P4 | **HIT** | `ingredient_absent_from_list` — salt is used in cooking steps and bought by no row. |
| P5 | **HIT** | `ingredient_absent_from_list` — water, likewise. |
| P6 | **MISS — check is unreachable by this driver.** | The robot buys `ceil(need / pack)`, so it can never come up short. `quantity_deviation` needs an over-purchase/waste arm to be reachable. Gate defect, logged. |
| P7 | **MISS — out of slice.** | `storage_unspecified` needs the put-away phase, which is slice 3. Not a failure. |

**The headline finding is P2's true size.** Eleven of thirty-five shopping rows —
almost a third of the list — ask for food by weight that no store sells by
weight. You cannot buy 880 g of lime, 490 g of cilantro, or 6146 g of canned
beans; you buy limes, bunches, and cans. The list is expressed in the
solver's units rather than the shelf's.

**Two false-positive rounds preceded this**, both in the prose scan, and both
worth recording because they are the failure mode this file exists to catch:
word-matching flagged `limes`/`tomatoes`/`jalapenos` against rows that buy
exactly those (plural mismatch), and SKU-identity matching flagged `garlic`
and `chicken stock` because prose and rows resolved to *different* products
for the same food. Stemmed all-token coverage against what the list buys
produced zero false positives. A gate that cried wolf 78% of the time would
have been worse than no gate.

### Gate defects found by this run

1. `quantity_deviation` is unreachable for any driver that rounds up. Needs an
   over-purchase arm: compare actual leftover against the row's *stated*
   leftover, which is a real test of the planner's waste arithmetic.
2. Cost comparison should be reported as a line every run, not only when it
   breaches a threshold — the delta is informative even when it passes.
