---
name: mealplan
description: Plan a week of batch cooking for the household under macro targets, allergen exclusions, shelf life, and shared-ingredient waste. Use when Devon asks what to cook this week, wants to add a recipe he found to the library, asks whether a menu will hit macros, or asks why a plan came out infeasible. Also use for "what should I make", "plan the week", "add this recipe", "will this hit my macros".
---

# mealplan

A constraint solver for batch cooking. The library lives in `library/`, the solver
is `plan.py`. **Never do the macro arithmetic yourself — run the tool.** It is a
linear program and it will beat you every time, especially on the 4,700 kcal side.

## Setup (once)

```bash
pip install pyyaml pulp
```

## The one thing to understand first

This is two separate problems and the code keeps them apart:

1. **Which components to cook** — combinatorial, small, fuzzy objective (shared
   perishables, variety, hands-on time). Solved with greedy + local search on a
   cheap structural score, then LP-verified.
2. **How many grams, to whom, on which day** — continuous and exact. A linear
   program. Heuristics fail here because the dishes are *composite*: a burrito
   welds protein, fat, and carb at a fixed ratio, so the usual "set protein, set
   fat, float the starch" trick doesn't apply.

## Commands

```bash
python3 plan.py doctor                 # what the library can/can't hit and WHY
python3 plan.py menu --n 12            # pick this week's components
python3 plan.py week --menu a,b,c      # 7-day plan from a specific menu
python3 plan.py all  --n 12            # everything -> plan.md
python3 plan.py all  --n 12 --seed 3   # different week, same constraints
```

Run `doctor` first, always. Read what it says before proposing food.

## Adding a recipe Devon sends you

He will drop a TikTok, a Substack post, or a link. Convert it and append to
`library/components.yaml`. Steps:

1. Get the ingredient list with real quantities. If a source is paywalled, **say
   so and ask him to paste it** — do not guess quantities and do not try to route
   around a paywall.
2. Convert every quantity to **grams**. Volume → weight, "1 medium onion" → 150g,
   "2 cloves garlic" → 6g.
3. Check each ingredient exists in `library/ingredients.yaml`. Add any that don't,
   with per-100g macros. Prefer USDA FoodData Central over guessing.
   `pack_g` = how it's actually sold. `keeps_days` = realistic fridge life.
4. Estimate `yield_g` — the **finished cooked weight of one batch**. Braises lose
   ~30%, ground meat ~25%, rice roughly triples, pasta roughly 2.2x. This number
   drives every macro downstream, so think about it rather than guessing round.
5. Set `serve_g.min/max` from what a person would actually eat in a day. **These
   matter.** Without them the solver prescribes eleven tortillas.
6. Set `unit_g` only for discrete things (tortillas, meatballs, wings).
7. Tag `role` and, if it's a low-fat protein, `anchor: lean`.
8. Re-run `plan.py doctor` and report what changed.

Do **not** hand-enter macros for a component. They are derived from the ingredient
list. If a component's macros look wrong, the ingredient list or `yield_g` is wrong.

## The accent rule (this is the important one)

Accents are their **own components**. Structural allergens live **inside** the
component.

- Breakfast burrito → `egg_potato_scramble` + `flour_tortilla` + `cheddar_shred`.
  Cheddar is tagged `dairy`, so Devon simply gets 0g. Jimbo gets 120g.
- Nachos → would be **one** component tagged `dairy`, because you can't un-bake it.
  The solver will never serve it to Devon.

There is no variant machinery. The data model is the answer. **The cooking
consequence: push exclusions to the finishing step.** Cook one universal base,
diverge at plating. Cheese goes on top, not in the pan.

## Known structural facts about this household

Re-derive these with `doctor` rather than trusting them, but they've held so far:

- **Jimbo's binding constraint is carbohydrate, not fat.** 588g/day is ~590g of
  physical starch. Calorie-dense fatty food does nothing for it. The menu needs
  **at least 3 starches** and real carb-density headroom, or the days right before
  a cook session go infeasible. Cheese is not the answer to a 4,700 kcal day;
  tortillas, rice, potatoes and pasta are.
- **One lean protein is not enough.** Every fatty protein carries fat with it, so
  with only rich mains there's no way to add protein without overshooting fat. The
  menu needs ≥2 lean anchors, and they must not all expire on the same day.
- **Soy is the hard exclusion.** Soy sauce, gochujang, doenjang, hoisin, and oyster
  sauce are all out. Use coconut aminos + gochugaru + fish sauce. Every `cn_am`
  component in the library is already built this way — keep it that way.
- **Dairy is almost always an accent**, which is why Mexican works so well here.
  Keep it that way when adding recipes.

## When the solver says infeasible

`doctor` reports direction: `f forced 12g OVER` means the opposite of `c 12g SHORT`.
Read it before reacting.

In rough order of what to try (structural fixes FIRST — PRD §8.3):

1. Add the *class* of component that's missing — lean protein for a forced-over-fat,
   dense starch for a carb shortfall. `doctor` names the gap and its direction.
2. Move `cook_days`. `[0,3]` (Sun/Wed) leaves day 7 stranded; `[0,4]` (Sun/Thu)
   generally works better with 3-5 day shelf lives.
3. Raise a `serve_g.max` — but only if a person would really eat that much.
4. LAST resort: loosen `tolerance` in `library/people.yaml`. It is the cheapest
   knob precisely because it redefines success instead of fixing the plan
   (PRD §8.3 — corrected in M0.17; this file previously listed it first).

## Reporting back

Give him the cook list, the per-day assembly, and the "custom foods" table (he and
Jimbo log by weight against per-100g entries in their trackers). Lead with anything
`doctor` flagged. Don't bury an infeasibility in a nice-looking table.
