"""The solver: plate LP, doctor diagnostics, menu search, week assembly.

Extracted verbatim from plan.py — behavior-preserving, bug-for-bug. Known
prototype defects deliberately kept for the M0.1 parity gate (each is a Phase 2
task with its own regression test):
- build_week seeds diverse_plates with hash(pname) — nondeterministic across
  interpreter runs (M0.3).
- score_menu computes waste at 1 batch while cost uses estimated batches (M0.10).
- score_menu's shelf-life-stagger check at the bottom is a boolean no-op (M0.11).
- choose_menu rebinds `must` inside the restart loop (M0.5 --force wiring).

The problem is two problems, kept separate on purpose:
1. WHICH components to cook this week — combinatorial, small, fuzzy objective.
   Greedy + local search (choose_menu).
2. HOW MANY GRAMS of each, to whom, on which day — continuous and exact. A
   linear program (plate).
"""

import math
import random

import pulp

from .costing import (budget_ceiling, cook_minutes, estimate_batches, menu_cost,
                      purchase)
from .units import MACROS, fmt_miss, kcal_of


def eligible(comp, person):
    return not (set(comp["tags"]) & set(person.get("exclude", [])))


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
