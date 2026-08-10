"""dishes.py — M1.13 THE DISH LAYER (M113_SPEC, judge-panel-ratified).

The owner's correction is the requirement, verbatim: "These aren't meals —
they're random ingredients served together… how is that following an actual
recipe I gave you?" A meal is ONE DISH, portioned within its per-serving
ratio bands, plus compatible sides — never a free mix of the week's pool
(PRD §4.0 supersession clause).

Architecture (skeleton-then-solve): a deterministic, RNG-free, zero-LP
SKELETON picks WHICH dish fills each resolved slot (§4); ONE dish-blocked
LP per person-day with a continuous servings scalar ``t`` picks HOW MUCH
(§5). Dish ratio bands and per-meal serve caps are HARD LP bounds — dish
identity is structural, never scored (§6). Day macro tolerance stays soft
and slack-dominant: the failure mode inverts vs the M1.9 dealer exactly as
ordered — coherent dishes whose macro bands may miss, flagged with
receipts, never in-band days of incoherent meals.

Mode key: the PRESENCE of dishes.yaml in the library. Absent → this whole
module is dormant and the pipeline is byte-identical heritage (M1.9 dealer,
diverse_plates, deal_day all untouched). Downstream of ``demand[c]`` —
batching, purchase, costing, session attribution, freezer bridging, the
portioning matrix — never learns what a dish is: components remain the
batch units, and shared components consolidate across dishes automatically.

ESCALATION (up — M113_SPEC §13, the M19 §10 house pattern, spec not
folklore): the dish-binary day MILP (P1's formulation, design work done).
Per person-day: anchor binaries ``y[j,s]`` per (dish, slot) with
``Σ_j y[j,s] = 1`` plus hole-slack; per-instance servings ``t[j,s] ≤
T_MAX_j · y[j,s]``; linear band coupling ``min_g[c,j]·t[j,s] ≤ x[c,j,s] ≤
max_g[c,j]·t[j,s]``; week-variety ``used_days²`` entering as linear costs
on ``y``; seeded epsilon tie-breaks on all binaries. Dish SELECTION moves
from the skeleton into CBC behind the same ``plate_dishes`` seam — callers
never change. CRITERION (data-forced, never vibes): an M1.6-class real
week shows skeleton ASSIGN_RETRIES exhaustion or ``no_dish_assignable`` on
more than 5% of person-days, or ``dish_band_binding``/``band_unmet`` above
``BAND_ESCALATION_THRESHOLD`` (MEAL_WEIGHTS, 0.20) — the decision is
forced onto the owner's desk with flag counts as receipts.

DOWNGRADE (down): the dish-coupled continuous-``s`` pool LP (P2's hatch) —
one ``s_d`` servings variable per dish per day, linear member coupling
``x[c] = Σ_d s_d · base_g[c,d]`` with band slack, NO new binaries — if
``plate-dish`` blows a named BASELINES.md multiple or CBC shows non-optimal
terminations on real weeks. Same seam, same flags.
"""

import hashlib
import math

import pulp

from .engine import (SCORE_WEIGHTS, _count_solve, available_on,
                     effective_serve_bounds, eligible, solve_stage)
from .meals import MEAL_WEIGHTS
from .model import resolve_meal_slots
from .units import MACROS, kcal_of

# --------------------------------------------------------------------------- #
#  DISH_WEIGHTS registry (M113_SPEC §11) — mirrors SCORE_WEIGHTS/MEAL_WEIGHTS:
#  named, documented, override-mergeable; EVERY value provisional (P9),
#  ratified (or not) against the M1.6 real week.
# --------------------------------------------------------------------------- #
DISH_WEIGHTS = {
    # skeleton retry ladder: next-ranked assignment for the binding slot
    "ASSIGN_RETRIES": 3,                 # provisional (P9)
    # sides nominated per slot from the dish's compatible_sides
    "SIDE_CANDIDATES": 3,                # provisional (P9)
    # nonzero sides per slot in the LP (semicontinuous cardinality)
    "SIDES_PER_SLOT_MAX": 2,             # provisional (P9)
    # slotless people: implicit day-block dish count cap
    "DISHES_PER_DAY": 3,                 # provisional (P9)
    # continuous servings scalar lower bound (below this a plate stops
    # reading as the dish)
    "DISH_T_MIN": 0.5,                   # provisional (P9)
    # dish_band_loose lint threshold: worst-case pairwise ratio error
    "DISH_BAND_LOOSE_RATIO": 3.0,        # provisional (P9)
    # ---- skeleton assignment score (min wins; ties → dish id asc) ----
    "variety_used_days": 100,            # provisional (P9): × used_days²
    "same_day_repeat": 400,              # provisional (P9): per repeat today
    "affinity_mismatch": 250,            # provisional (P9)
    "capacity_fit_per_kcal": 1.0,        # provisional (P9): per kcal outside
    "batch_coherence": 40,               # provisional (P9): per shared member
    "dislike_core": 150,                 # provisional (P9): per disliked core
    # ---- plate_dishes LP objective (registry hierarchy: day slack ≫
    #      meal-band slack ≫ gram tie-break ≫ seeded epsilon) ----
    "meal_band_slack": 100,              # provisional (P9): strictly below
    #                                      SCORE_WEIGHTS plate_slack_dominance
    "gram_tiebreak": 0.001,              # provisional (P9)
    "binary_epsilon": 1e-4,              # provisional (P9): seeded tie-break
    # ---- menu-score merges (SCORE_WEIGHTS style, M113_SPEC §3) ----
    "slot_coverage": 3000,               # provisional (P9)
    "slot_capacity_unmet": 5000,         # provisional (P9)
}

_RESERVED_TRIO = ("breakfast", "lunch", "dinner")


def _weights(config=None):
    w = dict(DISH_WEIGHTS)
    w.update(config or {})
    return w


# --------------------------------------------------------------------------- #
#  pure dish arithmetic (zero-LP): membership, availability, eligibility,
#  closure, achievable ranges
# --------------------------------------------------------------------------- #
def core_members(dish):
    """CORE member = components entry with min_g > 0 (M113_SPEC §2). A
    member authored min_g 0 is droppable per person — same dish without
    it; the draft schema authors exactly the accents that way."""
    return [cid for cid, b in dish["components"].items() if b["min_g"] > 0]


def optional_members(dish):
    """min_g == 0 members that are NOT accents (droppable, but plated by
    default — none in the current draft; supported for future authoring)."""
    acc = set(dish["accents"])
    return [cid for cid, b in dish["components"].items()
            if b["min_g"] == 0 and cid not in acc]


def dish_cuisine(dish, comps):
    """Authored ``cuisine:`` wins (optional additive key, consumed if
    present — M113_SPEC §2); else derived from the main-role member, else
    the first core member."""
    if dish.get("cuisine"):
        return dish["cuisine"]
    for cid in dish["components"]:
        if cid in comps and comps[cid]["role"] == "main":
            return comps[cid]["cuisine"]
    for cid in core_members(dish):
        if cid in comps:
            return comps[cid]["cuisine"]
    return None


def dish_available_on(dish, comps, day, settings, ing=None, extra_days=None):
    """A dish is available on ``day`` iff EVERY core member is (§2
    conjunction; freezer bridging applies per component via available_on).
    Returns ``(ok, killer)`` — the dish dies with its shortest-lived core
    member, and the killer is named (dish_unavailable payload)."""
    for cid in core_members(dish):
        if cid not in comps:
            return False, cid
        if available_on(comps[cid], day, settings, ing):
            continue
        if extra_days and day in extra_days.get(cid, ()):
            continue                      # cooked leftovers reach this day
        return False, cid
    return True, None


def dish_eligible(dish, comps, person):
    """Eligible iff no CORE member carries a tag the person excludes. An
    excluded accent or min_g-0 member drops that member only (dish intact);
    an excluded core member kills the dish. Returns ``(ok, (cid, tag))``."""
    excl = set(person.get("exclude") or [])
    for cid in core_members(dish):
        if cid not in comps:
            return False, (cid, None)
        hit = set(comps[cid]["tags"]) & excl
        if hit:
            return False, (cid, sorted(hit)[0])
    return True, None


def eligible_accents(dish, comps, person):
    """The dish's accents this person can have (excluded ones drop —
    the eater's 'no queso' is a dropped accent, dish intact)."""
    return [a for a in dish["accents"]
            if a in comps and eligible(comps[a], person)]


def closure(dishes, chosen):
    """Component closure of a dish menu (M113_SPEC §3): union of the
    chosen dishes' members ∪ accents ∪ compatible_sides — what everything
    cost/time/batch-shaped runs on, unchanged."""
    out = set()
    for j in chosen:
        d = dishes[j]
        out |= set(d["components"]) | set(d["accents"])
        out |= set(d["compatible_sides"])
    return sorted(out)


def orphan_sides(dishes, comps):
    """starch/veg referenced by NO dish's compatible_sides (M113_SPEC §9).
    dish_layer=permissive lets them serve as sides of any dish, flagged
    orphan_side per use; strict makes them unservable."""
    used = set()
    for d in dishes.values():
        used |= set(d["compatible_sides"])
    return sorted(cid for cid, c in comps.items()
                  if c["role"] in ("starch", "veg") and cid not in used)


def t_max(dish, comps, person):
    """The servings-scalar ceiling: ``min over core c of serve_max(c) /
    base_g(c)`` — the authored palatability absolutes cap the scale-up;
    bounds never scale by appetite (the lard-beans ruling, SCALING
    dormant)."""
    tm = math.inf
    for cid in core_members(dish):
        hi = effective_serve_bounds(comps[cid], person)[1]
        tm = min(tm, hi / dish["components"][cid]["base_g"])
    return tm


def t_max_limiter(dish, comps, person):
    """WHICH core member's serve cap sets T_MAX (the receipt in
    dish_band_binding: 'carnitas at 180g/serving max')."""
    best, best_cid = math.inf, None
    for cid in core_members(dish):
        hi = effective_serve_bounds(comps[cid], person)[1]
        r = hi / dish["components"][cid]["base_g"]
        if r < best:
            best, best_cid = r, cid
    return best_cid


def dish_kcal_range(dish, comps, person, config=None):
    """Per-serving-scaled achievable kcal range — the skeleton's ONLY macro
    arithmetic (§4): ``[T_MIN·Σ_core min_g·kcal/100 …
    T_MAX·Σ_members min(max_g·T_MAX, serve_max)·kcal/100]``."""
    W = _weights(config)
    tm = t_max(dish, comps, person)
    lo = sum(dish["components"][cid]["min_g"] * comps[cid]["per100"]["kcal"]
             / 100 for cid in core_members(dish)) * W["DISH_T_MIN"]
    hi = 0.0
    for cid, b in dish["components"].items():
        if cid not in comps or not eligible(comps[cid], person):
            continue
        smax = effective_serve_bounds(comps[cid], person)[1]
        hi += min(b["max_g"] * tm, smax) * comps[cid]["per100"]["kcal"] / 100
    return lo, hi


def dish_macro_ratio_max(dish, comps, person):
    """Max achievable protein/fat ratio over the band box (M113_SPEC §3
    lean anchors — P3's aggregate test, both judges): a linear-fractional
    objective over a box attains its max at a vertex, so evaluate the
    ``{min_g, max_g}`` vertices exactly (≤ 2^members, members ≤ ~6). A
    composite fat-heavy dish (turkey_meatballs_al_sugo — integration note
    3) is NOT lean, whatever its main's anchor flag says."""
    members = [(cid, b) for cid, b in dish["components"].items()
               if cid in comps and eligible(comps[cid], person)]
    if not members:
        return 0.0
    best = 0.0
    for mask in range(2 ** len(members)):
        p = f = 0.0
        for k, (cid, b) in enumerate(members):
            g = b["max_g"] if (mask >> k) & 1 else b["min_g"]
            p += g * comps[cid]["per100"]["protein"] / 100
            f += g * comps[cid]["per100"]["fat"] / 100
        if p <= 0:
            continue
        best = max(best, p / max(f, 0.1))
    return best


def slot_max_dishes(person, slot_name):
    """§7 rung 3 config: per-slot ``max_dishes_per_slot`` override, else
    the person's, else 1 (two dishes in one slot is explicit opt-in,
    never automatic)."""
    for s in (person.get("meal_slots") or []):
        if s.get("name") == slot_name and s.get("max_dishes_per_slot"):
            return s["max_dishes_per_slot"]
    return person.get("max_dishes_per_slot") or 1


# --------------------------------------------------------------------------- #
#  --implicit-dishes synthesis (M113_SPEC §9 — the steward's reconstruction
#  window shim; every synthesized dish is flagged so the real work stays
#  measurable against it)
# --------------------------------------------------------------------------- #
def implicit_dishes(comps, existing=None):
    """One synthesized dish per legacy main not already in a dish: the main
    + its pairs_with accents as ``accents``; all starch/veg as
    ``compatible_sides``; min/base/max lifted from serve_g min/midpoint/max
    (midpoint snapped to the unit grid for discrete mains). Returns
    ``({dish_id: dish_dict}, [implicit_dish warnings])``."""
    existing = existing or {}
    covered = set()
    for d in existing.values():
        covered |= set(d["components"])
    sides = sorted(cid for cid, c in comps.items()
                   if c["role"] in ("starch", "veg"))
    out, warns = {}, []
    for cid in sorted(comps):
        c = comps[cid]
        if c["role"] != "main" or cid in covered:
            continue
        lo, hi = c["serve_g"]["min"], c["serve_g"]["max"]
        base = (lo + hi) / 2
        u = c.get("unit_g")
        if u:
            base = max(u, round(base / u) * u)
        base = int(round(base))
        members = {cid: dict(base_g=base, min_g=lo, max_g=hi)}
        accents = []
        for aid in sorted(comps):
            if comps[aid]["role"] != "accent":
                continue
            if cid in (comps[aid].get("pairs_with") or []):
                accents.append(aid)
                alo, ahi = (comps[aid]["serve_g"]["min"],
                            comps[aid]["serve_g"]["max"])
                members[aid] = dict(base_g=int(round((alo + ahi) / 2)),
                                    min_g=0, max_g=ahi)
        did = f"{cid}_implicit"
        out[did] = dict(
            id=did, name=f"{c['name']} (implicit dish)",
            source=f"synthesized from component '{cid}' (--implicit-dishes)",
            reconstruction="inferred", components=members, accents=accents,
            compatible_sides=[s for s in sides if s not in members],
            meal_affinity=[], cuisine=c["cuisine"], notes=None)
        warns.append(dict(
            code="implicit_dish", dish=did, source_main=cid,
            message=(f"dish '{did}' was SYNTHESIZED from main '{cid}' "
                     "(--implicit-dishes): bands lifted from serve_g "
                     "min/midpoint/max — the steward's real reconstruction "
                     "replaces this shim")))
    return out, warns


# --------------------------------------------------------------------------- #
#  §4 the skeleton — deterministic dish assignment (zero-LP, RNG-free,
#  dealer-class discipline: every ordering an explicit sort key)
# --------------------------------------------------------------------------- #
def _recognized_labels(dishes):
    """Slot names for which affinity is ACTIVE: the reserved trio plus any
    label a dish actually authors. Anything else → affinity inert
    (M19 §11.2: breakfast semantics are never guessed)."""
    labels = set(_RESERVED_TRIO)
    for d in dishes.values():
        labels |= set(d["meal_affinity"])
    labels.discard("any")
    return labels


def _affinity_mismatch(dish, slot_name, labels):
    aff = dish["meal_affinity"]
    if not aff or "any" in aff or slot_name not in labels:
        return False                     # inert: no authored affinity, or
    return slot_name not in aff          # unrecognized slot name


def _assign_score(dish_id, dish, comps, person, slot_name, slot_kcal,
                  labels, used_days, today_count, week_demand, W):
    """Skeleton assignment score (§4.2) — min wins; ties → dish id asc."""
    s = W["variety_used_days"] * (used_days.get(dish_id, 0) ** 2)
    s += W["same_day_repeat"] * today_count
    if _affinity_mismatch(dish, slot_name, labels):
        s += W["affinity_mismatch"]
    lo, hi = dish_kcal_range(dish, comps, person)
    if slot_kcal < lo:
        s += W["capacity_fit_per_kcal"] * (lo - slot_kcal)
    elif slot_kcal > hi:
        s += W["capacity_fit_per_kcal"] * (slot_kcal - hi)
    if week_demand:
        shared = sum(1 for cid in dish["components"] if cid in week_demand)
        s -= W["batch_coherence"] * shared
    dislikes = set(person.get("dislikes") or [])
    s += W["dislike_core"] * sum(1 for cid in core_members(dish)
                                 if cid in dislikes)
    return s


def _side_candidates(dish, dishes, comps, person, day, settings, ing,
                     extra_days, used_days, menu_closure, W):
    """§4.3: nominate ≤ SIDE_CANDIDATES sides. Compatibility is AUTHORED
    data only — inference would resurrect free mixing with extra steps.
    dish_layer=permissive additionally admits orphan starch/veg from the
    menu closure, each use flagged orphan_side (§9). Ranked by predicted
    deficit-macro density desc, then used_days, then id asc."""
    pool = []
    orphan_pool = set()
    for sid in dish["compatible_sides"]:
        if sid in comps:
            pool.append(sid)
    if (settings.get("dish_layer") or "permissive") == "permissive":
        for sid in orphan_sides(dishes, comps):
            if (sid in menu_closure and sid not in pool
                    and sid not in dish["components"]):
                # never the dish's OWN member: a member+side pair would give
                # one component two LP variables in the slot, letting the
                # summed grams break the hard band and serve cap (§5/§6)
                pool.append(sid)
                orphan_pool.add(sid)
    avail = []
    for sid in pool:
        if not eligible(comps[sid], person):
            continue
        if not (available_on(comps[sid], day, settings, ing)
                or (extra_days and day in extra_days.get(sid, ()))):
            continue
        avail.append(sid)
    # predicted deficit macro: the slot-share macro the dish's baseline
    # covers worst — sides are the balancing fluid (§5)
    n_slots = 1
    base = {m: sum(b["base_g"] * comps[cid]["per100"][m] / 100
                   for cid, b in dish["components"].items() if cid in comps)
            for m in MACROS}
    share = {m: person["targets"][m] / n_slots for m in MACROS}
    deficit_macro = max(MACROS,
                        key=lambda m: (share[m] - base[m]) / max(share[m], 1))
    ranked = sorted(avail, key=lambda sid: (
        -comps[sid]["per100"][deficit_macro],
        used_days.get(sid, 0), sid))
    take = ranked[:W["SIDE_CANDIDATES"]]
    return take, {s for s in take if s in orphan_pool}


def skeleton_day(person, dishes, comps, settings, day, slots, menu,
                 *, ing=None, extra_days=None, used_days=None,
                 week_demand=None, rank_bump=None, veto=None, config=None):
    """Assign one dish (+ side candidates + accents) to each slot of one
    person-day — zero-LP, RNG-free, deterministic (two calls with the same
    inputs are byte-identical). Returns ``(assignments, notes)``:
    assignments[k] = ``{slot, dishes: [ids], sides, orphan_sides, accents,
    blockers}``; a slot with no assignable dish carries ``dishes: []`` and
    the per-dish blockers (the no_dish_assignable payload).

    ``rank_bump`` is the §4 retry ladder's lever: {slot_index: r} takes the
    (r+1)-th ranked dish for that slot on retry. ``veto`` (replate, §8)
    removes dishes for this day only. The engine relax-tier ladder concept
    maps here to the repeat cap (``ceil(n/|D|)``) declared up front —
    used_days pressure is quadratic, never a hard cap, so there is no
    tier to widen before retries burn."""
    W = _weights(config)
    rank_bump = rank_bump or {}
    used_days = used_days or {}
    veto = set(veto or ())
    labels = _recognized_labels(dishes)
    menu_closure = set(closure(dishes, [j for j in menu if j in dishes]))
    notes = []

    # candidate set: available on d ∧ eligible for p ∧ not vetoed
    cands, blockers = [], {}
    for j in menu:
        if j not in dishes or j in veto:
            continue
        d = dishes[j]
        ok, killer = dish_available_on(d, comps, day, settings, ing,
                                       extra_days)
        if not ok:
            blockers[j] = dict(code="dish_unavailable", dish=j, day=day,
                               component=killer)
            continue
        ok, hit = dish_eligible(d, comps, person)
        if not ok:
            blockers[j] = dict(code="dish_excluded_by",
                               person=person.get("name"), dish=j,
                               component=hit[0], tag=hit[1])
            continue
        cands.append(j)

    n = len(slots)
    repeat_cap = 1
    if cands and len(cands) < n:
        repeat_cap = -(-n // len(cands))          # ceil(n/|D|)
        notes.append(dict(code="variety_unmet", day=day,
                          dishes_available=len(cands), slots_required=n,
                          repeat_cap=repeat_cap, message=(
                              f"{len(cands)} dish(es) for {n} slot(s) — "
                              f"dishes repeat across up to {repeat_cap} "
                              "slot(s)")))

    day_kcal = kcal_of(person["targets"])
    today = {}
    assignments = []
    for k, slot in enumerate(slots):
        slot_kcal = day_kcal / max(n, 1)
        ranked = sorted(
            (j for j in cands
             if today.get(j, 0) < repeat_cap),
            key=lambda j: (_assign_score(
                j, dishes[j], comps, person, slot["name"], slot_kcal,
                labels, used_days, today.get(j, 0), week_demand, W), j))
        entry = dict(slot=slot, dishes=[], sides=[], orphan_sides=set(),
                     accents=[], blockers=blockers)
        if ranked:
            pick = ranked[min(rank_bump.get(k, 0), len(ranked) - 1)]
            entry["dishes"] = [pick]
            today[pick] = today.get(pick, 0) + 1
            sides, orph = _side_candidates(
                dishes[pick], dishes, comps, person, day, settings, ing,
                extra_days, used_days, menu_closure, W)
            entry["sides"] = sides
            entry["orphan_sides"] = orph
            # accents are day-filtered like everything served: an expired
            # accent cannot be plated (and would strand session attribution)
            entry["accents"] = [
                a for a in eligible_accents(dishes[pick], comps, person)
                if available_on(comps[a], day, settings, ing)
                or (extra_days and day in extra_days.get(a, ()))]
            # §7 rung 3: second dish, explicit opt-in only, capacity-forced
            if slot_max_dishes(person, slot["name"]) >= 2:
                lo, hi = dish_kcal_range(dishes[pick], comps, person, W)
                side_kcal = sum(
                    effective_serve_bounds(comps[s], person)[1]
                    * comps[s]["per100"]["kcal"] / 100 for s in sides)
                if hi + side_kcal < slot_kcal:
                    cuisine = dish_cuisine(dishes[pick], comps)
                    second = [j for j in ranked if j != pick
                              and dish_cuisine(dishes[j], comps) == cuisine]
                    second = second or [j for j in ranked if j != pick]
                    if second:
                        j2 = second[0]
                        entry["dishes"].append(j2)
                        today[j2] = today.get(j2, 0) + 1
                        # never the second dish's OWN member as a slot
                        # side: a member+side pair would give one
                        # component two LP variables in the slot, letting
                        # the summed grams break the hard band and serve
                        # cap (§5/§6 — the same guard orphan sides carry
                        # against the first dish above)
                        entry["sides"] = [
                            s for s in entry["sides"]
                            if s not in dishes[j2]["components"]]
                        entry["orphan_sides"] &= set(entry["sides"])
                        entry["accents"] = sorted(
                            set(entry["accents"])
                            | {a for a in eligible_accents(
                                dishes[j2], comps, person)
                               if available_on(comps[a], day, settings,
                                               ing)
                               or (extra_days
                                   and day in extra_days.get(a, ()))})
                        notes.append(dict(
                            code="second_dish_added", slot=slot["name"],
                            day=day, dishes=list(entry["dishes"]),
                            kcal_gap=round(slot_kcal - (hi + side_kcal), 1),
                            message=(f"'{slot['name']}' cannot reach its "
                                     f"{slot_kcal:.0f} kcal share from one "
                                     "dish + sides — second dish "
                                     f"'{j2}' added (max_dishes_per_slot "
                                     "opt-in); both carry full band "
                                     "integrity")))
        assignments.append(entry)
    return assignments, notes


# --------------------------------------------------------------------------- #
#  §5 the dish-blocked LP — plate_dishes() (one LP per person-day + the
#  standard discrete-snap re-solve; stage "plate-dish")
# --------------------------------------------------------------------------- #
def _eps_of(seed, cid, slot_name):
    """Deterministic epsilon per (component, slot) — hash of the explicit
    seed (the M19 §10 device): symmetric side/accent choices can't flap on
    CBC tie-breaking. hashlib only — the builtin hash function is
    PYTHONHASHSEED-randomized and banned by the determinism scan."""
    h = hashlib.sha256(f"{seed}:{cid}:{slot_name}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def plate_dishes(person, comps, dishes, assignments, settings, *, seed=0,
                 config=None, locked=None, meal_bands=True):
    """Portion one person-day within the skeleton's FIXED dish assignment —
    dish selection carries no binaries; only sides/accents are
    semicontinuous. Two-pass discrete snapping mirrors plate(): pass 1
    solves; pass 2 fixes binaries, snaps unit_g grams to whole units
    clamped into ``[t·min_g, t·max_g] ∩ serve caps`` (t stays free so the
    band flexes around the snapped grid point), re-solves the rest.
    Snapping can shift grams within a dish but can never change WHICH dish.

    Hard (never slack): dish ratio bands ``min_g·t ≤ x ≤ max_g·t``, the
    per-MEAL serve caps ``x ≤ effective_serve_bounds.max`` (serve_g's
    per-serving semantics land where authored — retiring the M19 §3.3
    sub_serving workaround at its root for dish mode), accent/side
    semicontinuity, ``max_daily_mass_g``. Soft: day macro tolerance
    (dominant slack — today's plate() contract verbatim), then per-slot
    macro bands at ``T_m/n ± MEAL_BAND``.

    ``locked`` (§8 replate): ``{(slot_name, cid): grams}`` (slot-qualified
    pins are natural here — the variable IS x[c,s]) or ``{cid: grams}``
    (applied where the component appears). Pins clamp into dish bands ∩
    serve caps, warned when adjusted.

    Returns ``dict(status, meals, miss, day_flags, warnings,
    binding_slot)`` — status ``ok`` | ``miss`` (day shipped, signed misses
    reported, exit-2 semantics unchanged) | ``infeasible`` (hard set empty
    → skeleton retry, §4)."""
    W = _weights(config)
    tol = person["tolerance"]
    targets = person["targets"]
    n = len(assignments)
    warnings = []

    # ---- shape the variables once (shared by both passes) ------------------
    # inst[i] = (k, dish_id, dish); member x-vars keyed (i, cid);
    # accent/side y-vars + binaries keyed (k, cid)
    inst = []
    for k, a in enumerate(assignments):
        for j in a["dishes"]:
            inst.append((k, j, dishes[j]))
    if not inst:
        return dict(status="infeasible", meals=[], miss={}, day_flags=[],
                    warnings=warnings, binding_slot=None)

    serve = {}
    for k, a in enumerate(assignments):
        ids = set(a["sides"]) | set(a["accents"])
        for i, (kk, j, d) in enumerate(inst):
            if kk == k:
                ids |= set(d["components"])
        for cid in ids:
            if cid in comps and cid not in serve:
                serve[cid] = effective_serve_bounds(comps[cid], person)[:2]

    dislikes = set(person.get("dislikes") or [])
    floor_g = MEAL_WEIGHTS["MIN_SUBPORTION_G"]

    def build(fixed_x=None, fixed_b=None):
        """One LP. ``fixed_x``: {(kind, key): grams} frozen grams;
        ``fixed_b``: {(kind, key): 0|1} frozen binaries (pass 2)."""
        fixed_x = fixed_x or {}
        fixed_b = fixed_b or {}
        m = pulp.LpProblem("plate_dishes", pulp.LpMinimize)
        t, x, y, b = {}, {}, {}, {}
        # (slot, cid) → every gram term (vars AND frozen numerics) of the
        # component in that slot — dish-instance members, accents, sides.
        # The per-MEAL serve cap is per SLOT-COMPONENT (§5 'per slot',
        # §6 rung 1): one cap over the SUM, so two opted-in dishes (§7
        # rung 3) sharing a member cannot ship 2x the authored absolute.
        slot_comp = {}
        for i, (k, j, d) in enumerate(inst):
            tm = t_max(d, comps, person)
            tm = max(tm, W["DISH_T_MIN"])       # validated non-empty band
            t[i] = pulp.LpVariable(f"t_{i}", W["DISH_T_MIN"], tm)
            excl = set(person.get("exclude") or [])
            for cid, band in d["components"].items():
                if cid not in comps:
                    continue
                if cid in d["accents"]:
                    continue                     # accents handled below
                if not eligible(comps[cid], person):
                    if band["min_g"] > 0:
                        # validated upstream (dish_eligible); belt+braces
                        return None
                    continue                     # dropped optional member
                key = ("x", i, cid)
                hi_cap = serve[cid][1]
                if key in fixed_x:
                    v = fixed_x[key]
                else:
                    v = pulp.LpVariable(f"x_{i}_{cid}", 0, hi_cap)
                # THE dish band — hard, linear (t continuous, band consts)
                m += v >= band["min_g"] * t[i]
                m += v <= band["max_g"] * t[i]
                slot_comp.setdefault((k, cid), []).append(v)
                x[key] = v
        for k, a in enumerate(assignments):
            slot_name = a["slot"]["name"]
            insts_k = [i for i, (kk, _, _) in enumerate(inst) if kk == k]
            # accents: semicontinuous {0} ∪ [serve_min, min(serve_max,
            # max_g·t)] — one binary each, attached to their dish's slot
            for cid in a["accents"]:
                if cid not in comps:
                    continue
                lo_c, hi_c = serve[cid]
                key = ("acc", k, cid)
                bkey = key
                hosts = [(i, inst[i][2]["components"][cid])
                         for i in insts_k
                         if cid in inst[i][2]["components"]]
                if key in fixed_x:
                    y[key] = fixed_x[key]
                    slot_comp.setdefault((k, cid), []).append(fixed_x[key])
                    continue
                v = pulp.LpVariable(f"a_{k}_{cid}", 0, hi_c)
                bb = (fixed_b[bkey] if bkey in fixed_b
                      else pulp.LpVariable(f"ba_{k}_{cid}", cat="Binary"))
                m += v >= lo_c * bb
                m += v <= hi_c * bb
                if hosts:
                    # accent band scales with EVERY host dish's t — one
                    # pooled variable, so the cap is the SUM of the hosts'
                    # band ceilings (any split stays in-band per host);
                    # previously only the last host's band·t constrained
                    m += v <= pulp.lpSum(hb["max_g"] * t[i]
                                         for i, hb in hosts)
                slot_comp.setdefault((k, cid), []).append(v)
                y[key], b[bkey] = v, bb
            # sides: semicontinuous, ≤ SIDES_PER_SLOT_MAX nonzero
            side_bs = []
            for cid in a["sides"]:
                if cid not in comps:
                    continue
                lo_c, hi_c = serve[cid]
                lo_c = max(lo_c, floor_g)        # MIN_SUBPORTION_G floor
                key = ("side", k, cid)
                if key in fixed_x:
                    y[key] = fixed_x[key]
                    slot_comp.setdefault((k, cid), []).append(fixed_x[key])
                    continue
                v = pulp.LpVariable(f"s_{k}_{cid}", 0, hi_c)
                bb = (fixed_b[key] if key in fixed_b
                      else pulp.LpVariable(f"bs_{k}_{cid}", cat="Binary"))
                m += v >= lo_c * bb
                m += v <= hi_c * bb
                slot_comp.setdefault((k, cid), []).append(v)
                y[key], b[key] = v, bb
                side_bs.append(bb)
            if side_bs:
                m += pulp.lpSum(side_bs) <= W["SIDES_PER_SLOT_MAX"]

        # per-MEAL serve caps, per SLOT-COMPONENT (§5: 'x[c,s] ≤
        # effective_serve_bounds(c,p).max per slot'; §6 rung 1: HARD,
        # never relaxed): the summed grams of a component across every
        # dish instance, accent and side sharing the slot obey ONE cap —
        # per-instance caps alone let two opted-in dishes ship 2x the
        # authored per-serving absolute (the lard-beans class of breach).
        for (kk, cid), terms in slot_comp.items():
            hi_cap = serve[cid][1]
            vs = [v for v in terms if not isinstance(v, (int, float))]
            frozen = sum(v for v in terms if isinstance(v, (int, float)))
            if vs:
                m += pulp.lpSum(vs) <= hi_cap - frozen
            elif frozen > hi_cap + 1e-6:
                return None          # frozen grams already breach the cap

        every = list(x.items()) + list(y.items())
        cap = person.get("max_daily_mass_g")
        if cap:
            m += pulp.lpSum(v for _, v in every
                            if not isinstance(v, (int, float))) \
                <= cap - sum(v for _, v in every
                             if isinstance(v, (int, float)))

        def slot_expr(k, mac):
            terms = []
            for key, v in every:
                kk = key[1]
                cid = key[2]
                if kk == k:
                    terms.append(v * comps[cid]["per100"][mac] / 100
                                 if not isinstance(v, (int, float))
                                 else v * comps[cid]["per100"][mac] / 100)
            return pulp.lpSum(terms)

        def key_slot(key):
            if key[0] == "x":
                return inst[key[1]][0]
            return key[1]

        # day macro tolerance — soft, slack-dominant (plate() verbatim)
        day_slack = {}
        for mac in MACROS:
            tgt = targets[mac]
            e = pulp.lpSum(
                (v if isinstance(v, (int, float)) else v)
                * comps[key[2]]["per100"][mac] / 100
                for key, v in every)
            over = pulp.LpVariable(f"o_{mac}", 0)
            under = pulp.LpVariable(f"u_{mac}", 0)
            m += e - over <= tgt * (1 + tol)
            m += e + under >= tgt * (1 - tol)
            day_slack[mac] = (over, under)

        # per-slot macro bands — softest (§6 precedence)
        meal_slack = []
        if meal_bands and n > 1:
            band = MEAL_WEIGHTS["MEAL_BAND"]
            for k in range(n):
                for mac in MACROS:
                    share = targets[mac] / n
                    e = pulp.lpSum(
                        v * comps[key[2]]["per100"][mac] / 100
                        for key, v in every if key_slot(key) == k)
                    ov = pulp.LpVariable(f"mo_{k}_{mac}", 0)
                    un = pulp.LpVariable(f"mu_{k}_{mac}", 0)
                    m += e - ov <= share * (1 + band)
                    m += e + un >= share * (1 - band)
                    meal_slack.extend((ov, un))

        obj = SCORE_WEIGHTS["plate_slack_dominance"] * pulp.lpSum(
            s for pr in day_slack.values() for s in pr)
        obj += W["meal_band_slack"] * pulp.lpSum(meal_slack)
        obj += W["gram_tiebreak"] * pulp.lpSum(
            (SCORE_WEIGHTS["dislike_multiplier"] if key[2] in dislikes
             else 1.0) * v
            for key, v in every if not isinstance(v, (int, float)))
        obj += W["binary_epsilon"] * pulp.lpSum(
            _eps_of(seed, key[2], assignments[key[1]]["slot"]["name"]) * bb
            for key, bb in b.items() if not isinstance(bb, (int, float)))
        m += obj
        return m, t, x, y, b, day_slack

    # ---- pins (§8): clamp into dish bands ∩ serve caps ---------------------
    fixed_x = {}
    for pk, pv in (locked or {}).items():
        slot_name, cid = pk if isinstance(pk, tuple) else (None, pk)
        placed = False
        for i, (k, j, d) in enumerate(inst):
            if slot_name is not None \
                    and assignments[k]["slot"]["name"] != slot_name:
                continue
            if cid in d["components"] and cid not in d["accents"]:
                band = d["components"][cid]
                lo = band["min_g"] * W["DISH_T_MIN"]
                hi = min(band["max_g"] * max(t_max(d, comps, person),
                                             W["DISH_T_MIN"]),
                         serve.get(cid, (0, math.inf))[1])
                adj = min(hi, max(lo, float(pv)))
                u = comps[cid].get("unit_g") if cid in comps else None
                if u:
                    adj = round(adj / u) * u
                    adj = min(hi, max(lo, adj))
                if adj != float(pv):
                    warnings.append(dict(
                        code="pin_adjusted", component=cid,
                        pinned_g=float(pv), adjusted_g=adj,
                        message=(f"pin {pv}g on '{cid}' adjusted to "
                                 f"{adj:g}g (clamped into the dish band ∩ "
                                 "serve caps — dish identity is hard)")))
                fixed_x[("x", i, cid)] = adj
                placed = True
                break
        if not placed:
            warnings.append(dict(
                code="pin_unknown", component=cid, pinned_g=float(pv),
                message=(f"pin {pv}g on '{cid}' dropped: not a member of "
                         "any assigned dish"
                         + (f" in slot '{slot_name}'" if slot_name
                            else ""))))

    built = build(fixed_x=fixed_x)
    if built is None:
        return dict(status="infeasible", meals=[], miss={}, day_flags=[],
                    warnings=warnings, binding_slot=None)
    m, t, x, y, b, day_slack = built
    with solve_stage("plate-dish"):
        _count_solve()
        status = m.solve(pulp.PULP_CBC_CMD(msg=0))
    if status != 1:
        return dict(status="infeasible", meals=[], miss={},
                    day_flags=[], warnings=warnings,
                    binding_slot=_heaviest_slot(assignments, dishes))

    # ---- pass 2: freeze binaries, snap unit_g grams, re-solve --------------
    fixed_b = {}
    for key, bb in b.items():
        if not isinstance(bb, (int, float)):
            fixed_b[key] = int(round(bb.value() or 0))
    snap_x = dict(fixed_x)
    for key, v in list(x.items()) + list(y.items()):
        if isinstance(v, (int, float)):
            continue
        cid = key[2]
        u = comps[cid].get("unit_g")
        if not u:
            continue
        val = v.value() or 0
        if key[0] != "x" and fixed_b.get(key, 1) == 0:
            snap_x[key] = 0
            continue
        if key[0] == "x":
            # CORE member: the snapped grid point must stay inside the
            # reachable band ``[min_g·T_MIN, min(max_g·t_max, serve_max)]``
            # — a bare round() can banker-round a member solved at its
            # floor (e.g. 35.5g of a 71g tortilla at t=0.5) down to ZERO
            # units, freezing a core member out of its own dish and
            # stranding pass 2 (0 >= min_g·t vs t >= T_MIN is infeasible).
            i = key[1]
            d_i = inst[i][2]
            band = d_i["components"][cid]
            tm_i = max(t_max(d_i, comps, person), W["DISH_T_MIN"])
            k_lo = max(1, math.ceil(band["min_g"] * W["DISH_T_MIN"] / u
                                    - 1e-9))
            k_hi = max(k_lo, math.floor(
                min(band["max_g"] * tm_i, serve[cid][1]) / u + 1e-9))
            snapped = min(k_hi, max(k_lo, round(val / u))) * u
        else:
            snapped = round(val / u) * u
            if snapped == 0 and fixed_b.get(key) == 1:
                snapped = u
        snap_x[key] = snapped
    built = build(fixed_x=snap_x, fixed_b=fixed_b)
    if built is None:
        # snapped/frozen grams already breach a hard cap: retry the ladder
        return dict(status="infeasible", meals=[], miss={},
                    day_flags=[], warnings=warnings,
                    binding_slot=_heaviest_slot(assignments, dishes))
    m2, t2, x2, y2, b2, day_slack2 = built
    with solve_stage("plate-dish"):
        _count_solve()
        status = m2.solve(pulp.PULP_CBC_CMD(msg=0))
    if status != 1:
        # a snapped grid point stranded the band: retry the ladder
        return dict(status="infeasible", meals=[], miss={},
                    day_flags=[], warnings=warnings,
                    binding_slot=_heaviest_slot(assignments, dishes))
    t, x, y, day_slack = t2, x2, y2, day_slack2

    # ---- emit meals directly from the solve (M19 §4 MealDay shape) ---------
    miss = {}
    for mac, (o, u_) in day_slack.items():
        ov, un = (o.value() or 0), (u_.value() or 0)
        if ov > 0.5:
            miss[mac] = round(ov, 1)
        elif un > 0.5:
            miss[mac] = -round(un, 1)

    def val(v):
        return v if isinstance(v, (int, float)) else (v.value() or 0)

    meals, day_flags = [], []
    for k, a in enumerate(assignments):
        slot = a["slot"]
        items = {}
        exact = {}
        insts_k = [i for i, (kk, _, _) in enumerate(inst) if kk == k]
        servings = {}
        for i in insts_k:
            _, j, d = inst[i]
            tv = val(t[i])
            servings[j] = round(tv, 2)
            for cid in d["components"]:
                key = ("x", i, cid)
                if key in x:
                    g = val(x[key])
                    # STRUCTURAL ASSERT (§8 conservation): every non-side
                    # item is its dish's member within [t·min, t·max] ∩ caps
                    band = d["components"][cid]
                    assert (band["min_g"] * tv - 1.0 <= g
                            <= band["max_g"] * tv + 1.0), (
                        f"dish integrity broken: {g}g of '{cid}' outside "
                        f"[{band['min_g'] * tv:.1f}, "
                        f"{band['max_g'] * tv:.1f}] of dish '{j}'")
                    exact[cid] = exact.get(cid, 0.0) + g
                    if g >= 1:
                        items[cid] = items.get(cid, 0) + int(round(g))
        for key, v in y.items():
            if key[1] != k:
                continue
            g = val(v)
            exact[key[2]] = exact.get(key[2], 0.0) + g
            if g >= 1:
                items[key[2]] = items.get(key[2], 0) + int(round(g))
        # STRUCTURAL ASSERT (§8 conservation): the per-MEAL serve cap
        # holds on the SLOT'S summed grams of each component — instances
        # individually in-band is NOT enough when opted-in dishes (§7
        # rung 3) share a member or a member doubles as accent/side
        for cid, g_tot in exact.items():
            assert g_tot <= serve[cid][1] + 1.0, (
                f"per-meal serve cap broken: {g_tot:.1f}g of '{cid}' in "
                f"slot '{slot['name']}' > {serve[cid][1]}g")
        vec = {m_: sum(g * comps[c]["per100"][m_] / 100
                       for c, g in items.items()) for m_ in MACROS}
        vec["kcal"] = kcal_of(vec)
        d0 = dishes[a["dishes"][0]] if a["dishes"] else None
        meal = dict(
            slot=slot["name"], serving_model=slot["serving_model"],
            dish=(a["dishes"][0] if a["dishes"] else None),
            dish_name=(d0["name"] if d0 else None),
            dishes=list(a["dishes"]), servings=servings,
            items={c: items[c] for c in sorted(items)},
            macros={m_: round(vec[m_], 1) for m_ in MACROS}
            | {"kcal": round(vec["kcal"], 1)},
            target={m_: round(targets[m_] / max(n, 1), 1) for m_ in MACROS},
            flags=[])
        # sorted: set iteration order is PYTHONHASHSEED-dependent — flag
        # order must be byte-stable across processes (verify-plan golden)
        for sid in sorted(a["orphan_sides"]):
            if sid in items:
                meal["flags"].append(dict(
                    code="orphan_side", slot=slot["name"], component=sid,
                    message=(f"'{sid}' serves beside '{meal['dish']}' "
                             "without an authored compatible_sides entry "
                             "(dish_layer: permissive — author the "
                             "pairing or it dies at the strict ratchet)")))
        # per-meal band flags (§6 precedence: softest, reported)
        if meal_bands and n > 1 and a["dishes"]:
            band = MEAL_WEIGHTS["MEAL_BAND"]
            for mac in MACROS:
                share = targets[mac] / n
                lo_b, hi_b = share * (1 - band), share * (1 + band)
                if lo_b - 1e-9 <= vec[mac] <= hi_b + 1e-9:
                    continue
                resp = max(items, key=lambda c: (
                    items[c] * comps[c]["per100"][mac] / 100, c),
                    default=None)
                delta = vec[mac] - share
                meal["flags"].append(dict(
                    code="band_unmet", slot=slot["name"], macro=mac,
                    achieved_g=round(vec[mac], 1),
                    band=[round(lo_b, 1), round(hi_b, 1)],
                    target_g=round(share, 1), delta_g=round(delta, 1),
                    responsible_component=resp,
                    responsible_dish=meal["dish"],
                    binding_bound=_binding_bound(
                        insts_k, inst, t, x, serve, val, person, comps),
                    message=(f"'{slot['name']}' is {abs(round(delta))}g "
                             f"{'over' if delta > 0 else 'short'} of its "
                             f"{mac} slot target — dish integrity wins "
                             f"({meal['dish']})")))
        meals.append(meal)

    # day-level receipts (§6): a day miss attributable to a hard band.
    # Cappedness is read off the MEMBER GRAMS against their absolute
    # ceilings min(max_g·t_max, serve_max) — never off t itself: once the
    # serve caps bind, t is degenerate in the LP (raising it adds nothing)
    # and CBC legitimately reports it below t_max.
    slot_tot = {}
    for key, v in list(x.items()) + list(y.items()):
        kk_t = inst[key[1]][0] if key[0] == "x" else key[1]
        slot_tot[(kk_t, key[2])] = slot_tot.get((kk_t, key[2]), 0.0) \
            + val(v)

    def _capped_for(i, d, macro=None):
        """Every core member (carrying ``macro`` when given) of instance
        ``i`` is within a gram of its absolute ceiling — the instance
        cannot scale further, whatever t says. The ceiling is the
        member's own ``min(max_g·t_max, serve_max)`` OR the slot's
        SHARED serve cap on the summed grams (§5): a shared member of
        two opted-in dishes is capped even when each instance sits
        below its individual ceiling."""
        tm = max(t_max(d, comps, person), W["DISH_T_MIN"])
        hit_any = False
        for cid, band in d["components"].items():
            key = ("x", i, cid)
            if key not in x or band["min_g"] <= 0:
                continue
            if macro is not None and comps[cid]["per100"][macro] <= 0:
                continue
            hit_any = True
            ceiling = min(band["max_g"] * tm, serve[cid][1])
            if val(x[key]) < ceiling - 1.0 \
                    and slot_tot.get((inst[i][0], cid), 0.0) \
                    < serve[cid][1] - 1.0:
                return False
        return hit_any

    if miss:
        for k, a in enumerate(assignments):
            insts_k = [i for i, (kk, _, _) in enumerate(inst) if kk == k]
            for i in insts_k:
                _, j, d = inst[i]
                tm = max(t_max(d, comps, person), W["DISH_T_MIN"])
                lim = t_max_limiter(d, comps, person)
                smax = serve.get(lim, (0, 0))[1] if lim else None
                for mac, delta in miss.items():
                    if delta < 0 and _capped_for(i, d, mac):
                        # short AND every carrier at its hard ceiling
                        day_flags.append(dict(
                            code="dish_band_binding", macro=mac,
                            dish=j, component=lim,
                            bound=(f"t_max {tm:.2f} ({lim} at "
                                   f"{smax:g}g/serving max)"),
                            delta_g=delta,
                            message=(f"{mac} short {abs(delta)}g — "
                                     f"'{j}' maxed at {tm:.2f} "
                                     f"servings ({lim} at {smax:g}g/"
                                     "serving max)")))
        # §7: slot underfilled with every cap binding and rung 3 off
        for k, a in enumerate(assignments):
            if not a["dishes"]:
                continue
            share = kcal_of(targets) / max(n, 1)
            got = meals[k]["macros"]["kcal"]
            insts_k = [i for i, (kk, _, _) in enumerate(inst) if kk == k]
            all_capped = all(_capped_for(i, inst[i][2]) for i in insts_k)
            if got < share * (1 - MEAL_WEIGHTS["MEAL_BAND"]) and all_capped \
                    and slot_max_dishes(person, a["slot"]["name"]) < 2:
                caps = sorted({t_max_limiter(inst[i][2], comps, person)
                               for i in insts_k if
                               t_max_limiter(inst[i][2], comps, person)})
                day_flags.append(dict(
                    code="meal_underfilled", slot=a["slot"]["name"],
                    shortfall_g_by_macro={m_: round(
                        max(0.0, targets[m_] / max(n, 1)
                            - meals[k]["macros"][m_]), 1) for m_ in MACROS},
                    binding_caps=caps,
                    options=["widen the binding serve_g/band",
                             "author heartier compatible_sides",
                             "opt in max_dishes_per_slot: 2"],
                    message=(f"'{a['slot']['name']}' ships "
                             f"{got:.0f} kcal vs a {share:.0f} share — "
                             f"serve caps bind on {', '.join(caps)}; "
                             "remedies: wider band, heartier sides, or "
                             "max_dishes_per_slot: 2 (owner opt-in)")))

    return dict(status=("ok" if not miss else "miss"), meals=meals,
                miss=miss, day_flags=day_flags, warnings=warnings,
                binding_slot=_heaviest_slot(assignments, dishes))


def _binding_bound(insts_k, inst, t, x, serve, val, person, comps):
    """Name the active hard constraint for band_unmet receipts, read off
    the frozen pass-2 solution (cheap and exact)."""
    for i in insts_k:
        _, j, d = inst[i]
        tv = val(t[i])
        tm = max(t_max(d, comps, person), DISH_WEIGHTS["DISH_T_MIN"])
        if tv >= tm - 1e-6:
            return f"t_max {tm:.2f} of '{j}'"
        if tv <= DISH_WEIGHTS["DISH_T_MIN"] + 1e-6:
            return f"t_min {DISH_WEIGHTS['DISH_T_MIN']} of '{j}'"
        for cid, band in d["components"].items():
            key = ("x", i, cid)
            if key not in x:
                continue
            g = val(x[key])
            if g >= band["max_g"] * tv - 1e-6:
                return f"{cid} max_g {band['max_g']}g/serving"
            if cid in serve and g >= serve[cid][1] - 1e-6:
                return f"{cid} serve_max {serve[cid][1]}g"
    return None


def _heaviest_slot(assignments, dishes):
    """Retry-ladder target on infeasibility: the slot whose assignment
    forces the most minimum mass (deterministic; ties → lowest index)."""
    best, best_k = -1.0, None
    for k, a in enumerate(assignments):
        w = 0.0
        for j in a["dishes"]:
            w += sum(b["min_g"] for b in dishes[j]["components"].values())
        if w > best:
            best, best_k = w, k
    return best_k


# --------------------------------------------------------------------------- #
#  week assembly (dish mode) — the §1 pipeline steps 2–4
# --------------------------------------------------------------------------- #
def build_week_dishes(comps, people, settings, dishes, menu, seed=0,
                      ing=None, diag=None, leftovers=None):
    """Dish-mode week assembly: per person-day, skeleton → plate_dishes →
    MealDay emitted DIRECTLY from the solve; the day plate is the derived
    Σ over meals (an arithmetic identity, asserted); ``demand[c] +=
    grams`` exactly as build_week does — everything downstream consumes
    component grams and never learns what a dish is.

    Returns ``(weeks, demand, mealdays)``. ``diag`` (P8) gains
    ``dish_retries`` (per person: skeleton retry count per day),
    ``dish_flag_counts``, and ``no_dish_assignable`` — the §13 pre-M1.6
    escalation instrumentation."""
    W = _weights()
    days = settings["days"]
    leftover_days = {}
    for e in (leftovers or []):
        leftover_days.setdefault(e["component"], set()).update(e["days"])
    weeks, demand, mealdays = {}, {}, {}
    flag_counts = {}
    for pname in sorted(people):
        person = people[pname]
        slots = resolve_meal_slots(person)
        slotless = slots is None
        used_days = {}
        week_demand = set()
        wk, mds, retries_by_day = [], [], []
        for d in range(days):
            day_slots = slots if not slotless \
                else _slotless_blocks(person, dishes, comps, menu, d,
                                      settings, ing, leftover_days, W)
            bumps, attempts = {}, 0
            res = None
            asn = notes = None
            while True:
                asn, notes = skeleton_day(
                    person, dishes, comps, settings, d, day_slots, menu,
                    ing=ing, extra_days=leftover_days, used_days=used_days,
                    week_demand=week_demand, rank_bump=bumps)
                res = plate_dishes(person, comps, dishes, asn, settings,
                                   seed=seed, meal_bands=not slotless)
                if res["status"] != "infeasible" \
                        or attempts >= W["ASSIGN_RETRIES"]:
                    break
                k = res["binding_slot"]
                if k is None:
                    break
                bumps[k] = bumps.get(k, 0) + 1
                attempts += 1
            retries_by_day.append(attempts)
            meals_out = res["meals"]
            if res["status"] == "infeasible":
                # exhausted ladder → explained hole (never silent)
                hole = dict(
                    code="no_dish_assignable", day=d,
                    slot=[a["slot"]["name"] for a in asn
                          if not a["dishes"]] or
                    [a["slot"]["name"] for a in asn],
                    tried=attempts,
                    reasons=sorted((asn[0]["blockers"] or {}).values(),
                                   key=lambda r: r["dish"])
                    if asn and asn[0]["blockers"] else [],
                    message=(f"day {d}: no dish assignment solved after "
                             f"{attempts} retries — explained hole"))
                notes = list(notes) + [hole]
                if diag is not None:
                    diag.setdefault("no_dish_assignable", []).append(
                        dict(person=pname, day=d))
                meals_out = [dict(slot=a["slot"]["name"],
                                  serving_model=a["slot"]["serving_model"],
                                  dish=None, dish_name=None, dishes=[],
                                  servings={}, items={},
                                  macros={m: 0.0 for m in MACROS}
                                  | {"kcal": 0.0},
                                  target={m: round(
                                      person["targets"][m]
                                      / max(len(asn), 1), 1)
                                      for m in MACROS},
                                  flags=[dict(hole)]) for a in asn]
            md = dict(meals=meals_out, notes=list(notes))
            for f in res["day_flags"]:
                md["notes"].append(dict(f))
                flag_counts[f["code"]] = flag_counts.get(f["code"], 0) + 1
            for meal in meals_out:
                for f in meal.get("flags", []):
                    flag_counts[f["code"]] = \
                        flag_counts.get(f["code"], 0) + 1
            # derived day plate: Σ meals ≡ day plate (conservation is
            # definitional — kept as the cheap sanity check, §8)
            plate = {}
            for meal in meals_out:
                for cid, g in meal["items"].items():
                    plate[cid] = plate.get(cid, 0) + g
            dealt = {}
            for meal in meals_out:
                for cid, g in meal["items"].items():
                    dealt[cid] = dealt.get(cid, 0) + g
            assert dealt == plate, "Σ meals != day plate (definitional)"
            wk.append(plate)
            mds.append(md)
            today_dishes = {j for a in (asn or []) for j in a["dishes"]}
            for j in today_dishes:
                used_days[j] = used_days.get(j, 0) + 1
            for cid, g in plate.items():
                demand[cid] = demand.get(cid, 0) + g
                week_demand.add(cid)
        if diag is not None:
            diag.setdefault("dish_retries", {})[pname] = retries_by_day
        # M1.9-IC unchanged: permute whole composed meals; dish identity
        # travels with the meal
        if not slotless and any(s.get("interchangeable") for s in slots):
            from .meals import equalize_interchangeable
            mds = equalize_interchangeable(mds, slots)
        weeks[pname] = wk
        mealdays[pname] = mds
    if diag is not None:
        diag["dish_flag_counts"] = flag_counts
    return weeks, demand, mealdays


def _slotless_blocks(person, dishes, comps, menu, day, settings, ing,
                     extra_days, W):
    """§4 people without slots: uniform machinery, one implicit unlabeled
    day-block — up to DISHES_PER_DAY dish blocks (fewest that clear the
    day target on capacity arithmetic), each portioned within its own
    bands, no per-meal macro bands (day tolerance only). Dish integrity
    holds unconditionally."""
    target = kcal_of(person["targets"])
    cands = []
    for j in menu:
        if j not in dishes:
            continue
        d = dishes[j]
        if not dish_available_on(d, comps, day, settings, ing,
                                 extra_days)[0]:
            continue
        if not dish_eligible(d, comps, person)[0]:
            continue
        cands.append(dish_kcal_range(d, comps, person, W)[1])
    cands.sort(reverse=True)
    k, reach = 1, (cands[0] if cands else 0.0)
    while reach < target and k < W["DISHES_PER_DAY"]:
        k += 1
        reach += cands[min(k - 1, len(cands) - 1)] if cands else 0.0
    sm = person.get("serving_model") or "portioned"
    return [dict(name=f"dish_{i + 1}", serving_model=sm,
                 interchangeable=False) for i in range(k)]


# --------------------------------------------------------------------------- #
#  §8 replate (dish mode): pins clamp into dish bands; a dish veto
#  re-runs the skeleton for the day, then re-solves
# --------------------------------------------------------------------------- #
def replate_dishes(person, dishes, comps, menu, day, settings, *,
                   locked=None, veto=None, seed=0, ing=None,
                   used_days=None, leftovers=None):
    """Re-solve ONE person's ONE day in dish mode. ``locked``: pins —
    ``{(slot_name, cid): g}`` (slot-qualified pins become natural in dish
    mode; M19's slot_pin_unsupported restriction lifts) or ``{cid: g}``.
    ``veto``: dish ids banned for this day — the skeleton re-runs without
    them; no alternative → ``no_alternative_dish`` explained. Returns the
    plate_dishes result dict (+ a day_flags entry on veto exhaustion)."""
    leftover_days = {}
    for e in (leftovers or []):
        leftover_days.setdefault(e["component"], set()).update(e["days"])
    slots = resolve_meal_slots(person) or _slotless_blocks(
        person, dishes, comps, menu, day, settings, ing, leftover_days,
        _weights())
    asn, notes = skeleton_day(
        person, dishes, comps, settings, day, slots, menu, ing=ing,
        extra_days=leftover_days, used_days=used_days or {}, veto=veto)
    if veto and not any(a["dishes"] for a in asn):
        return dict(status="infeasible", meals=[], miss={}, warnings=[],
                    binding_slot=None, day_flags=[dict(
                        code="no_alternative_dish", day=day,
                        vetoed=sorted(veto),
                        message=(f"day {day}: no alternative dish after "
                                 f"vetoing {sorted(veto)} — the veto "
                                 "cannot be honored"))])
    res = plate_dishes(person, comps, dishes, asn, settings, seed=seed,
                      locked=locked, meal_bands=slots is not None)
    res["notes"] = notes
    return res


# --------------------------------------------------------------------------- #
#  §3 menu selection over dishes — two-phase shape survives verbatim
# --------------------------------------------------------------------------- #
def score_menu_dishes(comps, ing, dishes, chosen, settings, people=None,
                      score_weights=None):
    """CHEAP dish-menu score — zero LP, structural proxies only, so local
    search runs hot. Cost/time/waste/batch estimates run on the COMPONENT
    CLOSURE unchanged (components are the batch units — shared components
    consolidate in the estimate exactly as at cook time); the structural
    terms translate weight-for-weight against SCORE_WEIGHTS (§3)."""
    from .costing import (budget_ceiling, cook_minutes, cookable_sessions,
                          estimate_batches, menu_cost, purchase,
                          sessions_for)
    W = dict(SCORE_WEIGHTS)
    W.update(DISH_WEIGHTS)
    W.update(score_weights or {})
    closure_ids = [c for c in closure(dishes, chosen) if c in comps]
    batches = (estimate_batches(comps, people, settings, closure_ids)
               if people else {i: 1 for i in closure_ids})
    rows, wp, _ = purchase(comps, ing, closure_ids, batches)
    active = cook_minutes(comps, settings, batches)
    cuisines = len({dish_cuisine(dishes[j], comps) for j in chosen})
    days_n = settings["days"]

    # side closure: what can actually be SERVED as a side (§3 — the
    # "starches expire mid-week" lesson re-based); permissive admits
    # orphan starch/veg from the closure
    side_ids = set()
    for j in chosen:
        side_ids |= {s for s in dishes[j]["compatible_sides"] if s in comps}
    if (settings.get("dish_layer") or "permissive") == "permissive":
        side_ids |= {s for s in orphan_sides(dishes, comps)
                     if s in closure_ids}
    accent_ids = set()
    for j in chosen:
        accent_ids |= {a for a in dishes[j]["accents"] if a in comps}

    pen = 0
    pen += max(0, active - settings["active_min_budget"]) \
        * W["time_over_budget_per_min"]
    # role floors, dish translations (§3): every dish carries a main
    pen += 0 if len(chosen) >= 3 else W["role_floor_mains"]
    starches_in_sides = [s for s in side_ids
                         if comps[s]["role"] == "starch"]
    pen += 0 if len(starches_in_sides) >= 2 else W["role_floor_starches"]
    pen += 0 if len(accent_ids) >= 2 else W["role_floor_accents"]
    pen -= cuisines * W["cuisine_variety_reward"]
    n_sessions = len(sessions_for(settings))
    for i in closure_ids:
        pen += W["uncookable_session_pair"] * (
            n_sessions - len(cookable_sessions(comps[i], settings, ing)))
    cap = budget_ceiling(settings, people or {})
    est = menu_cost(comps, ing, closure_ids, batches=batches)
    if cap:
        pen += max(0, est - cap) * W["budget_overage_per_dollar"]
    pen += est * W["cost_per_dollar"]

    if people:
        avail = {j: [dish_available_on(dishes[j], comps, d, settings,
                                       ing)[0] for d in range(days_n)]
                 for j in chosen}
        side_avail = {s: [available_on(comps[s], d, settings, ing)
                          for d in range(days_n)] for s in side_ids}
        labels = _recognized_labels(dishes)
        for p in people.values():
            elig = [j for j in chosen
                    if dish_eligible(dishes[j], comps, p)[0]]
            pen += 0 if len(elig) >= 3 else W["person_mains_floor"]
            est_st = [s for s in starches_in_sides if eligible(comps[s], p)]
            pen += 0 if len(est_st) >= 3 else W["person_starch_floor"]
            eacc = [a for a in accent_ids if eligible(comps[a], p)] \
                + [s for s in side_ids if comps[s]["role"] == "veg"
                   and eligible(comps[s], p)]
            pen += 0 if len(eacc) >= 2 else W["person_accents_floor"]
            # lean anchors — the dish's OWN achievable ratio (P3 aggregate
            # test; anchor flags do not survive assembly)
            need_ratio = p["targets"]["protein"] / max(p["targets"]["fat"],
                                                       1)
            lean = [j for j in elig
                    if dish_macro_ratio_max(dishes[j], comps, p)
                    >= need_ratio * 1.25]
            pen += 0 if len(lean) >= settings["min_lean_anchors"] \
                else W["lean_anchor_floor"]
            uncovered = sum(1 for d in range(days_n)
                            if not any(avail[j][d] for j in lean))
            pen += W["uncovered_lean_day"] * uncovered
            # carb headroom, day-correct on dishes + servable sides
            worst_carb = min(
                sum(t_max(dishes[j], comps, p)
                    * sum(b["max_g"] * comps[c]["per100"]["carb"] / 100
                          for c, b in dishes[j]["components"].items()
                          if c in comps and eligible(comps[c], p))
                    for j in elig if avail[j][d])
                + sum(effective_serve_bounds(comps[s], p)[1]
                      * comps[s]["per100"]["carb"] / 100
                      for s in side_ids
                      if eligible(comps[s], p) and side_avail[s][d])
                for d in range(days_n)) if days_n else 0
            pen += 0 if worst_carb >= p["targets"]["carb"] \
                else W["carb_headroom_shortfall"]
            # NEW: slot coverage + capacity (§3, P9 weights)
            slots = resolve_meal_slots(p)
            if slots:
                n = len(slots)
                aff_slots = [s["name"] for s in slots
                             if s["name"] in labels]
                for d in range(days_n):
                    for sname in aff_slots:
                        hit = any(
                            avail[j][d] and (
                                not dishes[j]["meal_affinity"]
                                or "any" in dishes[j]["meal_affinity"]
                                or sname in dishes[j]["meal_affinity"])
                            for j in elig)
                        if not hit:
                            pen += W["slot_coverage"]
                for d in range(days_n):
                    day_dishes = sorted(
                        (dish_kcal_range(dishes[j], comps, p)[1]
                         for j in elig if avail[j][d]), reverse=True)
                    if not day_dishes:
                        pen += W["slot_capacity_unmet"]
                        continue
                    cap_r = -(-n // len(day_dishes))
                    lineup = []
                    for hi in day_dishes:
                        lineup += [hi] * min(cap_r, n - len(lineup))
                        if len(lineup) >= n:
                            break
                    side_top = sorted(
                        (effective_serve_bounds(comps[s], p)[1]
                         * comps[s]["per100"]["kcal"] / 100
                         for s in side_ids
                         if eligible(comps[s], p) and side_avail[s][d]),
                        reverse=True)[:W["SIDES_PER_SLOT_MAX"]]
                    reach = sum(lineup) + n * sum(side_top)
                    if reach < kcal_of(p["targets"]):
                        pen += W["slot_capacity_unmet"]
    return wp + pen, dict(waste_perishable=wp, active_min=active,
                          cuisines=cuisines,
                          roles={"dish": len(chosen)},
                          closure=closure_ids)


def choose_menu_dishes(comps, ing, people, settings, dishes, n=8, seed=0,
                       iters=4000, shortlist=25, must=None):
    """Two-phase dish-menu search (§3): phase 1 local search on the cheap
    dish score; phase 2 verifies candidates best-first with ONE skeleton +
    plate_dishes feasibility solve per person on a representative day-0
    availability set, returning who broke and which constraint bound.

    ``must`` may name dishes OR components — a component id maps through
    dish membership (alphabetically-first containing dish) with a named
    warning (§3)."""
    import random
    import sys
    ids = sorted(dishes)
    must_dishes = []
    for mid in (must or []):
        if mid in dishes:
            must_dishes.append(mid)
            continue
        hosts = sorted(j for j, d in dishes.items()
                       if mid in d["components"]
                       or mid in d["compatible_sides"])
        if hosts:
            must_dishes.append(hosts[0])
            print(f"[warning:must_component_mapped] --force '{mid}' is a "
                  f"component; mapped through dish membership to "
                  f"'{hosts[0]}'", file=sys.stderr)
    n = min(n, len(ids))
    sc = lambda sel: score_menu_dishes(comps, ing, dishes, sel, settings,
                                       people)
    seen = {}
    for restart in range(6):
        r2 = random.Random(seed * 100 + restart)
        rest = [i for i in ids if i not in must_dishes]
        sel = list(must_dishes) + r2.sample(rest,
                                            max(0, n - len(must_dishes)))
        cur, _ = sc(sel)
        for _ in range(iters // 6):
            swappable = [i for i in sel if i not in must_dishes]
            outside = [i for i in ids if i not in sel]
            if not swappable or not outside:
                break
            out = r2.choice(swappable)
            inn = r2.choice(outside)
            cand = [i for i in sel if i != out] + [inn]
            s2, _ = sc(cand)
            if s2 <= cur:
                sel, cur = cand, s2
            seen[frozenset(cand)] = s2
        seen[frozenset(sel)] = cur
    ranked = sorted(seen.items(), key=lambda kv: (kv[1],
                                                  sorted(kv[0])))[:shortlist]
    first_fail = None
    with solve_stage("menu-verify"):
        for keys, _score in ranked:
            sel = sorted(keys)
            broke = {}
            for pname, p in people.items():
                slots = resolve_meal_slots(p) or [dict(
                    name="dish_1",
                    serving_model=p.get("serving_model") or "portioned",
                    interchangeable=False)]
                asn, _notes = skeleton_day(p, dishes, comps, settings, 0,
                                           slots, sel)
                res = plate_dishes(p, comps, dishes, asn, settings,
                                   meal_bands=False)
                if res["status"] == "infeasible":
                    broke[pname] = {m: -p["targets"][m] for m in MACROS}
                elif res["miss"]:
                    broke[pname] = res["miss"]
            if not broke:
                _, info = sc(sel)
                return sel, info, True, {}
            if first_fail is None:
                first_fail = (sel, broke)
    sel, broke = first_fail if first_fail else (sorted(ids[:n]), {})
    _, info = sc(sel)
    return sel, info, False, broke


# --------------------------------------------------------------------------- #
#  doctor dish section (§3 — arithmetic ONLY, zero LP, before any build)
# --------------------------------------------------------------------------- #
def doctor_dish_section(comps, people, settings, dishes, ing=None):
    """The P6 dish diagnostics: eligibility kills named, per-day dish
    availability with the shortest-lived core member named per dead dish,
    lean-dish coverage, dish carb headroom, and slot_target_unreachable —
    the big-eater check is load-time arithmetic, never a solve-time surprise.
    Returns ``(lines, data)`` for engine.doctor to splice in."""
    lines = ["\n## Dish layer\n"]
    data = {}
    days = settings["days"]
    ids = sorted(dishes)
    W = _weights()
    recon = {}
    for j in ids:
        recon.setdefault(dishes[j].get("reconstruction") or "?",
                         []).append(j)
    lines.append(f"- {len(ids)} dishes; reconstruction: " + "; ".join(
        f"{k} ×{len(v)}" for k, v in sorted(recon.items())))
    inv = []
    for j in ids:
        d = dishes[j]
        inv.append(dict(dish=j, name=d.get("name"),
                        cuisine=dish_cuisine(d, comps),
                        reconstruction=d.get("reconstruction"),
                        source=d.get("source"), notes=d.get("notes"),
                        core=core_members(d), accents=list(d["accents"]),
                        compatible_sides=list(d["compatible_sides"])))
    data["dishes"] = inv
    # per-day availability with the killer named per dead dish
    per_day = {}
    for dd in range(days):
        alive, dead = [], []
        for j in ids:
            ok, killer = dish_available_on(dishes[j], comps, dd, settings,
                                           ing)
            (alive if ok else dead).append((j, killer))
        per_day[dd] = dict(available=[j for j, _ in alive],
                           dead={j: k for j, k in dead})
        if dead:
            lines.append(f"- day {dd}: {len(alive)} dish(es) available; "
                         "dead: " + ", ".join(
                             f"{j} (killed by {k})" for j, k in dead))
    data["availability"] = per_day
    data["per_person"] = {}
    for pn, p in people.items():
        elig, killed = [], {}
        for j in ids:
            ok, hit = dish_eligible(dishes[j], comps, p)
            if ok:
                elig.append(j)
            else:
                killed[j] = dict(component=hit[0], tag=hit[1])
        need_ratio = p["targets"]["protein"] / max(p["targets"]["fat"], 1)
        lean = [j for j in elig
                if dish_macro_ratio_max(dishes[j], comps, p)
                >= need_ratio * 1.25]
        uncovered = [dd for dd in range(days)
                     if not any(j in per_day[dd]["available"]
                                for j in lean)]
        worst = None
        for dd in range(days):
            h = sum(
                t_max(dishes[j], comps, p)
                * sum(b["max_g"] * comps[c]["per100"]["carb"] / 100
                      for c, b in dishes[j]["components"].items()
                      if c in comps and eligible(comps[c], p))
                for j in elig if j in per_day[dd]["available"])
            for j in elig:
                if j not in per_day[dd]["available"]:
                    continue
                for s in dishes[j]["compatible_sides"]:
                    if s in comps and eligible(comps[s], p) \
                            and available_on(comps[s], dd, settings, ing):
                        h += effective_serve_bounds(comps[s], p)[1] \
                            * comps[s]["per100"]["carb"] / 100
            if worst is None or h < worst[1]:
                worst = (dd, h)
        # slot_target_unreachable (graft, P3/Judge 1): per slot, max
        # achievable kcal from eligible dishes at T_MAX + side maxima
        slots = resolve_meal_slots(p)
        unreachable = []
        if slots:
            n = len(slots)
            share = kcal_of(p["targets"]) / n
            for s in slots:
                best = 0.0
                for j in elig:
                    hi = dish_kcal_range(dishes[j], comps, p, W)[1]
                    side_kcal = sorted(
                        (effective_serve_bounds(comps[sd], p)[1]
                         * comps[sd]["per100"]["kcal"] / 100
                         for sd in dishes[j]["compatible_sides"]
                         if sd in comps and eligible(comps[sd], p)),
                        reverse=True)[:W["SIDES_PER_SLOT_MAX"]]
                    reach = (hi + sum(side_kcal)) \
                        * slot_max_dishes(p, s["name"])
                    best = max(best, reach)
                if best < share:
                    unreachable.append(dict(
                        code="slot_target_unreachable", person=pn,
                        slot=s["name"], achievable_kcal=round(best, 1),
                        target_kcal=round(share, 1)))
        data["per_person"][pn] = dict(
            eligible=len(elig), killed=killed, lean_dishes=lean,
            uncovered_lean_days=uncovered,
            carb_headroom=dict(worst_day=worst[0] if worst else None,
                               worst_headroom_g=round(worst[1], 1)
                               if worst else 0.0,
                               target_g=p["targets"]["carb"],
                               ok=bool(worst)
                               and worst[1] >= p["targets"]["carb"]),
            slot_target_unreachable=unreachable)
        lines.append(
            f"- **{pn}**: {len(elig)}/{len(ids)} dishes eligible"
            + ("; killed: " + ", ".join(
                f"{j} ({v['component']}: {v['tag']})"
                for j, v in sorted(killed.items())) if killed else "")
            + f"; lean dishes: {', '.join(lean) or 'NONE'}"
            + (f"; **zero lean-dish days: {uncovered}**" if uncovered
               else ""))
        ch = data["per_person"][pn]["carb_headroom"]
        lines.append(
            f"  - carb headroom: worst day {ch['worst_day']} — "
            f"{ch['worst_headroom_g']:.0f}g vs {ch['target_g']}g target: "
            + ("OK" if ch["ok"] else "**SHORT**"))
        for u in unreachable:
            lines.append(
                f"  - **slot_target_unreachable**: '{u['slot']}' max "
                f"achievable {u['achievable_kcal']:.0f} kcal vs "
                f"{u['target_kcal']:.0f} share — widen bands, author "
                "heartier sides, or opt in max_dishes_per_slot: 2")
    return lines, data
