"""Purchasing, cook time, batch estimation, and cost attribution.

Extracted verbatim from plan.py. Known prototype defects preserved on purpose
for the M0.1 parity gate (each is a Phase 2 task with its own regression test):
- estimate_batches applies one global ceil to every chosen component (M0.4).
- cook_minutes amortizes across the whole week, disagreeing with serve.py's
  per-session split (M0.4).
"""

import math

from .units import KCAL, MACROS, kcal_of


def cost_per_g(comps, ing, cid):
    """Ingredient-proportional cost of one gram of a finished component."""
    c = comps[cid]
    tot = sum(ing[n]["cost"] / ing[n]["pack_g"] * g for n, g in c["ingredients"].items())
    return tot / c["yield_g"]


def attribute(comps, ing, weeks, grocery_total):
    """Split what the groceries ACTUALLY cost by consumption share.
    Leftovers get split too — you both paid for them."""
    eaten = {pn: sum(cost_per_g(comps, ing, cid) * g
                     for pl in wk for cid, g in pl.items())
             for pn, wk in weeks.items()}
    tot = sum(eaten.values()) or 1.0
    return {pn: grocery_total * v / tot for pn, v in eaten.items()}, eaten


def purchase(comps, ing, chosen, batches=None):
    """Ingredient demand -> whole purchase units -> leftover grams."""
    batches = batches or {i: 1 for i in chosen}
    need = {}
    for i in chosen:
        for name, grams in comps[i]["ingredients"].items():
            need[name] = need.get(name, 0) + grams * batches[i]
    rows, waste_perishable, waste_total = [], 0, 0
    for name, grams in sorted(need.items(), key=lambda x: -x[1]):
        pack = ing[name]["pack_g"]
        units = math.ceil(grams / pack - 1e-9)
        left = units * pack - grams
        waste_total += left
        if ing[name]["perishable"]:
            waste_perishable += left
        rows.append((name, round(grams), units, pack, round(left),
                     ing[name]["perishable"], ing[name]["keeps_days"]))
    return rows, waste_perishable, waste_total


def cook_minutes(comps, settings, batches):
    """Hands-on time for a whole week. Counting each recipe once is wrong — you
    cook most of them 2-5 times. Each extra batch costs batch_time_factor of the
    first (setup amortizes; a bigger braise is nearly free, meatballs are not)."""
    f = settings.get("batch_time_factor", 0.45)
    return round(sum(comps[i]["active_min"] * (1 + f * (b - 1))
                     for i, b in batches.items() if b > 0))


def estimate_batches(comps, people, settings, chosen):
    """How many batches will a week actually need? Scale by total calorie demand
    vs. what one batch of the whole menu delivers. Crude, but cheap and monotone —
    good enough to make a budget ceiling bind during the search. The real batch
    counts come out of build_week."""
    days = settings.get("days", 7)
    need = sum(kcal_of(p["targets"]) for p in people.values()) * days
    per_set = sum(comps[i]["yield_g"] * comps[i]["per100"]["kcal"] / 100 for i in chosen)
    if per_set <= 0:
        return {i: 1 for i in chosen}
    k = max(1, math.ceil(need / per_set))
    return {i: k for i in chosen}


def menu_cost(comps, ing, chosen, batches=None, people=None, settings=None):
    if batches is None and people and settings:
        batches = estimate_batches(comps, people, settings, chosen)
    rows, _, _ = purchase(comps, ing, chosen, batches)
    return sum(u * ing[n]["cost"] for n, g, u, pk, l, per, k in rows)


def budget_ceiling(settings, people):
    b = settings.get("budget") or {"mode": "off"}
    if b.get("mode") == "shared":
        return b.get("total")
    if b.get("mode") == "per_person":
        pp = b.get("per_person") or {}
        return sum(pp.get(pn, 0) for pn in people) or None
    return None
