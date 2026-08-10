"""The solver: plate LP, doctor diagnostics, menu search, week assembly.

Extracted verbatim from plan.py — behavior-preserving, bug-for-bug. Known
prototype defects deliberately kept for the M0.1 parity gate (each is a Phase 2
task with its own regression test):
- choose_menu rebinds `must` inside the restart loop (M0.5 --force wiring).

Fixed here (M0.10): score_menu computes waste, time, AND cost at the one
estimated-batch scale — the prototype mixed 1-batch waste with
estimated-batch cost inside the same score (plan.py:341).

Fixed here (M0.11, PRD §8.3): score_menu's shelf-life-stagger check is a real
per-day lean-anchor coverage penalty (the prototype's len(set-of-bools) no-op
at plan.py:397-398 is gone), and its carb ceiling is worst-day availability
headroom instead of the flat 1.45x whole-library fudge. doctor returns
(rendered_text, structured_data) and adds binding-macro, volume-floor,
lean-coverage, and carb-headroom sections (binding_macro / volume_floor /
lean_coverage / carb_headroom below).

The problem is two problems, kept separate on purpose:
1. WHICH components to cook this week — combinatorial, small, fuzzy objective.
   Greedy + local search (choose_menu).
2. HOW MANY GRAMS of each, to whom, on which day — continuous and exact. A
   linear program (plate).

Determinism (M0.3, fixed): every random draw flows from an explicit seed —
the prototype's hash-of-person-name day-seeding is gone; build_week derives
per-day seeds from its seed parameter and the sorted index of the person
name. No wall clock anywhere in the package.
"""

import math
import random
from contextlib import contextmanager

import pulp

from .costing import (budget_ceiling, cook_minutes, cookable_sessions,
                      estimate_batches, freezer_bridges, menu_cost, purchase,
                      raw_freshness, sessions_for, shop_days_for)
from .model import distinct_day_types, person_for_day, resolve_meal_slots
from .units import MACROS, fmt_miss, kcal_of


# --------------------------------------------------------------------------- #
#  M0.14 solve-count instrumentation (PRD §8.5)
# --------------------------------------------------------------------------- #
# Deterministic bookkeeping ONLY: every CBC invocation increments a counter
# tagged with the innermost active stage. The counters never feed a solver
# input, never branch engine behavior, and read no wall clock — timing lives
# OUTSIDE the engine, in instrument.py (allowlisted by the determinism scan).

SOLVE_COUNTS = {}          # stage -> CBC invocations since the last reset
_STAGE_STACK = []          # innermost active stage tag; empty -> "plate"


@contextmanager
def solve_stage(name):
    """Tag every LP solve inside the block with ``name`` (innermost wins)."""
    _STAGE_STACK.append(name)
    try:
        yield
    finally:
        _STAGE_STACK.pop()


def _count_solve():
    stage = _STAGE_STACK[-1] if _STAGE_STACK else "plate"
    SOLVE_COUNTS[stage] = SOLVE_COUNTS.get(stage, 0) + 1


def reset_solve_counts():
    SOLVE_COUNTS.clear()


def solve_counts():
    """Snapshot: {stage: CBC invocations} since the last reset."""
    return dict(SOLVE_COUNTS)


# --------------------------------------------------------------------------- #
#  score weights (PRD §8.3): named, configurable, PROVISIONAL — never hidden
# --------------------------------------------------------------------------- #
# EVERY value below is provisional (P9): these are v1's magic penalties made
# visible as one named registry instead of hiding as design. They were tuned
# by feel against the founder corpus and measured against nothing. score_menu
# accepts a ``score_weights`` override mapping (merged over these defaults);
# plate() reads its objective constants from here too. A settings-file
# surface for overrides is an M1 config concern — the M0 contract is that
# the weights are named, documented, and swappable, not baked into
# expressions.
SCORE_WEIGHTS = {
    # score_menu — menu-wide structural proxies
    "time_over_budget_per_min": 12,      # provisional (P9)
    "role_floor_mains": 4000,            # provisional (P9)
    "role_floor_starches": 4000,         # provisional (P9)
    "role_floor_accents": 1500,          # provisional (P9)
    "cuisine_variety_reward": 900,       # provisional (P9)
    "uncookable_session_pair": 6000,     # provisional (P9)
    "budget_overage_per_dollar": 120,    # provisional (P9)
    "cost_per_dollar": 1.5,              # provisional (P9)
    # score_menu — per-person structural proxies
    "person_mains_floor": 6000,          # provisional (P9)
    "person_starch_floor": 6000,         # provisional (P9)
    "carb_headroom_shortfall": 15000,    # provisional (P9)
    "person_accents_floor": 2500,        # provisional (P9)
    "lean_anchor_floor": 15000,          # provisional (P9)
    "uncovered_lean_day": 4000,          # provisional (P9)
    # plate LP objective
    "dislike_multiplier": 6.0,           # provisional (P9): soft down-weight
    "plate_slack_dominance": 10_000,     # provisional (P9): violations dominate
    "plate_weight_tiebreak": 0.001,      # provisional (P9): variety tie-break
}


# --------------------------------------------------------------------------- #
#  per-person serve_g scaling (M1.7, PRD §8.1) — PROVISIONAL, never hidden
# --------------------------------------------------------------------------- #
# REVOKED BY OWNER (2026-08-09, the lard-beans incident — PRD Appendix B
# item 2): kcal-proportional scaling of serve bounds was implemented (M1.7),
# measured against a real plan the same day, and rejected — authored serve_g
# bounds are per-dish palatability ABSOLUTES ("more than this much of one
# dish is gross, period"), already written with the household's biggest
# eater in mind. Scaling them by person-kcal double-counts appetite: it
# legalized 720g of refried beans in a day (400g cap x 1.8 clamp).
# The mechanism stays, DORMANT (identity defaults), for a possible future
# per-dish opt-in (e.g. component-level scalable: true) — never blanket math.
SCALING = {
    "reference_kcal": 2500,   # reference point if scaling is ever re-enabled
    "scale_min": 1.0,         # identity: authored bounds are absolute
    "scale_max": 1.0,         # identity: authored bounds are absolute
}


def person_scale(person):
    """This person's serve-bound scale: Atwater kcal of their daily targets
    over SCALING['reference_kcal'], clamped to [scale_min, scale_max]."""
    s = kcal_of(person["targets"]) / SCALING["reference_kcal"]
    return min(SCALING["scale_max"], max(SCALING["scale_min"], s))


def effective_serve_bounds(comp, person):
    """The (min, max) serve band THIS person actually gets for ``comp``
    (M1.7): the authored serve_g bounds times person_scale, re-aligned to
    the grid portions are actually EMITTED on — the unit grid for discrete
    (unit_g) components, the whole-gram grid otherwise (plate() rounds every
    portion to int grams, so a fractional scaled bound would let a portion
    solved AT the bound round outside it). Min ceils UP to the grid, max
    floors DOWN, so every admissible emitted portion stays inside the scaled
    band. Returns ``(lo, hi, warning)``.

    If grid re-alignment would invert the band (max < min), scaling cannot
    produce a usable band on the emission grid: fall back to the UNSCALED
    bounds (which validation guarantees are unit-aligned) and attach a
    structured ``serve_scale_unaligned`` warning instead of inventing a band.
    """
    lo, hi = comp["serve_g"]["min"], comp["serve_g"]["max"]
    s = person_scale(person)
    if s == 1.0:
        return lo, hi, None
    slo, shi = lo * s, hi * s
    u = comp.get("unit_g") or 1        # emission grid: unit_g, else 1g (int)
    slo = math.ceil(slo / u - 1e-9) * u
    shi = math.floor(shi / u + 1e-9) * u
    if shi < slo:
        return lo, hi, dict(
            code="serve_scale_unaligned", component=comp["id"],
            scale=round(s, 4), bounds=[lo, hi], unit_g=comp.get("unit_g"),
            message=(f"serve bounds [{lo}, {hi}]g of '{comp['id']}' "
                     f"scaled by {s:.2f} leave no whole {u}g unit; "
                     "falling back to the unscaled bounds"))
    return slo, shi, None


def eligible(comp, person):
    return not (set(comp["tags"]) & set(person.get("exclude", [])))


class PlateResult(tuple):
    """(ok, {id: grams}, {macro: miss}) — unpacks exactly like the classic
    3-tuple every caller already uses, and additionally carries structured
    warnings (list of dicts, JSON-ready) as ``.warnings`` (M0.8/M0.13)."""

    def __new__(cls, ok, grams, miss, warnings=None):
        self = tuple.__new__(cls, (ok, grams, miss))
        self.warnings = list(warnings or [])
        return self

    @property
    def ok(self):
        return self[0]

    @property
    def items_g(self):
        return self[1]

    @property
    def miss(self):
        return self[2]


# --------------------------------------------------------------------------- #
#  layer 2 — the plate LP
# --------------------------------------------------------------------------- #
def plate(person, comps, ids, weights=None, tol=None, locked=None,
          allow_out_of_bounds=False):
    """One day, one person. Returns PlateResult(ok, {id: grams}, {macro:
    grams_missed}) — unpacks like the historical 3-tuple, plus ``.warnings``.

    Discrete components (tortillas, meatballs, wings) are handled in two passes:
    solve continuous, snap them to whole units — clamped into serve bounds
    (M0.8; bounds are validated unit-aligned, so the clamp keeps the unit
    grid) — then re-solve the rest. Doing it as a true MILP is correct but
    ~100x slower and the difference is under a tortilla.

    Pinned portions (``locked``, M0.8): each pin is snapped onto the unit grid
    and clamped into serve bounds by default. With
    ``allow_out_of_bounds=True`` the raw pin is honored as given and a
    structured ``pin_out_of_bounds`` warning is attached to the result;
    such pins are excluded from the bounds guarantees. A pin on a component
    the person cannot eat (or one not on the menu) is dropped with a
    structured ``pin_ineligible`` / ``pin_unknown`` warning (M1.0),
    consistent with replate's ``locked_unavailable``.

    Serve bounds are PER PERSON since M1.7 (PRD §8.1): every bound below is
    ``effective_serve_bounds(comp, person)`` — the authored band scaled by
    the person's kcal (provisional curve, ``SCALING``) and re-aligned to the
    unit grid. Structural-failure fallbacks report NEGATIVE (SHORT) misses:
    a person served nothing is short of every target (M1.0).
    """
    tol = person["tolerance"] if tol is None else tol
    weights = dict(weights or {})
    for d in person.get("dislikes") or []:      # SOFT: weighted down, not banned
        weights[d] = weights.get(d, 1.0) * SCORE_WEIGHTS["dislike_multiplier"]
    usable = [i for i in ids if eligible(comps[i], person)]
    if not usable:
        return PlateResult(False, {},
                           {m: -person["targets"][m] for m in MACROS})

    # M1.7: this person's effective serve band per component (+ structured
    # warnings where discrete grid re-alignment had to fall back unscaled)
    warnings = []
    bounds = {}
    for i in usable:
        lo, hi, w = effective_serve_bounds(comps[i], person)
        bounds[i] = (lo, hi)
        if w is not None:
            warnings.append(w)

    def build(fixed=None):
        fixed = fixed or {}
        m = pulp.LpProblem("plate", pulp.LpMinimize)
        g, slack = {}, {}
        for i in usable:
            lo, hi = bounds[i]
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
        m += SCORE_WEIGHTS["plate_slack_dominance"] * pulp.lpSum(
            x for pr in slack.values() for x in pr
        ) + pulp.lpSum(
            weights.get(i, 1.0) * g[i] for i in usable if not isinstance(g[i], (int, float))
        ) * SCORE_WEIGHTS["plate_weight_tiebreak"]
        return m, g, slack

    # ---- pinned portions (M0.8): snap to unit grid + clamp into bounds ------
    seed_fixed = {}
    for k, v in (locked or {}).items():
        if k not in usable:
            # M1.0: never a bare continue — the dropped pin is reported,
            # consistent with replate's locked_unavailable warning
            if k in comps and not eligible(comps[k], person):
                warnings.append(dict(
                    code="pin_ineligible", component=k, pinned_g=float(v),
                    message=(f"pin {v}g on '{k}' dropped: the component "
                             "carries a tag this person excludes")))
            else:
                warnings.append(dict(
                    code="pin_unknown", component=k, pinned_g=float(v),
                    message=(f"pin {v}g on '{k}' dropped: component is not "
                             "on this plate's menu")))
            continue
        c = comps[k]
        lo, hi = bounds[k]
        u = c.get("unit_g")
        adj = float(v)
        if u:
            adj = round(adj / u) * u
        adj = min(hi, max(lo, adj))
        if allow_out_of_bounds:
            if adj != float(v):
                warnings.append(dict(
                    code="pin_out_of_bounds", component=k, pinned_g=float(v),
                    bounds=[lo, hi], unit_g=u,
                    message=(f"pin {v}g on '{k}' is outside serve bounds "
                             f"[{lo}, {hi}]g or off the unit grid; honored "
                             "as requested and excluded from bounds "
                             "guarantees")))
            seed_fixed[k] = float(v)
        else:
            if adj != float(v):
                warnings.append(dict(
                    code="pin_adjusted", component=k, pinned_g=float(v),
                    adjusted_g=adj, bounds=[lo, hi], unit_g=u,
                    message=(f"pin {v}g on '{k}' adjusted to {adj}g "
                             "(clamped into serve bounds / snapped to the "
                             "unit grid)")))
            seed_fixed[k] = adj
    m, g, slack = build(seed_fixed)
    _count_solve()
    if m.solve(pulp.PULP_CBC_CMD(msg=0)) != 1:
        # solver failure: nothing served -> SHORT of every target (M1.0)
        return PlateResult(False, {},
                           {mac: -person["targets"][mac] for mac in MACROS},
                           warnings)

    # snap discrete components to whole units — clamped into serve bounds
    # (M0.8; effective bounds are unit-aligned, so the clamp stays on the
    # grid) — then re-solve the continuous rest
    fixed = dict(seed_fixed)
    for i in usable:
        u = comps[i].get("unit_g")
        if u and i not in fixed:
            val = g[i].value() or 0
            snapped = round(val / u) * u
            if snapped:      # 0 means the component is OFF — never force it on
                lo, hi = bounds[i]
                snapped = min(hi, max(lo, snapped))
            fixed[i] = snapped
    if fixed:
        m, g, slack = build(fixed)
        _count_solve()
        if m.solve(pulp.PULP_CBC_CMD(msg=0)) != 1:
            return PlateResult(False, {},
                               {mac: -person["targets"][mac]
                                for mac in MACROS},
                               warnings)

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
    return PlateResult((not miss), out, miss, warnings)


# --------------------------------------------------------------------------- #
#  doctor — why is it infeasible, and what class of thing is missing
# --------------------------------------------------------------------------- #
def _day_type_views(person):
    """M1.11 (M111_SPEC §7): ``(label_suffix, person_view)`` per DISTINCT
    day-type. A person WITHOUT profiles yields exactly ``[("", person)]``
    — the SAME object and an empty suffix, so the degenerate doctor report
    is byte-identical to pre-M1.11. Every PROFILED person gets one view
    per day-type (the volume_floor dict-copy house pattern) labeled
    ``" — day-type 'lift' (mon, wed, fri)"`` — named gaps, anchor-free by
    construction (per type, not per calendar day).

    The inert branch is gated on the ABSENCE of profiles, never on
    ``len(types) == 1``: a full 7-day week map naming exactly one profile
    (§13/P-3 anticipates authors writing full coverage) also yields a
    single type, but that type is the PROFILE — short-circuiting to
    ``person`` there would diagnose base targets no plan day ever uses and
    silently skip the only day-type the household eats, gutting the §12
    P-1 safety net. A profile may even be *named* "base"; profile presence
    is the only sound gate."""
    if not person.get("target_profiles"):
        return [("", person)]
    out = []
    types = distinct_day_types(person)
    for label, tgts, days in types:
        view = {k: v for k, v in person.items()}
        view["targets"] = tgts
        out.append((f" — day-type '{label}' ({', '.join(days)})", view))
    return out


def binding_macro(person, comps, ids=None, steps=10):
    """WHICH macro binds for THIS person on this library (M0.11, PRD §8.3) —
    the generic capability behind the prototype's household-specific
    "which macro binds first" finding.

    Tighten the tolerance from the person's own setting toward zero on a
    linear grid; at the first tolerance where the plate LP misses, the
    first-missed macro (largest signed miss) is the binding constraint.
    Returns None when the targets are met even at ±0%, else
    ``dict(macro, direction, signed_miss_g, at_tolerance, misses)`` —
    signed_miss_g > 0 means forced OVER target, < 0 means cannot reach it.
    """
    ids = list(comps) if ids is None else ids
    tol0 = person["tolerance"]
    with solve_stage("doctor-binding"):
        for k in range(steps + 1):
            t = tol0 * (steps - k) / steps
            ok, _, miss = plate(person, comps, ids, tol=t)
            if not ok and miss:
                mac = max(miss, key=lambda m: abs(miss[m]))
                return dict(macro=mac, signed_miss_g=miss[mac],
                            direction="over" if miss[mac] > 0 else "short",
                            at_tolerance=round(t, 4), misses=dict(miss))
    return None


def volume_floor(person, comps, ids=None, lo=500, hi=8000, res=25):
    """Minimum daily food mass the person's targets require (M0.11, PRD §8.3)
    — the generic form of the prototype's reproduced volume-floor finding.

    Bisects ``max_daily_mass_g`` over [lo, hi] to ``res``-gram resolution at
    the person's own tolerance. Returns ``dict(floor_g, binding, searched,
    note)``:
    - floor_g: smallest cap on the grid that stays feasible; None when even
      ``hi`` is infeasible (not a volume problem); ``lo`` when the bottom of
      the search range is already feasible (floor at or below ``lo``);
    - binding: which macro misses just below the floor (dict(macro,
      signed_miss_g, direction)) — None when there is no infeasible point
      below the floor inside the range.
    """
    ids = list(comps) if ids is None else ids

    def solve(cap):
        p2 = {k: v for k, v in person.items()}
        p2["max_daily_mass_g"] = cap
        with solve_stage("doctor-volume"):
            return plate(p2, comps, ids)

    lo0, hi0 = lo, hi
    if not solve(hi)[0]:
        return dict(floor_g=None, binding=None, searched=[lo0, hi0],
                    note=f"infeasible even at {hi0}g/day — "
                         "not a volume problem")
    if solve(lo)[0]:
        return dict(floor_g=lo, binding=None, searched=[lo0, hi0],
                    note=f"feasible at the {lo0}g search bottom — the floor "
                         f"is at or below {lo0}g")
    while hi - lo > res:
        mid = lo + ((hi - lo) // 2 // res) * res
        if mid == lo:
            break
        if solve(mid)[0]:
            hi = mid
        else:
            lo = mid
    _, _, miss = solve(lo)
    binding = None
    if miss:
        mac = max(miss, key=lambda m: abs(miss[m]))
        binding = dict(macro=mac, signed_miss_g=miss[mac],
                       direction="over" if miss[mac] > 0 else "short")
    return dict(floor_g=hi, binding=binding, searched=[lo0, hi0], note=None)


def lean_coverage(comps, settings, ing=None, ids=None):
    """The REAL shelf-life stagger check (M0.11): per-day lean-anchor
    availability from cook_days + each anchor's keeps_days (+freezer
    bridging when enabled, via available_on). Pass ``ing`` to also apply the
    raw-freshness rule (M0.6). Returns ``dict(lean_anchors, per_day,
    uncovered_days)`` — uncovered_days are the days with ZERO lean-anchor
    availability: the hole in the middle of the week the prototype's
    boolean expression never actually checked for."""
    ids = list(comps) if ids is None else ids
    days = settings["days"]
    leans = [i for i in ids if comps[i].get("anchor") == "lean"]
    per_day = {d: [i for i in leans
                   if available_on(comps[i], d, settings, ing)]
               for d in range(days)}
    return dict(lean_anchors=leans, per_day=per_day,
                uncovered_days=[d for d in range(days) if not per_day[d]])


def carb_headroom(person, comps, settings, ids=None, ing=None):
    """Per-day carb availability vs this person's target (M0.11, PRD §8.3):
    for each day, sum serve_max x carb_per100/100 over that day's AVAILABLE,
    eligible components. Replaces the prototype's flat 1.45x whole-library
    fudge, which ignored that short-keeping starches are gone mid-week.
    Returns ``dict(target_g, per_day, worst_day, worst_headroom_g, ok)`` —
    ok iff even the worst day's headroom clears the target."""
    ids = list(comps) if ids is None else ids
    days = settings["days"]
    elig = [i for i in ids if eligible(comps[i], person)]
    tgt = person["targets"]["carb"]
    per_day = []
    for d in range(days):
        # M1.7: headroom is against the PERSON's effective serve max — the
        # scaled band is what the plate LP will actually allow them
        h = sum(effective_serve_bounds(comps[i], person)[1]
                * comps[i]["per100"]["carb"] / 100
                for i in elig if available_on(comps[i], d, settings, ing))
        per_day.append(dict(day=d, headroom_g=round(h, 1)))
    worst = min(per_day, key=lambda r: r["headroom_g"]) if per_day else None
    return dict(target_g=tgt, per_day=per_day,
                worst_day=worst["day"] if worst else None,
                worst_headroom_g=worst["headroom_g"] if worst else 0.0,
                ok=bool(worst) and worst["headroom_g"] >= tgt)


def doctor(comps, people, settings, ing=None, dishes=None):
    """Diagnostics (PRD §8.3). Returns ``(text, data)``: the rendered
    markdown report AND a structured, JSON-ready mirror of every section
    (M0.11). Sections: raw freshness (M0.6, when ``ing`` given), per-person
    feasibility, binding macro, volume floor, structural ablation,
    lean-anchor coverage, carb headroom.

    ``dishes`` (M1.13): pass the loaded dish map and an ARITHMETIC-ONLY
    dish section is appended (zero LP — dishes.doctor_dish_section):
    eligibility kills, per-day dish availability with killers named,
    lean-dish coverage, dish carb headroom, slot_target_unreachable. With
    ``dishes=None`` (no dishes.yaml — heritage mode) the output is
    byte-identical to pre-M1.13.

    M1.11 (M111_SPEC §7): the per-person targets-bearing checks
    (feasibility, binding macro, volume floor, carb headroom, and the
    targets-bearing dish reachability checks) run once per DISTINCT
    day-type via ``_day_type_views`` — anchor-free (per type, never per
    calendar day; the doctor needs NO date). Rendered lines and the
    structured-mirror keys carry ``pname — day-type 'lift' (mon, wed,
    fri)`` labels; a no-profile person degenerates to one unlabeled pass,
    byte-identical to pre-M1.11."""
    lines = []
    ids = list(comps)
    data = {}
    # ---- M0.6: raw freshness — which sessions can actually cook what -------
    if ing is not None:
        rf = []
        lines.append("## Raw freshness\n")
        lines.append(f"- shopping trips on days {shop_days_for(settings)}; "
                     f"cook sessions on days {sessions_for(settings)}")
        clean = True
        for cid, c in comps.items():
            for k, start in enumerate(sessions_for(settings)):
                ok, blockers, _ = raw_freshness(c, start, settings, ing)
                if ok:
                    continue
                clean = False
                rf.append(dict(component=cid, session=k, day=start,
                               blockers=blockers))
                for b in blockers:
                    if b["shop_day"] is None:
                        why = "no shopping trip happens before the session"
                    else:
                        why = (f"'{b['ingredient']}' bought day "
                               f"{b['shop_day']} keeps {b['keeps_days']}d raw "
                               f"— {start - b['shop_day']} days old at cook "
                               "time")
                    lines.append(
                        f"- **{cid}** cannot be cooked in session {k} "
                        f"(day {start}): {why}. Add a shopping trip, move "
                        "the session, or mark the ingredient freezable.")
        if clean:
            lines.append("- every component is cookable in every session")
        lines.append("")
        data["raw_freshness"] = rf
    lines.append("## Feasibility\n")
    data["feasibility"] = {}
    # M1.11 (M111_SPEC §7): every per-person targets-bearing check below
    # runs once per DISTINCT day-type via _day_type_views — named gaps
    # ("day-type 'lift' (mon, wed, fri)"), anchor-free, O(distinct types).
    # A single-type person degenerates to today's one pass, byte-identical
    # (the view IS the person and the label suffix is empty). Structured
    # mirrors key by the same label, so single-type keys stay the bare
    # person name and every value keeps its historical shape.
    for pname, p in people.items():
        for suffix, pv in _day_type_views(p):
            label = pname + suffix
            with solve_stage("doctor-feasibility"):
                ok, pl, miss = plate(pv, comps, ids)
            elig = [i for i in ids if eligible(comps[i], pv)]
            blocked = [i for i in ids if not eligible(comps[i], pv)]
            entry = dict(ok=bool(ok), miss=dict(miss), eligible=len(elig),
                         total=len(ids), blocked=blocked,
                         clears_at_tolerance=None)
            t = pv["targets"]
            lines.append(
                f"**{label}** — {t['carb']}c / {t['fat']}f / {t['protein']}p "
                f"({kcal_of(t):.0f} kcal), excludes {pv.get('exclude') or 'nothing'}"
            )
            lines.append(f"- {len(elig)}/{len(ids)} components eligible"
                         + (f"; blocked: {', '.join(blocked)}" if blocked else ""))
            if ok:
                lines.append(f"- **feasible** on the full library (±{pv['tolerance']:.0%})")
            else:
                lines.append(f"- **INFEASIBLE** — misses "
                             + fmt_miss(miss))
                for t2 in (0.08, 0.10, 0.15):
                    with solve_stage("doctor-feasibility"):
                        cleared = plate(pv, comps, ids, tol=t2)[0]
                    if cleared:
                        lines.append(f"  - would clear at ±{t2:.0%} tolerance")
                        entry["clears_at_tolerance"] = t2
                        break
                else:
                    lines.append("  - does not clear even at ±15%. This is a library gap, "
                                 "not a tolerance problem.")
            lines.append("")
            data["feasibility"][label] = entry

    # ---- M0.11: which macro binds, per person (per day-type, M1.11) --------
    lines.append("## Binding macro\n")
    data["binding_macro"] = {}
    for pname, p in people.items():
        for suffix, pv in _day_type_views(p):
            label = pname + suffix
            bm = binding_macro(pv, comps, ids)
            data["binding_macro"][label] = bm
            if bm is None:
                lines.append(f"- **{label}**: no macro binds — targets are met "
                             "even at ±0% tolerance")
            else:
                word = "forced OVER target" if bm["signed_miss_g"] > 0 \
                    else "unreachable (SHORT)"
                lines.append(
                    f"- **{label}**: **{bm['macro']}** binds — tightening "
                    f"tolerance to ±{bm['at_tolerance']:.1%} first makes "
                    f"{bm['macro']} {word} by {abs(bm['signed_miss_g'])}g")
    lines.append("")

    # ---- M0.11: volume floor (per day-type, M1.11) -------------------------
    lines.append("## Volume floor\n")
    data["volume_floor"] = {}
    for pname, p in people.items():
        for suffix, pv in _day_type_views(p):
            label = pname + suffix
            vf = volume_floor(pv, comps, ids)
            data["volume_floor"][label] = vf
            if vf["floor_g"] is None:
                lines.append(f"- **{label}**: {vf['note']}")
                continue
            msg = (f"- **{label}**: minimum daily food mass ≈ "
                   f"**{vf['floor_g']}g** ({vf['floor_g'] / 453.6:.1f} lb)")
            b = vf["binding"]
            if b:
                msg += (f" — just below the floor, {b['macro']} goes "
                        f"{abs(b['signed_miss_g'])}g "
                        + ("OVER" if b["signed_miss_g"] > 0 else "SHORT"))
            if vf["note"]:
                msg += f" ({vf['note']})"
            lines.append(msg)
    lines.append("")

    # the structural check that actually matters
    lines.append("## Structural check\n")
    leans = [i for i, c in comps.items() if c.get("anchor") == "lean"]
    data["structural"] = dict(lean_anchors=leans, ablation={})
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
        with solve_stage("doctor-ablation"):
            for k in range(1, len(leanness)):
                trimmed = [i for i in comps if i not in leanness[:k]]
                ok, _, miss = plate(p, comps, trimmed)
                if not ok:
                    broke = (k, miss)
                    break
        if broke:
            k, miss = broke
            data["structural"]["ablation"][pname] = dict(
                min_lean_mains=k, miss=dict(miss))
            lines.append(
                f"  - **{pname}** survives losing the {k-1} leanest main(s); removing "
                f"`{leanness[k-1]}` too makes the week infeasible ("
                + fmt_miss(miss)
                + f"). So {pname} needs at least **{k}** lean-ish protein(s) on the menu."
            )
        else:
            data["structural"]["ablation"][pname] = dict(min_lean_mains=None,
                                                         miss={})
            lines.append(f"  - **{pname}** stays feasible no matter which mains are cut "
                         "(their target is loose relative to this library).")
    dense = [i for i, c in comps.items() if c["per100"]["kcal"] >= 250]
    data["structural"]["dense_carriers"] = len(dense)
    lines.append(f"- calorie-dense carriers (>=250 kcal/100g): {len(dense)}")

    # ---- M0.11: lean-anchor coverage (the REAL stagger check) --------------
    lines.append("\n## Lean-anchor coverage\n")
    lc = lean_coverage(comps, settings, ing=ing, ids=ids)
    data["lean_coverage"] = lc
    lines.append(f"- cook days {sessions_for(settings)}; lean anchors: "
                 + (", ".join(f"{i} (keeps {comps[i]['keeps_days']}d)"
                              for i in lc["lean_anchors"]) or "NONE"))
    if lc["uncovered_days"]:
        lines.append("- **days with ZERO lean-anchor availability: "
                     + ", ".join(str(d) for d in lc["uncovered_days"])
                     + "** — stagger keeps_days, move a cook day, or freeze "
                       "half a batch on cook day")
    else:
        lines.append("- every day has at least one lean anchor available")

    # ---- M0.11: carb headroom from availability (per day-type, M1.11) ------
    lines.append("\n## Carb headroom\n")
    data["carb_headroom"] = {}
    for pname, p in people.items():
        for suffix, pv in _day_type_views(p):
            label = pname + suffix
            ch = carb_headroom(pv, comps, settings, ids=ids, ing=ing)
            data["carb_headroom"][label] = ch
            flag = "OK" if ch["ok"] else "**SHORT** — add a longer-keeping " \
                                         "starch or move a cook day"
            lines.append(f"- **{label}**: worst day is day {ch['worst_day']} — "
                         f"{ch['worst_headroom_g']:.0f}g carb available vs "
                         f"{ch['target_g']}g target: {flag}")

    # ---- M1.9: meal-layer dealability (arithmetic only, ZERO LP solves) ----
    # Meal-layer infeasibility is explained at the layer it occurs (P6),
    # before a full build. Section appears only when somebody configures
    # meals — the layer is inert otherwise (byte-identical doctor output).
    # M1.13: in dish mode the meal layer's dealability arithmetic is
    # re-based on dishes (the dish section below carries per-day dish
    # availability + slot capacity) — the component-pool numbers would
    # answer a question the dish pipeline never asks.
    meal_slots = {pn: resolve_meal_slots(p) for pn, p in people.items()}
    if not dishes and any(meal_slots.values()):
        from .meals import MEAL_WEIGHTS
        lines.append("\n## Meal layer\n")
        data["meal_layer"] = {}
        days = settings["days"]
        floor_g = MEAL_WEIGHTS["MIN_SUBPORTION_G"]
        for pname, slots in meal_slots.items():
            if not slots:
                continue
            p = people[pname]
            n = len(slots)
            elig = [i for i in ids if eligible(comps[i], p)]
            per_day_mains, per_day_side = [], []
            for d in range(days):
                av = [i for i in elig
                      if available_on(comps[i], d, settings, ing)]
                per_day_mains.append(
                    len({i for i in av if comps[i]["role"] == "main"}))
                per_day_side.append(round(sum(
                    effective_serve_bounds(comps[i], p)[1]
                    for i in av
                    if comps[i]["role"] in ("starch", "veg")), 1))
            worst_mains = min(per_day_mains) if per_day_mains else 0
            worst_side = min(per_day_side) if per_day_side else 0.0
            need_side = n * floor_g
            variety_days = sum(1 for m in per_day_mains if m < n)
            entry = dict(slots=n, worst_day_mains=worst_mains,
                         worst_day_side_mass_g=worst_side,
                         side_mass_needed_g=need_side,
                         expected_variety_unmet_days=variety_days)
            data["meal_layer"][pname] = entry
            lines.append(
                f"- **{pname}** ({n} meals/day): worst-day distinct mains "
                f"{worst_mains} vs {n} needed"
                + (" — **mains repeat across slots "
                   f"on {variety_days} day(s)** (variety_unmet expected)"
                   if variety_days else " — OK")
                + f"; worst-day splittable side mass {worst_side:.0f}g vs "
                  f"{need_side}g needed"
                + (" — OK" if worst_side >= need_side
                   else " — **SHORT**: meals will lean on carved "
                        "sub-portions or ship flagged"))

    # ---- M1.13: dish section (arithmetic only, ZERO LP — P6). Appears
    # only in dish mode; heritage doctor output stays byte-identical.
    # Lazy import: dishes.py imports from this module.
    if dishes:
        from .dishes import doctor_dish_section
        dlines, ddata = doctor_dish_section(comps, people, settings, dishes,
                                            ing=ing)
        lines.extend(dlines)
        data["dish_layer"] = ddata
    return "\n".join(lines), data


# --------------------------------------------------------------------------- #
#  layer 1 — menu selection (shared perishables, variety, time budget)
# --------------------------------------------------------------------------- #
def score_menu(comps, ing, chosen, settings, people=None, score_weights=None):
    """CHEAP score — no LP. Structural proxies only, so local search can run hot.
    Actual macro feasibility is verified separately, on the shortlist.

    M0.10: every estimate in the score — waste, time, cost — is computed at
    the ONE estimated-batch scale (prototype plan.py:341 computed the
    perishable-waste term at 1 batch/component while cost used
    estimate_batches; waste was constant in demand). Without ``people``
    there is no demand to estimate from and the scale is 1 batch throughout.

    PRD §8.3: every penalty/reward constant is a named PROVISIONAL weight in
    ``SCORE_WEIGHTS``; ``score_weights`` merges overrides on top.
    """
    W = dict(SCORE_WEIGHTS)
    W.update(score_weights or {})
    batches = (estimate_batches(comps, people, settings, chosen)
               if people else {i: 1 for i in chosen})
    rows, wp, _ = purchase(comps, ing, chosen, batches)
    active = cook_minutes(comps, settings, batches)
    cuisines = len({comps[i]["cuisine"] for i in chosen})
    roles = {}
    for i in chosen:
        roles[comps[i]["role"]] = roles.get(comps[i]["role"], 0) + 1
    pen = 0
    # over the hands-on time budget: hard, per minute
    pen += max(0, active - settings["active_min_budget"]) \
        * W["time_over_budget_per_min"]
    pen += 0 if roles.get("main", 0) >= 3 else W["role_floor_mains"]
    pen += 0 if roles.get("starch", 0) >= 2 else W["role_floor_starches"]
    pen += 0 if roles.get("accent", 0) >= 2 else W["role_floor_accents"]
    pen -= cuisines * W["cuisine_variety_reward"]      # reward variety

    # M0.6 raw freshness: a menu whose components need cook sessions their
    # raw ingredients cannot survive to is structurally broken — availability
    # shrinks and the back of the week starves. Penalize per blocked
    # (component, session) pair. Weight is provisional (P9).
    n_sessions = len(sessions_for(settings))
    for i in chosen:
        pen += W["uncookable_session_pair"] * (
            n_sessions - len(cookable_sessions(comps[i], settings, ing)))

    # BUDGET. Not hardcoded — read from library/people.yaml or the CLI. Over the
    # ceiling is penalised steeply; under it, cheaper is mildly better so the search
    # does not burn budget it did not need.
    cap = budget_ceiling(settings, people or {})
    est = menu_cost(comps, ing, chosen, batches=batches)   # same scale as waste
    if cap:
        pen += max(0, est - cap) * W["budget_overage_per_dollar"]
    pen += est * W["cost_per_dollar"]

    # Per-person structural proxies. These are what actually predict LP feasibility,
    # and they cost nothing to evaluate.
    if people:
        days_n = settings["days"]
        # per-day availability is menu-wide and person-independent (cook_days
        # + keeps_days + freezer bridging + raw freshness) — compute it once
        avail = {i: [available_on(comps[i], d, settings, ing)
                     for d in range(days_n)] for i in chosen}
        for p in people.values():
            elig = [i for i in chosen if eligible(comps[i], p)]
            mains = [i for i in elig if comps[i]["role"] == "main"]
            starch = [i for i in elig if comps[i]["role"] == "starch"]
            acc = [i for i in elig if comps[i]["role"] in ("accent", "veg")]
            pen += 0 if len(mains) >= 3 else W["person_mains_floor"]
            # >=3 starches, not 2. Discovered the hard way: on the day before
            # a cook session some starches have expired, and a high daily
            # carb target cannot be met from two starches at realistic
            # serving sizes.
            pen += 0 if len(starch) >= 3 else W["person_starch_floor"]
            # CARB HEADROOM, day-correct (M0.11). For high-kcal targets the
            # binding constraint is usually carbs, not fat: the target has to
            # be physically eatable. For every day, the ceiling is what is
            # actually AVAILABLE that day (the person's effective serve max
            # x carb/100 over the day's eligible survivors — scaled per
            # person since M1.7) — the worst day must clear the target
            # outright. Replaces the prototype's flat whole-library fudge
            # multiplier, which ignored that the short-keeping starches are
            # gone by mid-week.
            worst_carb = min(
                sum(effective_serve_bounds(comps[i], p)[1]
                    * comps[i]["per100"]["carb"] / 100
                    for i in elig if avail[i][d])
                for d in range(days_n))
            pen += 0 if worst_carb >= p["targets"]["carb"] \
                else W["carb_headroom_shortfall"]
            # everyone needs at least one accent they can actually eat — that's the
            # per-plate fine-tuning lever. An accent only ONE of you can eat is not
            # waste, it's the entire point of separating accents out.
            pen += 0 if len(acc) >= 2 else W["person_accents_floor"]
            # need enough protein-per-fat headroom to reach protein before fat caps out
            need_ratio = p["targets"]["protein"] / max(p["targets"]["fat"], 1)
            lean = [i for i in mains
                    if comps[i]["per100"]["protein"] / max(comps[i]["per100"]["fat"], .1)
                    >= need_ratio * 1.25]
            # doctor's ablation says one lean main is not enough. And they must not all
            # expire together, or you get a hole in the middle of the week.
            # M0.5: the floor is settings.min_lean_anchors (default 2 — the
            # effective prototype behavior; its yaml said 1 but the code
            # hardcoded 2, and matching behavior wins).
            pen += 0 if len(lean) >= settings["min_lean_anchors"] \
                else W["lean_anchor_floor"]
            # REAL stagger check (M0.11): the prototype's boolean expression
            # here (plan.py:397-398 — len(set-of-bools), a no-op) is replaced
            # by actual coverage: every day on which ZERO of this person's
            # lean mains is available is a hole in the middle of the week.
            # Per-day weight is PROVISIONAL (P9).
            uncovered = sum(1 for d in range(days_n)
                            if not any(avail[i][d] for i in lean))
            pen += W["uncovered_lean_day"] * uncovered
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
            outside = [i for i in ids if i not in sel]
            # n == len(comps): the menu IS the whole library — nothing to
            # swap in, so phase 1 is done (M1.4 minor A's neighbor: the
            # just-fits case must work, never an empty-sequence traceback)
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

    ranked = sorted(seen.items(), key=lambda kv: kv[1])[:shortlist]

    # ---- phase 2: verify best-first ------------------------------------------
    first_fail = None
    with solve_stage("menu-verify"):
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


def available_on(comp, day, settings, ing=None):
    """Is a batch of ``comp`` edible on ``day``? True iff SOME cook session
    both reaches the day (0 <= day - start < keeps_days) and — when
    ingredient data is supplied — can actually cook the component under the
    raw-freshness rule (M0.6): a session whose perishable, non-freezable
    ingredients are already past raw keeps_days does not produce it.

    M0.5 freezer bridging (PRD §8.1/§8.2): if the component freezes and
    settings.use_freezer (default true), a batch cooked in ANY earlier
    cookable session remains usable — the freezer bridges past cooked
    keeps_days. Such days are marked "from freezer — thaw ahead" in
    week/cook output (see ``from_freezer``). With use_freezer false or
    freezes false/absent, the strict shelf-life rule applies unchanged."""
    bridged = freezer_bridges(comp, settings)
    for start in sessions_for(settings):
        if start > day:
            continue
        if (day - start) >= comp["keeps_days"] and not bridged:
            continue
        if ing is not None and not raw_freshness(comp, start, settings,
                                                 ing)[0]:
            continue
        return True
    return False


def from_freezer(comp, day, settings, ing=None):
    """Is ``day`` served ONLY via the freezer bridge (M0.5)? True iff the
    component is available on ``day`` but no cookable session's batch is
    still within cooked shelf life — i.e. the serving comes out of the
    freezer and the eater must thaw ahead. Used to mark week output."""
    for start in sessions_for(settings):
        if start <= day and (day - start) < comp["keeps_days"]:
            if ing is not None and not raw_freshness(comp, start, settings,
                                                     ing)[0]:
                continue
            return False               # a fresh batch covers this day
    return available_on(comp, day, settings, ing)


def replate(person, comps, menu, day, settings, locked=None, weights=None,
            tol=None, allow_out_of_bounds=False, ing=None, anchor=None):
    """Re-solve ONE person's ONE day, day-correctly (M0.13).

    ``anchor`` (M1.11): plan-date weekday anchor — the day re-solves
    against the person's RESOLVED day targets (``model.person_for_day``,
    identity for a no-profile person).

    The menu is filtered by ``available_on(comp, day, settings)`` BEFORE
    solving — the v1 prototype's ``serve.py`` replate ignored availability
    entirely, so a rebalanced day 7 could serve components that expired on
    day 3. A lock on a component that is not available on ``day`` (expired,
    not yet cooked, or simply unknown) is dropped with a structured
    ``locked_unavailable`` warning — never honored, never a crash.

    Pass ``ing`` to also apply the raw-freshness rule (M0.6): days only
    reachable from sessions that cannot cook a component are unavailable.

    Returns PlateResult; ``.warnings`` carries both the dropped-lock warnings
    and any pin warnings from the underlying plate solve.
    """
    person = person_for_day(person, day, anchor)   # M1.11: day-view person
    warnings = []
    avail = [i for i in menu
             if i in comps and available_on(comps[i], day, settings, ing)]
    kept = {}
    for k, v in (locked or {}).items():
        if k in avail:
            kept[k] = v
        else:
            warnings.append(dict(
                code="locked_unavailable", component=k, day=day, pinned_g=v,
                message=(f"lock of {v}g on '{k}' dropped: component is not "
                         f"available on day {day}")))
    with solve_stage("replate"):
        res = plate(person, comps, avail, weights=weights, tol=tol,
                    locked=kept, allow_out_of_bounds=allow_out_of_bounds)
    return PlateResult(res[0], res[1], res[2], warnings + res.warnings)


def build_week(comps, people, settings, menu, seed=0, ing=None, diag=None,
               leftovers=None, anchor=None):
    """Day by day, because a component's eligibility depends on WHICH day it is.
    Guacamole keeps 2 days; it cannot be on the day-7 plate no matter how well
    the macros work out.

    Raw freshness (M0.6): pass ``ing`` and availability also drops the days
    only reachable from sessions that cannot cook the component (perishable,
    non-freezable ingredients past raw keeps_days at cook time) — an
    uncookable session simply doesn't produce it.

    Deterministic (M0.3): per-day plate seeds derive from the explicit ``seed``
    and the sorted index of the person name — never from the builtin hash,
    which PYTHONHASHSEED randomizes across interpreter runs.

    Diagnostics: pass a dict as ``diag`` and it is populated with
    ``diag["relax_tiers"][pname]`` — one entry per day: the index of the
    relaxation-ladder tier that produced the day's plate (0 = strict caps,
    1/2 = progressively relaxed, None = no tier fed the day, i.e. an
    explained hole). Lets tests and doctors observe WHETHER the caps had to
    be relaxed, which the served week alone cannot show.

    Cooked leftovers (M1.8, PRD §8.1): pass ``leftovers`` — the entries
    ``costing.cooked_leftovers`` computes from the pantry's cooked list —
    and each component is ALSO available on the plan days its leftover's
    residual life covers; session_plan then consumes those grams before any
    fresh batch (economy: leftovers are already paid for).

    ``anchor`` (M1.11, M111_SPEC §5): plan-date weekday anchor. Each day's
    plate solves against the DAY-VIEW person (``model.person_for_day`` —
    the injection point), so every inner ``person["targets"]`` read is
    the resolved day-type map. Identity for a no-profile person: the
    pipeline stays byte-identical."""
    # M1.9 §3.5 picker nudge: for a person WITH a meal structure, candidate
    # plates that deal well (enough distinct mains, enough splittable side
    # mass) are preferred among the already-solved candidates. Zero-LP —
    # selection only; the term contributes 0 when meals are unset, so the
    # no-meals pipeline stays byte-identical. Lazy import: meals.py imports
    # effective_serve_bounds from this module.
    from .meals import dealability_penalty
    days = settings["days"]
    cap_batches = settings["max_batches_per_component"]
    porder = {pn: k for k, pn in enumerate(sorted(people))}
    leftover_days = {}
    for e in (leftovers or []):
        leftover_days.setdefault(e["component"], set()).update(e["days"])
    weeks, demand = {}, {}
    for pname, p in people.items():
        slots = resolve_meal_slots(p)
        slots_n = len(slots) if slots else 0
        wk, used_days, used_g = [], {}, {}
        for d in range(days):
            # M1.11 injection point (#8): the day-view person feeds
            # diverse_plates/plate — identity when the layer is inert
            p_day = person_for_day(p, d, anchor)
            fresh = [i for i in menu
                     if available_on(comps[i], d, settings, ing)
                     or d in leftover_days.get(i, ())]

            def pool(cap_days, cap_b):
                # The variety cap belongs on MAINS and sauces. Nobody gets bored of
                # rice. Capping starch the same way starves the back half of the week.
                return [i for i in fresh
                        if (comps[i]["role"] != "main"
                            or used_days.get(i, 0) < cap_days)
                        and used_g.get(i, 0) < comps[i]["yield_g"] * cap_b]

            best, tier_used = None, None
            # try strict first, then progressively relax rather than emit an empty day
            for tier, (cd, cb) in enumerate((
                    (settings["max_days_same_component"], cap_batches),
                    (settings["max_days_same_component"] + 1, cap_batches + 1),
                    (days, cap_batches + 2))):
                avail = pool(cd, cb)
                mains_avail = len({i for i in avail
                                   if comps[i]["role"] == "main"}) \
                    if slots_n else 0
                bestsc = None
                for pl in diverse_plates(p_day, comps, avail, k=10,
                                         seed=(seed * 1009 + porder[pname] * 101
                                               + d * 31) % 9973):
                    sc = sum(used_days.get(c, 0) ** 2 for c in pl)
                    if slots_n:
                        sc += dealability_penalty(pl, comps, slots_n,
                                                  mains_avail)
                    if bestsc is None or sc < bestsc:
                        bestsc, best = sc, pl
                if best:
                    tier_used = tier
                    break
            if best is None:
                best = {}
            if diag is not None:
                diag.setdefault("relax_tiers", {}).setdefault(
                    pname, []).append(tier_used)
            wk.append(best)
            for c, gmz in best.items():
                used_days[c] = used_days.get(c, 0) + 1
                used_g[c] = used_g.get(c, 0) + gmz
                demand[c] = demand.get(c, 0) + gmz
        weeks[pname] = wk
    return weeks, demand
