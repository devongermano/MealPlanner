"""Purchasing, cook time, batch estimation, cost attribution, and the
canonical session plan.

M0.4: ``session_plan`` is THE one session-attribution function (PRD §8.2,
P10). It assigns each (component, day) demand to the EARLIEST cook session
whose batch is still within cooked shelf life on that day (economy over
freshness; provisional), computes per-session batches and minutes, and its
summed batches feed purchasing and menu cost. The prototype fork —
plan.py's global ceil vs serve.py's latest-session ceils — is dead.

M0.6: raw freshness. Shopping trips are data (``settings.shop_days``,
default ``[0]``). A component is cookable in session s iff every perishable
ingredient satisfies ``session_start - nearest_prior_shop_day < keeps_days``
OR is freezable (frozen on arrival -> thaw note in the cook plan).

``estimate_batches`` remains a deliberately crude ESTIMATE for the cheap
menu-search score only; every real surface consumes session_plan.
"""

import datetime
import math

from .units import kcal_of


def _as_date(v):
    """Coerce a stored date (datetime.date from YAML, or ISO string) to a
    datetime.date. Deterministic: parses stored values only — never reads a
    clock."""
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(v)


# --------------------------------------------------------------------------- #
#  sessions & raw freshness (M0.4 / M0.6)
# --------------------------------------------------------------------------- #
def sessions_for(settings):
    """Cook-session start days (0-indexed, sorted, deduped). A component
    cooked in session s is edible on day d iff 0 <= d - start(s) < keeps_days.

    ``cook_days`` is REQUIRED settings data (M0.17) — validated at load;
    there is no code default. Canonicalized here exactly like shop_days_for
    (Settings.from_raw already canonicalizes on load): session_plan's
    "earliest session wins" (PRD §8.2) must mean earliest DAY — the YAML
    list order of cook_days can never change attribution."""
    return sorted(set(settings["cook_days"]))


def shop_days_for(settings):
    """Shopping-trip days (0-indexed, sorted, deduped). Default: one trip on
    day 0."""
    return sorted(set(settings["shop_days"]))


def nearest_prior_shop_day(day, settings):
    """The most recent shopping trip on or before ``day``; None if the first
    trip is still ahead (nothing has been bought yet)."""
    prior = [t for t in shop_days_for(settings) if t <= day]
    return max(prior) if prior else None


def raw_freshness(comp, session_start, settings, ing):
    """Can ``comp`` be cooked in the session starting on ``session_start``?

    Returns ``(cookable, blockers, frozen)``:
    - blockers: perishable, non-freezable ingredients already past raw
      keeps_days at cook time (each: ingredient, shop_day, keeps_days) —
      any blocker makes the component uncookable in this session;
    - frozen: freezable ingredients past raw keeps_days — cookable, but
      frozen on arrival, so the cook plan carries a thaw note.
    """
    blockers, frozen = [], []
    for iname in comp["ingredients"]:
        i = ing[iname]
        if not i.get("perishable"):
            continue
        t = nearest_prior_shop_day(session_start, settings)
        if t is None:
            blockers.append(dict(ingredient=iname, shop_day=None,
                                 keeps_days=i["keeps_days"]))
            continue
        if session_start - t < i["keeps_days"]:
            continue                     # still fresh at cook time
        entry = dict(ingredient=iname, shop_day=t,
                     keeps_days=i["keeps_days"])
        (frozen if i.get("freezable") else blockers).append(entry)
    return (not blockers), blockers, frozen


def freezer_bridges(comp, settings):
    """M0.5 (PRD §8.1/§8.2): freezer-bridging availability. A component that
    ``freezes``, in a household that uses the freezer (``settings.use_freezer``,
    default true), stays usable past its cooked keeps_days: the batch goes
    into the freezer on cook day and later days serve it
    "from freezer — thaw ahead". With use_freezer false (or freezes
    false/absent) the strict shelf-life rule applies unchanged."""
    return bool(comp.get("freezes")) and bool(settings["use_freezer"])


def cookable_sessions(comp, settings, ing=None):
    """Indices of the cook sessions able to cook ``comp`` under the raw
    freshness rule. Without ingredient data every session qualifies."""
    ss = sessions_for(settings)
    if ing is None:
        return list(range(len(ss)))
    return [k for k, start in enumerate(ss)
            if raw_freshness(comp, start, settings, ing)[0]]


# --------------------------------------------------------------------------- #
#  pantry aging + cooked leftovers (M1.8, PRD §8.1)
# --------------------------------------------------------------------------- #
def age_pantry(pantry, ing, settings, plan_date):
    """Apply the stock 'acquired' age rule (PRD §8.1: age reduces effective
    raw keeps_days) and return ``(effective_pantry, warnings)``.

    For each stock entry: ``age_days = plan_date - acquired`` (a stock entry
    acquired AFTER the plan start is a hard error) and its REMAINING raw
    life is ``keeps_days - age_days``. A perishable, non-freezable entry
    that cannot survive to ANY cook session (no session start s with
    ``s < remaining``) is dropped from the effective pantry — its grams are
    NOT deducted from purchasing — and reported in a structured
    ``stock_expiring_unused`` warning. Non-perishable stock never ages out;
    freezable stock past raw life is frozen on arrival (the raw-freshness
    escape hatch) and stays deductible.

    Purchased (store) grams keep the existing nearest-prior-trip freshness
    rule (a deliberate, labeled simplification — per-trip shopping
    assignment is future multi-trip UX). The cooked list rides through
    untouched; its planning integration is ``cooked_leftovers``.
    """
    ss = sessions_for(settings)
    kept, warnings = [], []
    for r in (pantry.get("stock") or []):
        acquired = _as_date(r["acquired"])
        age = (plan_date - acquired).days
        if age < 0:
            raise ValueError(
                f"pantry stock of '{r['ingredient']}' is acquired "
                f"{acquired.isoformat()}, after the plan start date "
                f"{plan_date.isoformat()} — acquired dates cannot be in "
                "the future")
        i = ing[r["ingredient"]]
        if i.get("perishable") and not i.get("freezable"):
            remaining = i["keeps_days"] - age
            if not any(s < remaining for s in ss):
                warnings.append(dict(
                    code="stock_expiring_unused", ingredient=r["ingredient"],
                    grams=r["grams"], acquired=acquired.isoformat(),
                    age_days=age, keeps_days=i["keeps_days"],
                    remaining_days=remaining,
                    message=(f"{r['grams']}g of '{r['ingredient']}' stock "
                             f"(acquired {acquired.isoformat()}, "
                             f"{age} days old, keeps {i['keeps_days']}d raw) "
                             "cannot survive to any cook session — not "
                             "deducted from the shopping list; it will "
                             "expire unused")))
                continue
        kept.append(dict(r))
    return {"stock": kept, "cooked": list(pantry.get("cooked") or [])}, \
        warnings


def cooked_leftovers(pantry, comps, settings, plan_date):
    """Join the pantry's cooked leftovers to plan-day availability (M1.8).

    Each cooked entry becomes ``{component, grams, days, cooked}`` where
    ``days`` are the plan days its residual life covers under the one strict
    convention (PRD §8.1): available on day d iff
    ``0 <= (plan_date + d) - cooked_date < keeps_days``. An entry whose
    window is empty (already past its life at plan start) is dropped with a
    structured ``leftover_expired`` warning. Entries are sorted (component,
    cook date) so session_plan eats the OLDEST leftover first.

    Consumers: engine.build_week (availability join) and session_plan
    (grams consumed before fresh batches — leftovers are already paid for).
    """
    days = settings["days"]
    entries, warnings = [], []
    for r in (pantry.get("cooked") or []):
        cid = r["component"]
        keeps = comps[cid]["keeps_days"]
        age0 = (plan_date - _as_date(r["cooked"])).days
        window = [d for d in range(days) if 0 <= age0 + d < keeps]
        if not window:
            warnings.append(dict(
                code="leftover_expired", component=cid, grams=r["grams"],
                cooked=_as_date(r["cooked"]).isoformat(), age_days=age0,
                keeps_days=keeps,
                message=(f"cooked leftover of '{cid}' ({r['grams']}g, "
                         f"cooked {_as_date(r['cooked']).isoformat()}, "
                         f"keeps {keeps}d) is past its life at plan start "
                         "— ignored")))
            continue
        entries.append(dict(component=cid, grams=r["grams"], days=window,
                            cooked=_as_date(r["cooked"]).isoformat()))
    entries.sort(key=lambda e: (e["component"], e["cooked"]))
    return entries, warnings


def session_plan(comps, ing, settings, weeks, leftovers=None):
    """THE canonical session attribution (PRD §8.2, P10).

    Each (component, day) gram demand from ``weeks`` ({person: [{cid: g}
    per day]}) is fed by the EARLIEST cook session that (a) can cook the
    component under the raw-freshness rule and (b) whose batch is still
    within cooked shelf life on that day (economy over freshness;
    provisional). Per-session batches = ceil(session demand / yield_g);
    per-session minutes pay the full active_min for each session's first
    batch and batch_time_factor for marginal ones.

    Returns::

        {"sessions": [{"index", "start", "demand_g", "batches", "made_g",
                       "minutes", "thaw_notes",
                       "feeds": [{component, day, grams}...]}...],
         "batches": {cid: summed batches},   # feeds purchase() / menu_cost()
         "minutes": total,                   # == sum of session minutes
         "unattributed": [{component, day, grams}...],  # demand no session
                                                        # can feed (reported,
                                                        # never invented)
         "leftover": [{component, day, grams, note}...]}  # demand fed from
                                                          # cooked leftovers

    Cooked leftovers (M1.8): pass ``leftovers`` (costing.cooked_leftovers
    entries) and each (component, day) demand is fed FIRST from any leftover
    still within residual life on that day — the economy-over-freshness rule
    extended: leftovers are already paid for, eat them before cooking a
    fresh batch. Only the remainder is attributed to cook sessions.

    ``feeds`` (M1.10): each session records the day-level attribution rows
    it was assigned — {component, day, grams} sorted by (component, day).
    This is the SAME attribution loop, exposed (never re-derived, P10): the
    portioning matrix on the cook plan is a pure reshape of MealDay x these
    rows (M19_SPEC §1). Freezer-bridged demand appears in the feeding
    session's feeds too (it is cooked there); the matrix marks those rows
    pack-at-thaw via the session's freezer_notes (M19_SPEC §11.1).
    """
    ss = sessions_for(settings)
    day_demand = {}
    for wk in weeks.values():
        for d, pl in enumerate(wk):
            for cid, g in (pl or {}).items():
                day_demand[(cid, d)] = day_demand.get((cid, d), 0) + g
    cookable = {cid: set(cookable_sessions(comps[cid], settings, ing))
                for cid in {c for c, _ in day_demand}}
    sess_demand = [{} for _ in ss]
    sess_feeds = [[] for _ in ss]        # M1.10: day-level attribution rows
    unattributed, freezer_serves = [], []
    remaining = [dict(e) for e in (leftovers or [])]   # never mutate inputs
    leftover_served = []
    for (cid, d), g in sorted(day_demand.items()):
        # M1.8: leftovers first (oldest first — cooked_leftovers sorts them)
        for e in remaining:
            if g <= 0:
                break
            if (e["component"] != cid or d not in e["days"]
                    or e["grams"] <= 0):
                continue
            take = min(e["grams"], g)
            e["grams"] -= take
            g -= take
            leftover_served.append(dict(
                component=cid, day=d, grams=take,
                note=(f"'{cid}' day {d}: {take}g from leftover "
                      f"(cooked {e['cooked']}) — leftovers are already "
                      "paid for, eat them first")))
        if g <= 0:
            continue
        k_fit = None
        for k, start in enumerate(ss):
            if (k in cookable[cid] and start <= d
                    and d - start < comps[cid]["keeps_days"]):
                k_fit = k
                break                    # earliest wins — economy over freshness
        if k_fit is None and freezer_bridges(comps[cid], settings):
            # M0.5: no session's batch survives to day d on the shelf, but the
            # component freezes — the earliest cookable session feeds it from
            # the freezer, and the plan says to thaw ahead.
            for k, start in enumerate(ss):
                if k in cookable[cid] and start <= d:
                    k_fit = k
                    freezer_serves.append(dict(
                        component=cid, day=d, grams=g, session=k,
                        note=(f"'{cid}' day {d}: {g}g from freezer — thaw "
                              f"ahead (cooked in session {k}, day {start})")))
                    break
        if k_fit is None:
            unattributed.append(dict(component=cid, day=d, grams=g))
        else:
            sess_demand[k_fit][cid] = sess_demand[k_fit].get(cid, 0) + g
            sess_feeds[k_fit].append(dict(component=cid, day=d, grams=g))
    f = settings["batch_time_factor"]
    sessions, total_batches = [], {}
    for k, start in enumerate(ss):
        batches = {cid: math.ceil(g / comps[cid]["yield_g"] - 1e-9)
                   for cid, g in sess_demand[k].items() if g > 0}
        minutes = round(sum(comps[cid]["active_min"] * (1 + f * (b - 1))
                            for cid, b in batches.items() if b > 0))
        thaw = []
        if ing is not None:
            for cid in sorted(batches):
                _, _, frozen = raw_freshness(comps[cid], start, settings, ing)
                for fr in frozen:
                    thaw.append(dict(
                        component=cid, ingredient=fr["ingredient"],
                        shop_day=fr["shop_day"], session=k,
                        note=(f"'{fr['ingredient']}' for '{cid}': frozen on "
                              f"day {fr['shop_day']}, thaw before session "
                              f"{k} (day {start})")))
        sessions.append(dict(
            index=k, start=start, demand_g=sess_demand[k], batches=batches,
            made_g={cid: b * comps[cid]["yield_g"]
                    for cid, b in batches.items()},
            minutes=minutes, thaw_notes=thaw,
            freezer_notes=[fs for fs in freezer_serves
                           if fs["session"] == k],
            feeds=sorted(sess_feeds[k],
                         key=lambda r: (r["component"], r["day"]))))
        for cid, b in batches.items():
            total_batches[cid] = total_batches.get(cid, 0) + b
    return dict(sessions=sessions, batches=total_batches,
                minutes=sum(s["minutes"] for s in sessions),
                unattributed=unattributed, freezer=freezer_serves,
                leftover=leftover_served)


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


def purchase(comps, ing, chosen, batches=None, pantry=None):
    """Ingredient demand -> pantry deduction -> whole purchase units ->
    leftover grams.

    M0.12 (PRD §8.1): ``pantry`` (a model.Pantry or the raw pantry mapping,
    optional) has its stock grams deducted from ingredient need BEFORE
    rounding to packs, floored at zero. A missing or empty pantry is a
    no-op — results are identical to no pantry at all. Cooked leftovers are
    not consumed here — they feed session_plan via cooked_leftovers (M1.8).

    Stock aging (M1.8, PRD §8.1): callers with a plan date apply
    ``age_pantry`` FIRST and pass its effective pantry here — stock too old
    to survive to any cook session is then never deducted (and is reported
    as expiring unused). This function itself deducts exactly the stock it
    is given."""
    batches = batches or {i: 1 for i in chosen}
    need = {}
    for i in chosen:
        for name, grams in comps[i]["ingredients"].items():
            need[name] = need.get(name, 0) + grams * batches[i]
    if pantry is not None:
        stock = {}
        for r in (pantry.get("stock") or []):
            stock[r["ingredient"]] = stock.get(r["ingredient"], 0) + r["grams"]
        for name in need:
            if name in stock:
                need[name] = max(0, need[name] - stock[name])
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
    f = settings["batch_time_factor"]
    return round(sum(comps[i]["active_min"] * (1 + f * (b - 1))
                     for i, b in batches.items() if b > 0))


def estimate_batches(comps, people, settings, chosen):
    """How many batches will a week actually need? Scale by total calorie demand
    vs. what one batch of the whole menu delivers. Crude, but cheap and monotone —
    good enough to make a budget ceiling bind during the search. The real batch
    counts come out of build_week."""
    days = settings["days"]
    need = sum(kcal_of(p["targets"]) for p in people.values()) * days
    per_set = sum(comps[i]["yield_g"] * comps[i]["per100"]["kcal"] / 100 for i in chosen)
    if per_set <= 0:
        return {i: 1 for i in chosen}
    k = max(1, math.ceil(need / per_set))
    return {i: k for i in chosen}


def menu_cost(comps, ing, chosen, batches=None, people=None, settings=None,
              pantry=None):
    if batches is None and people and settings:
        batches = estimate_batches(comps, people, settings, chosen)
    rows, _, _ = purchase(comps, ing, chosen, batches, pantry=pantry)
    return sum(u * ing[n]["cost"] for n, g, u, pk, l, per, k in rows)


def budget_ceiling(settings, people):
    """Weekly grocery ceiling from the budget doc, or None for no ceiling.

    'by_consumption' deliberately has NO ceiling (like 'off'): it is the
    attribution-only mode — whatever the shop costs splits by consumption
    share, which ``attribute`` computes and render applies for every mode."""
    b = settings["budget"] or {"mode": "off"}
    if b.get("mode") == "shared":
        return b.get("total")
    if b.get("mode") == "per_person":
        pp = b.get("per_person") or {}
        return sum(pp.get(pn, 0) for pn in people) or None
    return None
