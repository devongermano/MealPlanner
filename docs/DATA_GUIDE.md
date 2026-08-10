# The data guide

*Written for the owner. Everything the system knows about food lives in a handful of
text files; this explains what's in them, where each number comes from, and which ones
you're allowed to change by hand. No prior knowledge assumed — anything with a special
meaning gets defined the first time it shows up.*

---

## 1. The short version

There are four kinds of things, and they stack:

| | what it is | example |
|---|---|---|
| **Ingredient** | something you buy | 907 g of chicken thighs |
| **Component** | something you cook in a batch | a pot of soy-free orange chicken |
| **Dish** | a plate, made of components | orange chicken over jasmine rice |
| **Person** | who's eating and what they need | 180 g protein, no dairy |

You write the ingredients and the components. The dishes say how components go back
together. The system does the rest: it picks a menu of dishes, works out how many grams
of each component each person eats each day to hit their numbers, converts that into
batches to cook, converts *that* into a shopping list, and prints sheets saying what to
eat.

The one rule that explains most of the design: **a number is either something you
measured, or something the system computed from things you measured. Never both.**
Nutrition numbers for a recipe are always computed. If you could type them in by hand,
they'd drift away from the ingredients within a month and nobody would notice.

---

## 2. Where everything lives

```
examples/                  THE LIBRARY — your actual food, hand-written
  ingredients.yaml           ~70 things you buy, with macros and pack sizes
  components.yaml            26 things you cook, as ingredient lists
  people.yaml                who eats, their targets, and household settings

data/                      SUPPORTING DATA — mostly not hand-written
  dishes-draft/              NEW: how components assemble into plates (draft)
    dishes.yaml                16 dishes with per-serving amounts
    accent_baselines.yaml      how much of each finishing touch, per dish
    DISH_REVIEW.md             your approve/edit/reject checklist
    lint.py                    consistency checker
  methods-draft/             cooking steps for each component, one file each
  techniques/                what "mince" or "bloom" means, for cook mode
  fdc/                       a vendored copy of the USDA nutrition database

plans/                     OUTPUT — written by the system, never by hand
  2026-08-09/                one folder per plan, named for its shopping day
    plan.yaml                  the locked plan: inputs, menu, portions, hash
    shopping_list.md           what to buy, in store units
    cook_plan.md               what to cook per session, scaled
    eat_devon.md               what Devon eats each day
    eat_jimbo.md               what Jimbo eats each day

tools/fdc/                 the pipeline that builds and checks against USDA data
services/solver/           the engine itself (code)
```

`.yaml` files are plain text you can open in any editor. Indentation matters — two
spaces, never tabs.

**`examples/` is yours.** It's the founder household's real library and real config. It
is deliberately *not* part of the product: no shipped code, test, or default may assume
anything in it. That's a rule ("instance data never in the product") that exists because
the first version of this system hardcoded this household's numbers into the software
itself, and nothing could be changed without editing code.

---

## 3. Ingredients — the things you buy

`examples/ingredients.yaml`. One line each. Here's a real one:

```yaml
chicken_wings: {p: 18.3, f: 14.1, c: 0.0, perishable: true, pack_g: 2270,
                keeps_days: 3, tags: [], cost: 16.00, edible_fraction: 0.66,
                freezable: true}
```

Field by field:

- **`p` / `f` / `c`** — grams of protein, fat and carbohydrate per 100 g of the raw,
  as-purchased item. These are the only nutrition numbers typed by hand anywhere in the
  system, and §5 explains where they should come from.
- **`perishable`** — `true` means it goes bad and the system worries about waste with it.
  `false` means it's effectively free of that concern (oils, canned tomatoes, spices).
- **`pack_g`** — how it comes off the shelf. Wings come in a 5 lb bag; you cannot buy
  1,400 g of them. The shopping list rounds up to whole packs, and the leftover is
  reported rather than pretended away.
- **`keeps_days`** — how long after you buy it it's still good **raw**. See §7.
- **`tags`** — allergen and diet labels: `dairy`, `wheat`, `sesame`, `treenut`, `fish`.
  These are the mechanism behind exclusions. A person who excludes `dairy` can never be
  served anything containing an ingredient tagged `dairy` — it's not a preference the
  system weighs, it's arithmetic it refuses to do.
- **`cost`** — price per pack. See §6 on how honest this currently is.
- **`edible_fraction`** — the share of the gross weight you actually eat. Wings are
  0.66: you put 100 g on the scale, you eat 66 g of meat and leave 34 g of bone. Omit
  it and it's 1.0. This exists because the first version didn't have it and overstated
  wing protein by about 50%.
- **`freezable`** — whether it survives the freezer. Used for a specific problem: if a
  cook session falls outside an ingredient's raw shelf life, a freezable ingredient goes
  in the freezer on shopping day and the cook plan gets a thaw note, instead of the
  session being declared impossible.
- **`negligible`** — only on `spices`. It means "the zeros are deliberate, not a
  missing entry." Without it, a validator that catches empty macros would flag it
  forever.

---

## 4. Components — the things you cook

`examples/components.yaml`. A component is **one thing you cook in a batch and later
weigh onto a plate.** This is the unit the whole engine works in, because it's the unit
a meal-prep kitchen actually works in — you cook a pot of picadillo, not one gordita.

```yaml
- id: picadillo
  name: Beef picadillo
  cuisine: mexican
  role: main
  source: "barefoodtim - gorditas de picadillo (Vitamina T)"
  yield_g: 1000
  serve_g: {min: 80, max: 450}
  keeps_days: 5
  freezes: true
  active_min: 40
  ingredients:
    ground_beef_85: 454
    potato_russet: 300
    roma_tomato: 370
    onion_yellow: 150
    carrot: 60
    neutral_oil: 30
    dried_chiles: 15
    garlic: 12
    spices: 5
```

- **`ingredients`** — grams of each ingredient in one batch. This *is* the recipe.
  Adding a recipe means listing what's in it, which is what a recipe already is.
- **`yield_g`** — what one batch **weighs when it's cooked**. Note that the ingredients
  above add up to 1,396 g but the yield is 1,000 g: 396 g of water left during
  simmering. You cannot compute this — you weigh it once, on a real cook day. It is the
  single most important hand-measured number in the system, because every nutrition
  figure divides by it.
- **`role`** — `main`, `starch`, `veg`, `accent`, or `drink` (reserved, not yet used).
  This drives composition rules: a meal is never a bucket of one thing.
- **`serve_g`** — the smallest and largest sensible portion. See §8; this one has a
  story.
- **`unit_g`** — for things that come in pieces: tortillas 71 g, wings 45 g, meatballs
  40 g, gordita shells 85 g. Portions snap to whole multiples. Omitted for things you
  can serve any amount of (rice, sauce, braise).
- **`keeps_days`** / **`freezes`** — cooked shelf life. See §7.
- **`active_min`** — hands-on minutes for one batch. Unattended oven or braise time
  doesn't count. Extra batches of the same thing cost less than the first (doubling a
  braise is nearly free); the household setting `batch_time_factor` says how much less.
- **`anchor: lean`** — marks a low-fat protein. Three components carry it:
  `shrimp_al_pastor`, `turkey_meatballs`, `salsa_verde_chicken`. Without enough of
  these, a leaner eater's targets become impossible to hit — there's no way to reach
  their protein without blowing past their fat.
- **`household_unit`** — for people who don't use a scale: `{name: cup, grams: 185}`
  lets a sheet say "about 2 cups of rice" instead of "370 g". The gram figures are the
  truth; this is a translation at the edge. 23 of the 26 components carry one — cups
  for anything scoopable, tablespoons for accents you spoon over a plate. The three
  without are the ones already counted in pieces (gordita shells, meatballs, wings),
  where a count beats a volume. Every grams-per-unit is a provisional estimate until
  someone weighs a real cup from the real recipe.
- **`source`** — where the recipe came from. `house`, `store`, or a named recipe such as
  `barefoodtim 010 - spicy sausage fusilli al forno (sauce base)`. §6.

**Why accents are their own components.** Cheddar isn't an ingredient inside the
breakfast burrito — it's a separate component you sprinkle at the plate. That single
decision means one batch of scramble and one stack of tortillas serve everyone: Devon
gets 0 g of cheddar, Jimbo gets 30 g, and nobody cooks two breakfasts. The flip side
holds too: something like nachos, where the cheese is baked in and can't be removed,
would be **one** component tagged `dairy`, and Devon would simply never be served it.
The rule is: if you can leave it off at the plate, it's its own component; if you can't,
it lives inside and its allergen tag hard-excludes the whole thing.

---

## 5. Dishes — how components go back on a plate *(new, in draft)*

This is the layer that was missing, and the reason a week could come out looking like
unrelated food piled together. When your recipes were loaded, each one was correctly
split into its cookable parts — but nothing recorded that a gordita is *shells, split
and stuffed with picadillo, topped with salsa and queso in roughly these proportions.*

`data/dishes-draft/dishes.yaml` is that record.

```yaml
- id: gorditas_de_picadillo
  name: Gorditas de picadillo
  source: "barefoodtim - gorditas de picadillo (Vitamina T)"
  reconstruction: from_source
  cuisine: mexican
  components:
    gordita_shell:         {base_g: 170, min_g: 85, max_g: 340}
    picadillo:             {base_g: 160, min_g: 80, max_g: 340}
  accents: [charred_salsa_roja, queso_fresco_crumbled, mexican_crema]
  compatible_sides: [refried_pintos, cilantro_lime_rice]
  meal_affinity: [lunch, dinner]
```

- **`base_g`** — one normal serving. The shape of the dish: two shells, 160 g of filling.
- **`min_g` / `max_g`** — how far each amount may move for a bigger or smaller eater
  before the plate stops reading as that dish. This is where the two-people-one-kitchen
  trick actually happens: same pots, same dish, Devon's gordita has more picadillo and
  no queso, Jimbo's has more of everything.
- **`accents`** — the finishing layer, which any one person may skip entirely. Just a
  list of names: an accent has no per-dish amount here, because the engine lets each
  one be either nothing or anything within that component's own sensible serving range.
  The amounts this reconstruction *did* author (30 g of salsa, 25 g of queso) live in
  `accent_baselines.yaml` next door, which is where the review document and the macro
  figures get them. Whether accents should carry a per-dish amount after all is an open
  question — 45 g of mozzarella baked onto pasta really isn't the 20 g crumbled over a
  taco.
- **`cuisine`** — stated outright, though the engine can work it out from the dish's
  main component. It matters for the one dish with two mains, and it keeps the value
  steady if a component is ever relabelled.
- **`compatible_sides`** — things that may be served alongside but aren't part of the
  dish (beans next to gorditas). No amounts here; the component's own `serve_g` governs.
- **`meal_affinity`** — which meal slots this belongs in. Breakfast-ness is a property
  of a *dish*, not a component: the scramble isn't inherently breakfast, the breakfast
  burrito is.
- **`reconstruction`** — an honesty label, because these files were reconstructed rather
  than transcribed:
  - `from_source` — a real recipe defines this assembly (gorditas, breakfast burrito,
    fusilli al forno).
  - `inferred` — the components come from a `house` recipe with no document to read, so
    the *format* was inferred from what the component obviously is (carnitas → tacos).
  - `invented` — nothing implies this plate; it was built to give a component a home.
    Three dishes are marked this way and each says so in its notes.

**Every number in that file is provisional and waiting on you.** Work through
`data/dishes-draft/DISH_REVIEW.md` — it's the same sixteen dishes in plain English with
approve / edit / reject boxes, plus five questions about gaps in the library.

Components stay the unit of cooking. Dishes don't change what you cook or how much;
they change what lands on the plate together, and they let the menu be chosen as
"gorditas and bulgogi bowls this week" instead of a list of ingredients.

---

## 6. Where the nutrition numbers come from

**You never type a recipe's macros.** Not once. Here's the whole chain, using picadillo:

**Step 1 — every ingredient contributes its grams × its macros.**

```
ground_beef_85   454 g × 18.6 g protein/100 g  =  84.4 g protein
potato_russet    300 g ×  2.1                  =   6.3 g
roma_tomato      370 g ×  0.9                  =   3.3 g
onion_yellow     150 g ×  1.1                  =   1.7 g
carrot            60 g ×  0.9                  =   0.5 g
dried_chiles      15 g × 10.6                  =   1.6 g
garlic            12 g ×  6.4                  =   0.8 g
neutral_oil, spices                            =   0
                                        total  =  98.6 g protein per batch
```

**Step 2 — divide by the cooked yield, not the raw weight.**

98.6 g of protein in a 1,000 g batch = **9.9 g per 100 g** of finished picadillo. Fat
and carbs work identically: 10.0 g and 10.3 g per 100 g.

**Step 3 — calories are computed, never stored.** 4 calories per gram of protein, 9 per
gram of fat, 4 per gram of carb — the Atwater factors:

```
(4 × 9.9) + (9 × 10.0) + (4 × 10.3) = 171 kcal per 100 g
```

There is no `kcal` field anywhere in the library, on purpose. The first version stored
calories *as well as* macros and ended up with two accountings that contradicted each
other.

**The bone-in wrinkle.** For wings, each ingredient's contribution is multiplied by its
`edible_fraction` first: 2,270 g of wings × 0.66 × 18.3/100 = 274 g of protein, not 415 g.
But the portion you weigh stays **gross** — the 1,600 g batch yield includes the bones,
and a 270 g serving is six whole wings of which about 178 g is edible. The macros already
account for it, which is why a wings plate doesn't look like 270 g of meat.

**What this means for you in practice:** to fix a recipe's nutrition, you fix its
ingredient list or re-weigh its yield. There is no other lever, and that's the point.

---

## 7. Provenance — knowing which numbers are real

Different numbers in these files have very different standing, and the system is
supposed to say which is which rather than presenting them all with the same confidence.

**Ingredient macros → USDA.** `data/fdc/` holds a vendored extract of the USDA
FoodData Central database (public domain), built by `tools/fdc/download.py` and
`transform.py`, with the exact source releases recorded in `data/fdc/MANIFEST.yaml`.
When an ingredient is added, you look it up (`python tools/fdc/index.py "chicken thigh
raw"`), pick from the ranked candidates, and record where the number came from in this
form:

```
usda_fdc:2646170@sr2018-04+fnd2025-12-18
```

The part after the `@` names the exact database releases, so any macro figure can be
traced to a reviewable line in a specific USDA publication. **Partly applied:** the
schema has no provenance *field* yet, so the ids live in comments and in
`data/fdc/LINT_DISPOSITIONS.md`. As of the 2026-08-09 lint pass, three ingredients
carry a provenance comment because their values were corrected against USDA
(`dried_chiles`, `chorizo_pork`, `ricotta`), and sixteen more were verified against a
named entry whose id is recorded in that file — enough to back-fill a real field when
the schema grows one. The rest are
label estimates, and the dispositions file says which and why. Check the corpus against
USDA yourself with:

```
python tools/fdc/lint_ingredients.py examples/ingredients.yaml
```

It errors on physically impossible entries (macros summing over 100 g per 100 g,
negatives, implausible calorie density) and warns when an entry drifts more than 15%
from the best USDA match. Drift is often legitimate — label values versus lab values,
different cuts — which is why it warns rather than failing.

**Prices → estimate or receipt.** The intended convention is that every price says
whether it's a guess or a real receipt with a date. **Not yet applied:** `cost: 16.00`
on the wings is an estimate and nothing in the file admits it. Until that lands, treat
every cost figure in the library as a rough estimate, and treat the plan's cost total as
an order-of-magnitude figure rather than a budget.

**Recipes → the `source` field.** `house` means your own kitchen. `store` means it's
bought ready-made. A named string like `barefoodtim 013 - soy sauce -> coconut aminos,
butter -> neutral oil` names the original and records what was changed. One thing to
know: a good part of the corpus derives from @barefoodtim's Substack, some of it from
paywalled posts. That's fine for your own kitchen and it is **quarantined from anything
shipped to another user** — a distributable starter library needs original or licensed
recipes.

**Constants that haven't been calibrated → labelled `provisional`.** `batch_time_factor:
0.45`, the 185 g-per-cup rice conversion, the relaxed-mode ±12% tolerance, every gram in
the draft dish file. These are placeholders with plausible values, and they're marked so
that nobody mistakes them for measurements. Cooking a real week and weighing things is
what turns them into real numbers.

---

## 8. Serving sizes, and the beans incident

`serve_g: {min: 80, max: 400}` on refried beans means: less than 80 g isn't a serving,
and more than 400 g in a day is not something a person will eat.

These bounds are **absolutes per dish, not per person** — and that was a deliberate
decision made the hard way. An earlier version scaled them by each person's calorie
target, on the theory that a bigger eater should get proportionally bigger portions.
The same evening, it produced a plan with 720 g of lard refried beans in one day — the
400 g cap multiplied by a 1.8 scaling factor — and that plan was rejected on sight.

The ruling: `serve_g` means *"more than this much of one dish is gross, period."* You
already write those numbers thinking of the biggest eater in the house, so scaling them
by appetite counts appetite twice. Appetite differences are served by **which** dishes
and **how many** dishes a person gets, not by bigger piles of one thing. The scaling
machinery still exists in the engine but is switched off and does nothing; reviving it
would require per-dish opt-in data, never a blanket formula.

Consequences worth knowing:

- If a plan is impossible, the fix is *structural* first — add the missing kind of
  recipe, move a cook day, raise a bound someone would genuinely eat. Loosening
  someone's tolerance is the labelled last resort, because it redefines success instead
  of fixing the plan.
- The dish file may narrow these bounds but never widen them; the lint enforces it.
- For pieces (`unit_g`), bounds must land on the grid. You can't serve 1.4 tortillas, so
  `max: 284` (4 × 71) is valid and `max: 300` is not.

---

## 9. Shelf life — three different fields that sound the same

This trips everyone up, so here they are side by side:

| field | on what | means |
|---|---|---|
| `keeps_days` | **ingredient** | how long it's good **raw**, after you buy it |
| `keeps_days` | **component** | how long it's good **cooked**, after you cook it |
| `freezable` | **ingredient** | survives freezing raw, so it can wait for a later session |
| `freezes` | **component** | a cooked batch can be frozen, extending when it can be eaten |

`keeps_days: 5` means usable on days 0 through 4 — five days counting the day it was
made. That convention is the same everywhere, and days are counted from 0 throughout
(day 0 is the first day of the plan).

A worked example, using the household's settings (`shop_days: [0]`, `cook_days: [0, 4]`):

- **Raw shrimp** keeps 2 days. You shop on day 0; the second cook session is day 4. Day
  4 minus day 0 is 4, which is past 2 — so the session *cannot* use shrimp bought that
  morning. But `freezable: true`, so it goes in the freezer on shopping day and the cook
  plan gets a thaw note. Without that flag, the system would have to refuse the session,
  and the first version of this software cheerfully planned 6-day-old raw shrimp without
  noticing.
- **Cooked shrimp al pastor** keeps 3 days and `freezes: false`. Cooked on day 0, it's
  eaten by day 2. It cannot stretch to the end of the week no matter what.
- **Salsa verde chicken** keeps 5 days and freezes. It exists specifically to plug the
  hole this creates: the diagnostics found that the day before a cook day had no lean
  protein left alive, which made a leaner eater's targets unreachable that day.

One deliberate simplification, stated rather than hidden: cooking resets the clock.
A component made from ingredients that were already three days old gets the same cooked
shelf life as one made from ingredients bought that morning.

---

## 10. People and household settings

`examples/people.yaml`, two sections.

**Per person:** daily `targets` in grams of protein / fat / carb (calories are derived
from those, never entered); `tolerance` as a fraction each macro may miss by; `exclude`
for allergen tags; `dislikes` (a soft preference, *not* the same as exclude); `mode` for
whether they use a kitchen scale; `meals_per_day` or named `meal_slots`;
`max_daily_mass_g` if there's a cap on how much sheer food they'll eat in a day.

Two distinctions that matter:

- **`exclude` vs `dislikes`.** Exclusions are permanent and make a plan impossible
  rather than bending. Dislikes are weights that push the menu away from something. A
  weekly veto ("not chicken again this week") is also soft. "Not this week" is never
  recorded as "can't eat."
- **`meal_slots` vs `meals_per_day`.** `meals_per_day: 3` gets you three unnamed meals.
  Named slots let each meal have its own serving style — Devon's breakfast is
  `family_style` ("have about this much") while lunch and dinner are `portioned` (packed
  into containers on cook day).

**Household settings:** `days` in the plan; `cook_days` and `shop_days` as day numbers;
`active_min_budget` (hands-on minutes available per week — cooking time is reported
against this and flagged when it overruns, never quietly fitted); `max_days_same_component`
as a variety guard; `min_lean_anchors`; `max_batches_per_component` so the solver can't
lean on one recipe for eleven pounds of shrimp; and the budget block.

---

## 11. What the system produces

Running a plan and locking it writes a folder under `plans/`, named for its **first
shopping day** — `plans/2026-08-09/`. Inside:

- **`plan.yaml`** — the locked plan. It carries a verbatim snapshot of everything that
  went in (library, people, settings, pantry, random seed) plus a fingerprint (a hash)
  over that snapshot, then the menu, every person's per-day portions and meals, and the
  batches per session. Locked means locked: for the rest of the week, every screen shows
  *this file*, never a fresh calculation. The first version re-solved the plan on every
  page load, so someone checking their Tuesday could see a menu nobody had shopped for.
- **`shopping_list.md`** — what to buy, in store units, after deducting what's already
  in the pantry, rounded up to whole packs with the leftovers named.
- **`cook_plan.md`** — per session: which components, how many batches, scaled ingredient
  amounts, and hands-on minutes.
- **`eat_<person>.md`** — per day per person: what to eat and how much, in grams for
  scale users or household units for everyone else, with the day's macro totals against
  that person's targets.

`mealplan verify-plan` re-runs the solve from the snapshot inside `plan.yaml` and checks
it reproduces the same fingerprint and the same portions. Same inputs and same seed
always produce the same plan, byte for byte — the first version used a random source
that varied run to run, so the same command could produce different plans.

---

## 12. What keeps it all from breaking

**Validation runs before anything reads the files, and before anything writes them.**
Three properties, all deliberate:

1. **Every error at once, not the first one.** Loading a file with six problems reports
   six problems with exact locations (`components.yaml: component 'picadillo',
   ingredient 'tofu'`), not the first one and a shrug.
2. **Writes are atomic.** A save validates first, writes to a temporary file, then
   renames it over the target. An invalid save is refused outright and leaves the
   original untouched. A malformed recipe cannot corrupt your library.
3. **Every file declares a `schema_version`.** When the format grows a field, old files
   are still identifiable as old files rather than being misread as broken new ones.

Things validation catches today: an ingredient that doesn't exist, a negative or zero
gram amount, a bad role or mode, serving bounds that don't sit on the piece grid, a
component nobody can eat, a person whose meal count can't be composed from their
available library.

**Separate checkers you can run by hand:**

```
python tools/fdc/lint_ingredients.py examples/ingredients.yaml   # macros vs USDA
python3 data/methods-draft/lint.py                               # cooking steps
python3 data/dishes-draft/lint.py                                # dishes
```

The dish lint checks that every component named in a dish exists, that no dish is
garnish-only, that amounts are sane and land on piece grids, that no dish authorises a
portion its component calls inedible, that accents can always be omitted, and that no
main is left without a dish. It currently reports zero errors and zero warnings.

---

## 13. Known gaps, as of this writing

Not a wishlist — these are places where the data is thinner than it looks:

- **The dish layer is a draft.** Sixteen dishes; three are invented outright and eight
  more are format guesses over house recipes. Nothing is wired into the engine yet —
  `dishes.yaml` is reviewable data, not live behaviour.
- **The library has one vegetable.** Korean smashed cucumber. Nothing green belongs next
  to the Italian or Mexican food.
- **No corn tortillas as a component**, though carnitas, birria and al pastor tacos all
  want them. They exist as an *ingredient*, unused.
- **No raw onion-and-cilantro finish** for tacos, and no *reserved* consommé for birria
  (the braise keeps its liquid inside the component, so there's nothing separate to dip
  a tortilla in).
- **Provenance has no schema field yet.** Ids live in comments and in
  `data/fdc/LINT_DISPOSITIONS.md`; 19 of 73 ingredients are tied to a named USDA entry,
  the rest are label estimates. Prices have no estimate / receipt labels at all, though
  the convention exists.
- **Yields are mostly estimates.** Every recipe's `yield_g` should be weighed on a real
  cook day. Every nutrition figure in the system divides by it, so these are the highest-
  leverage measurements available.
- **Cooking-step durations are guesses**, useful for ordering work but not yet for
  telling you how long a cook day takes.
