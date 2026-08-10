# Kitchen sim — first run

**Founder corpus, shopping trip only (slice 1). Two drivers over the same
sheets: a deterministic robot with no judgment, and an LLM cook with real
kitchen knowledge. 52 findings, 18 recorded assumptions, 1 blocker.**

Neither driver could see this repository, the PRD, the YAML corpus, or the
engine. Both had only `shopping_list.md` and `cook_plan.md` and a store that
was authored independently of the planner's own ingredient data.

| | robot (the floor) | cook (the ceiling) |
|---|---|---|
| rows bought | 34 / 35 | 34 / 35 |
| register total | $435.93 | **$449.55** vs the sheet's $415.80 (+8.1%) |
| findings | 25 | 52 |
| assumptions forced | — (cannot assume) | **18** |
| stopped | never (it cannot) | once |

**The headline number is 18.** That is how many times a person had to supply
knowledge the sheets did not contain in order to finish one shopping trip.
Every one of them is something a less experienced cook could get wrong.

---

## BLOCKER — `spices` is not a thing you can buy

```
shopping_list:36 | - [ ] spices — 3 × 100g (need 226g, 74g leftover)
```

Both drivers failed this row independently. The cook searched "spices", then
tried "seasoning", then stopped:

> *the shopping list just says 'spices' with a total gram weight, but nothing
> on any shelf is called 'spices' — it's not a real product, it's a bucket*

It then noticed the deeper problem, which the robot could not: **every savory
recipe in the cook plan calls for some amount of generic `spices` — 5 g here,
35 g there — and nothing anywhere says which spice.** The plan cannot be
cooked as written, no matter what you put in the cart.

`dried_chiles` is the same defect one step less severe: ancho and guajillo are
different chiles, not substitutes, and the store sells nothing generic.

---

## The list speaks the solver's units, not the shelf's

Eleven of thirty-five rows request by weight what no shop sells by weight.

```
shopping_list:20 | - [ ] cilantro — 9 × 60g (need 490g, 50g leftover) — keeps 5d raw
shopping_list:16 | - [ ] lime — 3 × 335g (need 880g, 125g leftover) — keeps 14d raw
shopping_list:13 | - [ ] eggs — 2 × 600g (need 1200g, 0g leftover) — keeps 21d raw
```

You cannot buy 880 g of lime. You buy limes, or a 2 lb mesh bag. You cannot
buy 600 g of eggs; you buy a dozen. Cilantro is sold in rubber-banded bunches
that run 45–90 g, so "9 × 60g" is a fiction of precision — and roughly half a
bunch is stem.

The cook resolved every one of these by hand and recorded each as an
assumption. The robot bought the top search hit and moved on, which is
precisely the silent wrong answer the gate exists to expose.

**Pack sizes are invented too.** Chicken breast is modelled at 907 g packs;
the shelf's family pack is 1134 g, so the honest purchase is 4 packs, not 5.
Lard is modelled at 15 oz tubs; the real tub is 16 oz and one covers the need.
Queso fresco is modelled at 12 oz; the shelf has 10 oz, so one round falls
short and you must buy two.

---

## Grams that are not food — the `edible_fraction` hole

The cook, and separately an inspection of the corpus, found the same thing.

> *a whole avocado is ~200 g but only ~140 g of that is edible flesh after pit
> and skin (~30 % loss) — I bought 4 instead of the list's 3, and I'm still
> not sure that's enough*

**Exactly one ingredient in `examples/ingredients.yaml` carries an
`edible_fraction`: `chicken_wings` at 0.66.** Every one of these has none, and
is therefore counted as though skin, pit, core, stem and bone were food:

| ingredient | actual waste |
|---|---|
| pineapple | ~45 % (rind + core) |
| lime | ~65 % if the recipe wants juice |
| cilantro | ~50 % by weight (stem) |
| avocado | ~30 % (pit + skin) |
| scallion | ~20 % (roots, tough tops) |
| poblano | ~15 % (stem + seeds) |
| garlic | ~13 % (papery skin) |
| onion_yellow | ~10 % (skin + ends) |

The consequence is two-sided: the shopping list **under-buys** produce, and the
macro arithmetic **over-counts** what is on the plate.

Worse, it is ambiguous which basis the corpus means. The cook hit this on
pineapple and had to guess:

> *a whole pineapple only yields ~900 g edible after cutting away the rind and
> core (about half the weight), which matches the list's own 900 g package* —
> so is the 600 g the recipe wants whole fruit, or already-trimmed?

Nothing in the data can answer that. The same ambiguity governs bone-in vs
boneless: the cook bought boneless pork shoulder and boneless chicken thighs
by reading the *cook plan's* steps ("cut the beef chuck into large chunks",
"never mentions pulling a bone out"), which is a ~25 % yield swing the
shopping list never mentions.

---

## Fluid ounces read as weight

```
shopping_list | - [ ] coconut_aminos — 1 × 17 oz (need 60g, ...)
```

> *the list's '17oz' looks like a fluid-ounce label being read as a weight,
> which doesn't match either real bottle size*

Same for the oils: *"oil is sold in fluid ounces, not grams, so the list's
package size was never going to match anything on the shelf"* (neutral oil,
950 g assumed) and *"no 750 g olive oil exists — olive oil is sold by mL"*.

---

## Bought by nobody

Salt and water are used in cooking steps and appear on no shopping row. The
robot found these mechanically, by scanning step prose against the store's own
vocabulary — no hint was given that anyone was looking.

## One row hiding two ingredients

> *`white_rice_dry` (row 32) is actually two different rices the list hides
> behind one line: the cilantro lime rice needs plain long-grain white rice
> but the separate jasmine rice dish needs jasmine*

## Safety-adjacent

- **Refreezing.** The cook plan freezes half the shrimp on day 0 and thaws it
  before session 1. The cook bought *frozen* shrimp specifically because
  counter "fresh" shrimp is already-thawed and cannot be safely refrozen —
  reasoning the sheets never supply. A shopper who buys counter shrimp
  follows the plan into an unsafe refreeze.
- **Shelf life.** The list says `keeps 3d raw` for chicken breast; the package
  says 2 days.

---

## What the cook had to already know

All 18 assumptions, each one a thing the sheets should have said:

`chicken_breast` boneless vs split · `chicken_breast` pack count · `beef_chuck`
whole roast vs pre-cut cubes · `pork_shoulder` bone-in vs boneless ·
`chicken_thigh` boneless · `shrimp_raw` frozen vs counter, and count size ·
`roma_tomato` sold loose by the pound · `eggs` dozens not grams · `lime` mesh
bag · `orange_juice` carton size · `pineapple` trimmed vs whole basis ·
`cilantro` bunch count · `cheddar_shred` sharp vs mild · `pinto_beans_canned`
whether brine counts toward the weight · `neutral_oil` bottle · `olive_oil`
bottle · `lard` tub count · `coconut_aminos` bottle

A cook who knows less than this one does not finish the trip.

---

## What to do

1. **Ship purchase units, not solve units.** The shopping list should render
   what a shopper buys — bunches, dozens, cans, each — with grams as the
   parenthetical. This single change resolves eleven rows.
2. **Name products, not categories.** `spices` and `dried_chiles` must resolve
   to real ingredients before anyone cooks from this. `white_rice_dry` must
   split.
3. **Fill in `edible_fraction`** for the eight produce items above, and decide
   — explicitly, in `DATA_GUIDE.md` — whether corpus grams are gross or
   trimmed. Right now nobody can tell.
4. **Audit oz-vs-fl-oz** across the corpus; at least three rows look like a
   volume label stored as a weight.
5. **Put salt and water on the list**, or state that staples are assumed.
6. **Say "freeze on arrival"** where the plan's freezer bridge depends on it.

Items 1–3 are also the cheapest way to cut the assumption count, which is the
number this gate exists to drive toward zero.
