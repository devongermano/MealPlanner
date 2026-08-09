#!/usr/bin/env python3
"""
plan.py — constraint-based batch cooking planner.

The problem is two problems. This file keeps them separate on purpose:

  1. WHICH components to cook this week.  Combinatorial, small, fuzzy objective
     (shared perishables, cuisine variety, hands-on time). Greedy + local search.
     A solver here would be overkill and the objective isn't really linear anyway.

  2. HOW MANY GRAMS of each, to whom, on which day.  Continuous and exact.
     This is a linear program and heuristics get it wrong. Composite dishes have
     fixed macro ratios, so the usual "set protein, set fat, float the starch"
     trick does not work — that trick assumes separated components.

Commands:
    python3 plan.py doctor                  what the library can and cannot hit, and why
    python3 plan.py menu [--n 10]           choose this week's components
    python3 plan.py week  [--menu a,b,c]    full 7-day plan + cook list
    python3 plan.py shop  [--menu a,b,c]    shopping list, purchase units, waste
    python3 plan.py all                     everything, written to plan.md
"""

import argparse, itertools, math, os, random, sys
from pathlib import Path

try:
    import yaml, pulp
except ImportError:
    sys.exit("need: pip install pyyaml pulp")

ROOT = Path(__file__).parent
LIB = ROOT / "library"
MACROS = ("protein", "fat", "carb")
KCAL = {"protein": 4, "fat": 9, "carb": 4}
SHORT = {"protein": "p", "fat": "f", "carb": "c"}


# --------------------------------------------------------------------------- #
#  load + derive
# --------------------------------------------------------------------------- #
def load():
    ing = yaml.safe_load((LIB / "ingredients.yaml").read_text())["ingredients"]
    raw = yaml.safe_load((LIB / "components.yaml").read_text())["components"]
    ppl = yaml.safe_load((LIB / "people.yaml").read_text())

    comps = {}
    for c in raw:
        tot = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carb": 0.0}
        tags, missing = set(), []
        for name, grams in c["ingredients"].items():
            if name not in ing:
                missing.append(name)
                continue
            i = ing[name]
            tot["kcal"] += i["kcal"] * grams / 100
            tot["protein"] += i["p"] * grams / 100
            tot["fat"] += i["f"] * grams / 100
            tot["carb"] += i["c"] * grams / 100
            tags |= set(i.get("tags") or [])
        if missing:
            sys.exit(f"component {c['id']}: unknown ingredients {missing}")
        y = c["yield_g"]
        c = dict(c)
        # macros are DERIVED from the ingredient list. never hand-entered.
        c["per100"] = {k: round(v * 100 / y, 3) for k, v in tot.items()}
        c["tags"] = sorted(tags)
        comps[c["id"]] = c
    settings = dict(ppl["settings"])
    settings["budget"] = ppl.get("budget", {"mode": "off"})
    return ing, comps, ppl["people"], settings


def human_pack(g):
    """454g -> '1 lb'. Nobody shops in grams."""
    if g % 454 == 0 and g >= 454:
        n = g // 454
        return f"{n} lb" if n > 1 else "1 lb"
    if g in (227, 340, 425, 439, 473, 568, 780, 907, 946, 1360, 1814, 2270):
        oz = round(g / 28.35)
        return f"{oz} oz" if oz <= 34 else f"{oz/16:.1f} lb"
    return f"{g}g"


def fmt_miss(miss):
    return ", ".join(
        f"{SHORT[k]} forced {abs(v)}g OVER" if v > 0 else f"{SHORT[k]} {abs(v)}g SHORT"
        for k, v in miss.items())


def eligible(comp, person):
    return not (set(comp["tags"]) & set(person.get("exclude", [])))


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


def kcal_of(t):
    return sum(t[m] * KCAL[m] for m in MACROS)


# --------------------------------------------------------------------------- #
#  layer 2 — the plate LP
# --------------------------------------------------------------------------- #
def plate(person, comps, ids, weights=None, tol=None, locked=None):
    """One day, one person. Returns (ok, {id: grams}, {macro: grams_missed}).

    Discrete components (tortillas, meatballs, wings) are handled in two passes:
    solve continuous, snap them to whole units, then re-solve the rest. Doing it
    as a true MILP is correct but ~100x slower and the difference is under a
    tortilla.
    """
    tol = person["tolerance"] if tol is None else tol
    weights = dict(weights or {})
    for d in person.get("dislikes") or []:      # SOFT: weighted down, not banned
        weights[d] = weights.get(d, 1.0) * 6.0
    usable = [i for i in ids if eligible(comps[i], person)]
    if not usable:
        return False, {}, {m: person["targets"][m] for m in MACROS}

    def build(fixed=None):
        fixed = fixed or {}
        m = pulp.LpProblem("plate", pulp.LpMinimize)
        g, slack = {}, {}
        for i in usable:
            c = comps[i]
            lo, hi = c["serve_g"]["min"], c["serve_g"]["max"]
            if i in fixed:
                g[i] = fixed[i]
                continue
            v = pulp.LpVariable(f"g_{i}", 0, hi)
            b = pulp.LpVariable(f"b_{i}", cat="Binary")
            m += v >= lo * b
            m += v <= hi * b
            g[i] = v
        cap = person.get("max_daily_mass_g")
        if cap:
            # total plate weight. This is what stops "eat five pounds of rice".
            m += pulp.lpSum(g[i] for i in usable
                            if not isinstance(g[i], (int, float))) <= cap - sum(
                v for v in g.values() if isinstance(v, (int, float)))
        for mac in MACROS:
            tgt = person["targets"][mac]
            e = pulp.lpSum(g[i] * comps[i]["per100"][mac] / 100 for i in usable)
            over = pulp.LpVariable(f"o_{mac}", 0)
            under = pulp.LpVariable(f"u_{mac}", 0)
            m += e - over <= tgt * (1 + tol)
            m += e + under >= tgt * (1 - tol)
            slack[mac] = (over, under)
        nz = pulp.lpSum(
            v for i, v in g.items() if not isinstance(v, (int, float))
        )
        # violations dominate; weights break ties toward requested variety
        m += 10_000 * pulp.lpSum(x for pr in slack.values() for x in pr) + pulp.lpSum(
            weights.get(i, 1.0) * g[i] for i in usable if not isinstance(g[i], (int, float))
        ) * 0.001
        return m, g, slack

    seed_fixed = {k: v for k, v in (locked or {}).items() if k in usable}
    m, g, slack = build(seed_fixed)
    if m.solve(pulp.PULP_CBC_CMD(msg=0)) != 1:
        return False, {}, {mac: person["targets"][mac] for mac in MACROS}

    # snap discrete components to whole units, then re-solve the continuous rest
    fixed = dict(seed_fixed)
    for i in usable:
        u = comps[i].get("unit_g")
        if u and i not in fixed:
            val = g[i].value() or 0
            fixed[i] = round(val / u) * u
    if fixed:
        m, g, slack = build(fixed)
        if m.solve(pulp.PULP_CBC_CMD(msg=0)) != 1:
            return False, {}, {mac: person["targets"][mac] for mac in MACROS}

    miss = {}
    for mac, (o, u) in slack.items():
        ov, un = (o.value() or 0), (u.value() or 0)
        if ov > 0.5:
            miss[mac] = round(ov, 1)        # positive = forced OVER target
        elif un > 0.5:
            miss[mac] = -round(un, 1)       # negative = cannot REACH target
    out = {}
    for i in usable:
        val = g[i] if isinstance(g[i], (int, float)) else (g[i].value() or 0)
        if val >= 5:
            out[i] = int(round(val))
    return (not miss), out, miss


# --------------------------------------------------------------------------- #
#  doctor — why is it infeasible, and what class of thing is missing
# --------------------------------------------------------------------------- #
def doctor(comps, people, settings):
    lines = []
    ids = list(comps)
    lines.append("## Feasibility\n")
    for pname, p in people.items():
        ok, pl, miss = plate(p, comps, ids)
        elig = [i for i in ids if eligible(comps[i], p)]
        blocked = [i for i in ids if not eligible(comps[i], p)]
        t = p["targets"]
        lines.append(
            f"**{pname}** — {t['carb']}c / {t['fat']}f / {t['protein']}p "
            f"({kcal_of(t):.0f} kcal), excludes {p.get('exclude') or 'nothing'}"
        )
        lines.append(f"- {len(elig)}/{len(ids)} components eligible"
                     + (f"; blocked: {', '.join(blocked)}" if blocked else ""))
        if ok:
            lines.append(f"- **feasible** on the full library (±{p['tolerance']:.0%})")
        else:
            lines.append(f"- **INFEASIBLE** — misses "
                         + fmt_miss(miss))
            for t2 in (0.08, 0.10, 0.15):
                if plate(p, comps, ids, tol=t2)[0]:
                    lines.append(f"  - would clear at ±{t2:.0%} tolerance")
                    break
            else:
                lines.append("  - does not clear even at ±15%. This is a library gap, "
                             "not a tolerance problem.")
        lines.append("")

    # the structural check that actually matters
    lines.append("## Structural check\n")
    leans = [i for i, c in comps.items() if c.get("anchor") == "lean"]
    lines.append(f"- tagged lean anchors: {len(leans)} ({', '.join(leans) or 'NONE'})")

    # Real ablation, not a vibe check: strip mains from leanest to fattiest and
    # find the exact point each person goes infeasible. That number IS the answer
    # to "how many lean proteins does a week actually need".
    mains = [i for i, c in comps.items() if c["role"] == "main"]
    leanness = sorted(mains, key=lambda i: -(comps[i]["per100"]["protein"]
                                             / max(comps[i]["per100"]["fat"], 0.1)))
    lines.append("- protein-to-fat ratio of mains, leanest first: "
                 + ", ".join(f"{i} ({comps[i]['per100']['protein']/max(comps[i]['per100']['fat'],0.1):.1f})"
                             for i in leanness[:5]) + " …")
    for pname, p in people.items():
        broke = None
        for k in range(1, len(leanness)):
            trimmed = [i for i in comps if i not in leanness[:k]]
            ok, _, miss = plate(p, comps, trimmed)
            if not ok:
                broke = (k, miss)
                break
        if broke:
            k, miss = broke
            lines.append(
                f"  - **{pname}** survives losing the {k-1} leanest main(s); removing "
                f"`{leanness[k-1]}` too makes the week infeasible ("
                + fmt_miss(miss)
                + f"). So {pname} needs at least **{k}** lean-ish protein(s) on the menu."
            )
        else:
            lines.append(f"  - **{pname}** stays feasible no matter which mains are cut "
                         "(their target is loose relative to this library).")
    dense = [i for i, c in comps.items() if c["per100"]["kcal"] >= 250]
    lines.append(f"- calorie-dense carriers (>=250 kcal/100g): {len(dense)}")
    return "\n".join(lines), lines


# --------------------------------------------------------------------------- #
#  layer 1 — menu selection (shared perishables, variety, time budget)
# --------------------------------------------------------------------------- #
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


def score_menu(comps, ing, chosen, settings, people=None):
    """CHEAP score — no LP. Structural proxies only, so local search can run hot.
    Actual macro feasibility is verified separately, on the shortlist."""
    rows, wp, _ = purchase(comps, ing, chosen)
    active = cook_minutes(comps, settings,
                          estimate_batches(comps, people or {}, settings, chosen)
                          if people else {i: 1 for i in chosen})
    cuisines = len({comps[i]["cuisine"] for i in chosen})
    roles = {}
    for i in chosen:
        roles[comps[i]["role"]] = roles.get(comps[i]["role"], 0) + 1
    pen = 0
    pen += max(0, active - settings["active_min_budget"]) * 12   # over time budget, hard
    pen += 0 if roles.get("main", 0) >= 3 else 4000
    pen += 0 if roles.get("starch", 0) >= 2 else 4000
    pen += 0 if roles.get("accent", 0) >= 2 else 1500
    pen -= cuisines * 900                                        # reward variety

    # BUDGET. Not hardcoded — read from library/people.yaml or the CLI. Over the
    # ceiling is penalised steeply; under it, cheaper is mildly better so the search
    # does not burn budget it did not need.
    cap = budget_ceiling(settings, people or {})
    est = menu_cost(comps, ing, chosen, people=people, settings=settings)
    if cap:
        pen += max(0, est - cap) * 120
    pen += est * 1.5

    # Per-person structural proxies. These are what actually predict LP feasibility,
    # and they cost nothing to evaluate.
    if people:
        for p in people.values():
            elig = [i for i in chosen if eligible(comps[i], p)]
            mains = [i for i in elig if comps[i]["role"] == "main"]
            starch = [i for i in elig if comps[i]["role"] == "starch"]
            acc = [i for i in elig if comps[i]["role"] in ("accent", "veg")]
            pen += 0 if len(mains) >= 3 else 6000
            # >=3 starches, not 2. Discovered the hard way: on the day before a cook
            # session some starches have expired, and a 588g/day carb target cannot be
            # met from two starches at realistic serving sizes.
            pen += 0 if len(starch) >= 3 else 6000
            # CARB CEILING. This is the constraint that actually binds for a 4700 kcal
            # day, and it is not fat. Calorie-dense fatty food does nothing for carbs —
            # you have to physically eat ~590g of them. Needs headroom for the days
            # when the short-keeping starches are gone.
            carb_ceiling = sum(comps[i]["serve_g"]["max"] * comps[i]["per100"]["carb"] / 100
                               for i in elig)
            pen += 0 if carb_ceiling >= p["targets"]["carb"] * 1.45 else 15000
            # everyone needs at least one accent they can actually eat — that's the
            # per-plate fine-tuning lever. An accent only ONE of you can eat is not
            # waste, it's the entire point of separating accents out.
            pen += 0 if len(acc) >= 2 else 2500
            # need enough protein-per-fat headroom to reach protein before fat caps out
            need_ratio = p["targets"]["protein"] / max(p["targets"]["fat"], 1)
            lean = [i for i in mains
                    if comps[i]["per100"]["protein"] / max(comps[i]["per100"]["fat"], .1)
                    >= need_ratio * 1.25]
            # doctor's ablation says one lean main is not enough. And they must not all
            # expire together, or you get a hole in the middle of the week.
            pen += 0 if len(lean) >= 2 else 15000
            pen += 0 if len({comps[i]["keeps_days"] >= 4 for i in lean}) and \
                        any(comps[i]["keeps_days"] >= 4 for i in lean) else 8000
    return wp + pen, dict(waste_perishable=wp, active_min=active,
                          cuisines=cuisines, roles=roles)


def choose_menu(comps, ing, people, settings, n=10, seed=0, iters=4000,
                shortlist=25, must=None):
    """Two phase, because LPs are ~100x the cost of the cheap score.

    Phase 1  local search on the cheap structural score -> a shortlist of distinct
             candidate menus.
    Phase 2  LP-verify candidates best-first; return the first that is genuinely
             feasible for every person. If none are, return the best candidate
             along with who broke and why, so doctor can say something useful.
    """
    rng = random.Random(seed)
    ids = list(comps)
    sc = lambda sel: score_menu(comps, ing, sel, settings, people)

    # ---- phase 1: cheap search, collecting everything it passes through -------
    seen = {}
    for restart in range(6):
        r2 = random.Random(seed * 100 + restart)
        must = [m for m in (must or []) if m in comps]
        rest = [i for i in ids if i not in must]
        sel = list(must) + r2.sample(rest, max(0, n - len(must)))
        cur, _ = sc(sel)
        for _ in range(iters // 6):
            swappable = [i for i in sel if i not in must]
            if not swappable:
                break
            out = r2.choice(swappable)
            inn = r2.choice([i for i in ids if i not in sel])
            cand = [i for i in sel if i != out] + [inn]
            s2, _ = sc(cand)
            if s2 <= cur:
                sel, cur = cand, s2
            seen[frozenset(cand)] = s2
        seen[frozenset(sel)] = cur

    ranked = sorted(seen.items(), key=lambda kv: kv[1])[:shortlist]

    # ---- phase 2: verify best-first ------------------------------------------
    first_fail = None
    for keys, _score in ranked:
        sel = sorted(keys)
        broke = {}
        for pname, p in people.items():
            ok, _, miss = plate(p, comps, sel)
            if not ok:
                broke[pname] = miss
        if not broke:
            _, info = sc(sel)
            return sel, info, True, {}
        if first_fail is None:
            first_fail = (sel, broke)

    sel, broke = first_fail
    _, info = sc(sel)
    return sel, info, False, broke


# --------------------------------------------------------------------------- #
#  week assembly
# --------------------------------------------------------------------------- #
def diverse_plates(person, comps, menu, k=16, seed=0):
    rng = random.Random(seed)
    out = []
    for _ in range(k * 8):
        w = {i: rng.uniform(0.35, 2.4) for i in menu}
        ok, pl, _ = plate(person, comps, menu, weights=w)
        if ok and pl and pl not in out:
            out.append(pl)
        if len(out) >= k:
            break
    return out


def sessions_for(settings):
    """Two cook sessions. Session 0 cooks on day 1, session 1 on day 4 (0-indexed 0 and 3).
    A component cooked in session s is edible on day d iff 0 <= d - start(s) < keeps_days."""
    return settings.get("cook_days", [0, 3])


def available_on(comp, day, settings):
    for start in sessions_for(settings):
        if start <= day and (day - start) < comp["keeps_days"]:
            return True
    return False


def assign_week(plates, days, maxsame):
    if not plates:
        return []
    chosen, used = [], {}
    for _ in range(days):
        best, bestsc = None, None
        for p in plates:
            if any(used.get(c, 0) + 1 > maxsame for c in p):
                continue
            sc = sum(used.get(c, 0) ** 2 for c in p)
            if bestsc is None or sc < bestsc:
                bestsc, best = sc, p
        if best is None:
            best = min(plates, key=lambda p: sum(used.get(c, 0) ** 2 for c in p))
        chosen.append(best)
        for c in best:
            used[c] = used.get(c, 0) + 1
    return chosen


def build_week(comps, people, settings, menu):
    """Day by day, because a component's eligibility depends on WHICH day it is.
    Guacamole keeps 2 days; it cannot be on the day-7 plate no matter how well
    the macros work out."""
    days = settings["days"]
    cap_batches = settings.get("max_batches_per_component", 3)
    weeks, demand = {}, {}
    for pname, p in people.items():
        wk, used_days, used_g = [], {}, {}
        for d in range(days):
            fresh = [i for i in menu if available_on(comps[i], d, settings)]

            def pool(cap_days, cap_b):
                # The variety cap belongs on MAINS and sauces. Nobody gets bored of
                # rice. Capping starch the same way starves the back half of the week.
                return [i for i in fresh
                        if (comps[i]["role"] != "main"
                            or used_days.get(i, 0) < cap_days)
                        and used_g.get(i, 0) < comps[i]["yield_g"] * cap_b]

            best = None
            # try strict first, then progressively relax rather than emit an empty day
            for cd, cb in ((settings["max_days_same_component"], cap_batches),
                           (settings["max_days_same_component"] + 1, cap_batches + 1),
                           (days, cap_batches + 2)):
                avail = pool(cd, cb)
                bestsc = None
                for pl in diverse_plates(p, comps, avail, k=10,
                                         seed=(d * 31 + hash(pname)) % 9973):
                    sc = sum(used_days.get(c, 0) ** 2 for c in pl)
                    if bestsc is None or sc < bestsc:
                        bestsc, best = sc, pl
                if best:
                    break
            if best is None:
                best = {}
            wk.append(best)
            for c, gmz in best.items():
                used_days[c] = used_days.get(c, 0) + 1
                used_g[c] = used_g.get(c, 0) + gmz
                demand[c] = demand.get(c, 0) + gmz
        weeks[pname] = wk
    return weeks, demand


# --------------------------------------------------------------------------- #
#  report
# --------------------------------------------------------------------------- #
def render(comps, ing, people, settings, menu, weeks, demand, docmsg, menuinfo):
    L = ["# Week plan\n", docmsg, "\n## Menu\n"]
    for i in menu:
        c = comps[i]
        pc = c["per100"]
        L.append(f"- **{c['name']}** (`{i}`) — {c['cuisine']}, {c['role']}"
                 + (", lean anchor" if c.get("anchor") == "lean" else "")
                 + f" — {pc['kcal']:.0f} kcal / {pc['protein']:.1f}p / {pc['fat']:.1f}f "
                   f"/ {pc['carb']:.1f}c per 100g — {c['active_min']}min active, "
                   f"keeps {c['keeps_days']}d"
                 + (f" — source: {c['source']}" if c.get("source") else ""))
    L.append(f"\nHands-on total: **{menuinfo['active_min']} min** "
             f"(budget {settings['active_min_budget']}), "
             f"{menuinfo['cuisines']} cuisines.\n")

    # batches
    L.append("## Cook list\n")
    L.append("| component | need | batches | cook | leftover |")
    L.append("|---|---|---|---|---|")
    batches = {}
    for i in menu:
        need = demand.get(i, 0)
        b = max(1, math.ceil(need / comps[i]["yield_g"] - 1e-9)) if need else 0
        batches[i] = b
        made = b * comps[i]["yield_g"]
        if b:
            L.append(f"| {comps[i]['name']} | {need}g | {b} | {made}g | {made-need}g |")

    L.append("\n## Custom foods to create in your tracker\n")
    L.append("Create each of these once, per 100g. Then you only ever log a weight.\n")
    L.append("| food | kcal | protein | fat | carb |")
    L.append("|---|---|---|---|---|")
    for i in menu:
        pc = comps[i]["per100"]
        L.append(f"| {comps[i]['name']} | {pc['kcal']:.0f} | {pc['protein']:.1f} "
                 f"| {pc['fat']:.1f} | {pc['carb']:.1f} |")

    for pname, wk in weeks.items():
        p = people[pname]
        t = p["targets"]
        L.append(f"\n## {pname} — target {t['carb']}c / {t['fat']}f / {t['protein']}p "
                 f"({kcal_of(t):.0f} kcal)\n")
        if not wk:
            L.append("_no feasible plate found — run `plan.py doctor`_")
            continue
        for d, pl in enumerate(wk, 1):
            if not pl:
                gone = [comps[i]["name"] for i in menu
                        if not available_on(comps[i], d - 1, settings)]
                L.append(f"**Day {d}** — NO FEASIBLE PLATE.")
                L.append(f"  - past shelf life by day {d}: "
                         + (", ".join(gone) if gone else "nothing"))
                L.append("  - fix one of: move the 2nd cook day later (`cook_days` in "
                         "people.yaml), swap in a longer-keeping protein, or freeze "
                         "half of a batch on cook day.\n")
                continue
            tot = {m: sum(g * comps[c]["per100"][m] / 100 for c, g in pl.items())
                   for m in MACROS}
            L.append(f"**Day {d}** — {kcal_of(tot):.0f} kcal, "
                     f"{tot['protein']:.0f}p / {tot['fat']:.0f}f / {tot['carb']:.0f}c")
            for c, g in sorted(pl.items(), key=lambda x: -x[1]):
                u = comps[c].get("unit_g")
                extra = f"  ({g//u} × {comps[c]['name'].lower()})" if u else ""
                L.append(f"  - {comps[c]['name']}: **{g}g**{extra}")
            L.append("")

    # ---- cost + volume ----
    bought = menu_cost(comps, ing, [i for i in menu if batches.get(i)], batches)
    shares, eaten = attribute(comps, ing, weeks, bought)
    cap = budget_ceiling(settings, people)
    b = settings.get("budget") or {}
    L.append("\n## Cost\n")
    L.append(f"- groceries: **${bought:,.2f}**"
             + (f" against a ${cap:,.0f} ceiling — "
                + ("**OVER by $%.2f**" % (bought - cap) if bought > cap else
                   "$%.2f under" % (cap - bought)) if cap else " (no ceiling set)"))
    L.append(f"- food actually eaten: ${sum(eaten.values()):,.2f} — "
             f"the ${bought - sum(eaten.values()):,.2f} difference is leftover you paid for")
    L.append("")
    L.append("| person | share of groceries | % of cost | % of calories |")
    L.append("|---|---|---|---|")
    tk = sum(kcal_of(people[pn]["targets"]) for pn in weeks)
    for pn in weeks:
        pk = kcal_of(people[pn]["targets"])
        line = (f"| {pn} | ${shares[pn]:,.2f} | {shares[pn]/max(bought,.01)*100:.1f}% "
                f"| {pk/tk*100:.1f}% |")
        L.append(line)
    if (b.get("mode") == "per_person"):
        for pn, lim in (b.get("per_person") or {}).items():
            if pn in shares and shares[pn] > lim:
                L.append(f"\n> **{pn} is ${shares[pn]-lim:,.2f} over their ${lim} ceiling.** "
                         "Either raise it, or accept less variety on their side.")
    L.append("\n## Daily food volume\n")
    L.append("| person | avg/day | range | cap |")
    L.append("|---|---|---|---|")
    for pn, wk in weeks.items():
        ms = [sum(pl.values()) for pl in wk if pl]
        if not ms:
            continue
        capm = people[pn].get("max_daily_mass_g")
        L.append(f"| {pn} | {sum(ms)/len(ms):.0f}g ({sum(ms)/len(ms)/453.6:.1f} lb) "
                 f"| {min(ms)}-{max(ms)}g | {capm or 'none'} |")

    rows, wp, wt = purchase(comps, ing, [i for i in menu if batches.get(i)], batches)
    L.append("\n## Shopping list\n")
    L.append("| ingredient | need | buy | leftover | keeps |")
    L.append("|---|---|---|---|---|")
    for name, need, units, pack, left, per, keeps in rows:
        if not per:
            continue
        L.append(f"| {name} | {need}g | {units} × {pack}g | {left}g | {keeps}d |")
    L.append("\n_Shelf-stable (not a constraint):_ "
             + ", ".join(n for n, *_r in rows if not _r[4]))
    L.append(f"\n**Perishable leftover: {wp}g.** That is the number this whole "
             "thing exists to minimize.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
def frontier(comps, ing, people, settings, lo, hi, step, n):
    """What does each budget level actually buy? This is the answer to
    'how much variety can we afford' — a curve, not a number."""
    print(f"{'budget':>8} {'spend':>8} {'dishes':>7} {'cuisines':>9} "
          f"{'waste':>7} {'feasible':>9}")
    print("-" * 54)
    seen_menus = {}
    for cap in range(lo, hi + 1, step):
        st = dict(settings)
        st["budget"] = {"mode": "shared", "total": cap}
        menu, info, feas, broke = choose_menu(comps, ing, people, st, n=n)
        spend = menu_cost(comps, ing, menu, people=people, settings=st)
        mains = len([i for i in menu if comps[i]["role"] in ("main", "starch")])
        print(f"{cap:>8} {spend:>8.0f} {mains:>7} {info['cuisines']:>9} "
              f"{info['waste_perishable']:>6}g {'yes' if feas else 'NO':>9}")
        seen_menus[cap] = (menu, feas)
    return seen_menus


def parse_budget(txt):
    if not txt:
        return None
    if "=" in txt:
        pp = dict(kv.split("=") for kv in txt.split(","))
        return {"mode": "per_person", "per_person": {k: float(v) for k, v in pp.items()}}
    return {"mode": "shared", "total": float(txt)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["doctor", "menu", "week", "shop", "all", "frontier"])
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--menu", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="plan.md")
    ap.add_argument("--budget", default=None,
                    help="override: '550' for a shared pot, or 'devon=320,jimbo=240'")
    ap.add_argument("--mass", default=None, help="override: 'jimbo=2200'")
    ap.add_argument("--exclude", default="", help="components to keep off the menu")
    ap.add_argument("--force", default="", help="components that must be on the menu")
    ap.add_argument("--range", default="400:700:50", help="frontier sweep lo:hi:step")
    a = ap.parse_args()

    ing, comps, people, settings = load()
    if a.budget:
        settings["budget"] = parse_budget(a.budget)
    if a.mass:
        for kv in a.mass.split(","):
            k, v = kv.split("=")
            people[k]["max_daily_mass_g"] = float(v)
    for cid in [x for x in a.exclude.split(",") if x]:
        comps.pop(cid, None)
    if a.cmd == "frontier":
        lo, hi, st = (int(x) for x in a.range.split(":"))
        frontier(comps, ing, people, settings, lo, hi, st, a.n)
        return
    docmsg, _ = doctor(comps, people, settings)

    if a.cmd == "doctor":
        print(docmsg)
        return

    if a.menu:
        menu = [x.strip() for x in a.menu.split(",")]
        unknown = [x for x in menu if x not in comps]
        if unknown:
            sys.exit(f"unknown components: {unknown}")
        _, menuinfo = score_menu(comps, ing, menu, settings)
    else:
        menu, menuinfo, feas, broke = choose_menu(comps, ing, people, settings,
                                                  n=a.n, seed=a.seed)
        if not feas:
            print("!! best menu found is not feasible for everyone:", file=sys.stderr)
            for who, miss in broke.items():
                print(f"   {who}: {fmt_miss(miss)}", file=sys.stderr)
            print("   -> loosen tolerance in people.yaml, raise --n, or add a "
                  "component that fixes the gap. run `plan.py doctor`.\n", file=sys.stderr)

    if a.cmd == "menu":
        for i in menu:
            print(f"{i:24s} {comps[i]['cuisine']:9s} {comps[i]['role']}")
        print(f"\nactive {menuinfo['active_min']}min, "
              f"perishable waste {menuinfo['waste_perishable']}g, "
              f"cuisines {menuinfo['cuisines']}")
        return

    weeks, demand = build_week(comps, people, settings, menu)
    out = render(comps, ing, people, settings, menu, weeks, demand, docmsg, menuinfo)

    if a.cmd == "shop":
        print(out.split("## Shopping list")[1])
        return
    Path(a.out).write_text(out)
    print(out)
    print(f"\n\n[written to {a.out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
