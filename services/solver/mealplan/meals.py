"""meals.py — the M1.9 meal layer: post-solve dealer (M19_SPEC, ratified).

THE one canonical meal computation (P10): ``deal_day`` deals one person's
already-solved day plate into n composed meals. Zero LP solves, RNG-free,
pure Python. The day solve's authority is absolute: the dealer never
re-solves, never mutates the plate, and conserves it gram-for-gram — it can
never make a day wrong, only make meals uneven, and unevenness is always a
structured flag (P6/P8). Meals ALWAYS ship: no unallocated pools, no holes
invented by this layer, nothing silently widened, dropped, or relaxed.

Determinism (M19_SPEC §8): no ``seed`` parameter by design — every ordering
is an explicit sort key and every scan runs in fixed order, so the same
inputs produce byte-identical output with zero solver tie-breaking surface.

Algorithm (M19_SPEC §3): precheck regime notes → Phase A skeleton deal
(atomic mains/accents anchored; splittable starch/veg pools carved for the
side guarantee) → Phase B greedy quantum allocation + bounded
first-improvement polish → flags (band_unmet, sub_serving, ...).

Escape hatch (M19_SPEC §10) — the documented MILP upgrade, behind this same
``deal_day`` signature, if a real M1.6-class week shows ``band_unmet`` on
more than ``BAND_ESCALATION_THRESHOLD`` of person-days or chronic
``composition_unsatisfiable``: a per-person-day MILP with hard conservation
``Σ_s x[c,s] == G[c]``, composition binaries ``y[c,s]`` (main-anchor and
side floors per slot), min-split lower bounds ``x[c,s] >= MIN_SUBPORTION_G ·
y[c,s]`` on splittables, soft slack-dominant per-meal macro bands (slack
weights >> tie-breaks, mirroring plate()), seeded epsilon tie-breaks for
determinism, one tiny CBC solve per person-day tagged ``meal-alloc``. The
decision to escalate is data-forced (flag counts as receipts), never vibes.
"""

from .engine import effective_serve_bounds
from .model import person_for_day, resolve_meal_slots
from .units import MACROS, kcal_of

# --------------------------------------------------------------------------- #
#  MEAL_WEIGHTS registry (M19_SPEC §5) — mirrors SCORE_WEIGHTS (engine.py):
#  named, documented, override-mergeable via deal_day(config=...); every
#  value PROVISIONAL (P9), never inline.
# --------------------------------------------------------------------------- #
MEAL_WEIGHTS = {
    # soft per-meal band, fraction of T_m/n per macro (0.25–0.35 considered;
    # ratify at M1.6)
    "MEAL_BAND": 0.30,                   # provisional (P9)
    # floor for a carved splittable sub-portion (no rice dust)
    "MIN_SUBPORTION_G": 40,              # provisional (P9)
    # Phase-B allocation step for non-discrete pools
    "QUANTUM_G": 25,                     # provisional (P9)
    # local-search bound
    "POLISH_MAX_ITERS": 200,             # provisional (P9)
    # acceptable within-slot cross-day spread, fraction of slot target
    "INTERCHANGE_EPSILON": 0.10,         # provisional (P9)
    # §3.5 picker-nudge weights (scale of build_week's used_days² score)
    "dealability_mains": 500,            # provisional (P9)
    "dealability_side_mass": 300,        # provisional (P9)
    # §10 escalation criterion: fraction of person-days with band_unmet in a
    # real week that forces the MILP decision onto the owner's desk
    "BAND_ESCALATION_THRESHOLD": 0.20,   # provisional (P9)
}

ATOMIC_ROLES = ("main", "accent", "drink")
SPLITTABLE_ROLES = ("starch", "veg")

# epsilon tie-break weight: within-band smoothness never outweighs a real
# band violation (J's violation terms are in grams; smoothness is scaled down)
_SMOOTH_EPS = 1e-6


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _grid(comp):
    """Emission grid: unit_g for discrete components, whole grams otherwise
    (plate() emits int grams — the same grid it solved on)."""
    return comp.get("unit_g") or 1


def _split_on_grid(g, u, n):
    """Split ``g`` grams across ``n`` bins round-robin on grid ``u``:
    returns int-gram list summing EXACTLY to g (conservation), earlier bins
    get the remainder units (deterministic)."""
    units = int(round(g / u))
    out = []
    for k in range(n):
        uk = units // n + (1 if k < units % n else 0)
        out.append(int(round(uk * u)))
    drift = g - sum(out)
    if drift:                       # float-grid rounding: pin onto the plate
        out[0] += drift
    return out


def _macros_of(items, comps):
    """Macro vector of a meal's items ({cid: g}) via per100 (Atwater kcal)."""
    tot = {m: 0.0 for m in MACROS}
    for cid, g in items.items():
        per = comps[cid]["per100"]
        for m in MACROS:
            tot[m] += g * per[m] / 100
    tot["kcal"] = kcal_of(tot)
    return tot


def _deficit(meal_macros, targets):
    """Largest normalized macro deficit of a meal vs its slot target —
    Phase B routes each quantum to the hungriest meal by this key."""
    return max((targets[m] - meal_macros[m]) / max(targets[m], 1e-9)
               for m in MACROS)


def _add(items, cid, g):
    if g:
        items[cid] = items.get(cid, 0) + g


# --------------------------------------------------------------------------- #
#  the dealer
# --------------------------------------------------------------------------- #
def deal_day(person, comps, day_plate, slots, *, config=None, day=None):
    """Deal one solved day plate into ``len(slots)`` composed meals.

    - ``person``: Person (dict-view) or plain mapping with targets/exclude.
    - ``comps``: component map (role, per100, unit_g, cuisine, serve_g,
      optional pairs_with).
    - ``day_plate``: the solved ``{component_id: int_grams}`` exactly as
      build_week emits it. AUTHORITATIVE — never modified, conserved
      gram-for-gram (hard assert).
    - ``slots``: resolved slot list (model.resolve_meal_slots).
    - ``config``: optional overrides merged over MEAL_WEIGHTS.
    - ``day``: optional day index, carried into notes/flags for context.

    Returns a MealDay dict (M19_SPEC §4): ``{"meals": [...], "notes":
    [...]}`` — meals ordered by slot, each ``{slot, serving_model, items,
    macros, target, flags}``. JSON-ready; every failure mode is a
    structured note/flag, never a crash, never silent.
    """
    W = dict(MEAL_WEIGHTS)
    W.update(config or {})
    n = len(slots)
    targets = {m: person["targets"][m] / n for m in MACROS}
    day_plate = dict(day_plate or {})

    meals = [dict(slot=s["name"], serving_model=s["serving_model"],
                  items={}, flags=[]) for s in slots]
    notes = []

    # ---- §3.0 precheck (pure arithmetic) — the declared regime -------------
    mains = sorted(((cid, g) for cid, g in day_plate.items()
                    if comps[cid]["role"] == "main"),
                   key=lambda t: (-t[1], t[0]))
    pools = sorted(((cid, g) for cid, g in day_plate.items()
                    if comps[cid]["role"] in SPLITTABLE_ROLES),
                   key=lambda t: (-t[1], t[0]))
    extras = sorted(((cid, g) for cid, g in day_plate.items()
                     if comps[cid]["role"] not in ("main",)
                     + SPLITTABLE_ROLES),
                    key=lambda t: (t[0],))       # accents/drinks, id asc
    M, C = len(mains), len(day_plate)
    S_g = sum(g for _, g in pools)
    r = -(-n // max(M, 1))                       # ceil(n / max(M, 1))
    regime = dict(code="meal_regime", n=n, mains=M, components=C,
                  splittable_g=S_g, repeat_cap=r,
                  composition_waived=(n == 1))
    if day is not None:
        regime["day"] = day
    notes.append(regime)

    if C == 0:
        # empty day: an explained hole from build_week — no new failure
        # invented by this layer (the day-level explanation lives upstream)
        notes.append(dict(code="empty_day", day=day, message=(
            "no plate was solved for this day (explained hole from "
            "build_week) — zero-item meals emitted")))
        for meal in meals:
            _finish_meal(meal, comps, targets, W, person, band_flags=False)
        return dict(meals=meals, notes=notes)

    if C == 1:
        # the single component is dealt across slots on its grid — the day
        # is still eatable; every meal flagged (P6/P8)
        cid, g = next(iter(day_plate.items()))
        note = dict(code="meal_composition_impossible", day=day,
                    component=cid, reason=(
                        f"the day plate is a single component ('{cid}') — "
                        "composed meals (main + side) are impossible; "
                        "dealt across slots on its grid"))
        notes.append(note)
        for k, gk in enumerate(_split_on_grid(g, _grid(comps[cid]), n)):
            _add(meals[k]["items"], cid, gk)
            meals[k]["flags"].append(dict(note))
        _conserve(day_plate, meals)
        for meal in meals:
            _finish_meal(meal, comps, targets, W, person)
        return dict(meals=meals, notes=notes)

    if M < n and n > 1:
        # PRD Amendment 2's variety obligation is "where the menu allows" —
        # this is explanation, not failure, declared BEFORE dealing
        notes.append(dict(code="variety_unmet", day=day, mains_available=M,
                          slots_required=n, repeat_cap=r, message=(
                              f"{M} distinct main(s) for {n} meal slot(s) — "
                              f"mains repeat across up to {r} slot(s)")))

    # ---- §3.1 Phase A — skeleton deal --------------------------------------
    # 2. anchor mains (grams desc, id asc)
    slot_main = [None] * n                # the anchor main id per slot
    if M >= n:
        for k in range(n):
            cid, g = mains[k]
            _add(meals[k]["items"], cid, g)
            slot_main[k] = cid
        # surplus mains demote to protein sides of the least-protein meal
        for cid, g in mains[n:]:
            prot = [(_macros_of(meals[k]["items"], comps)["protein"], k)
                    for k in range(n)]
            k = min(prot)[1]
            _add(meals[k]["items"], cid, g)
    elif M >= 1:
        # round-robin split of each main across slots on its grid
        for j, (cid, g) in enumerate(mains):
            parts = _split_on_grid(g, _grid(comps[cid]), n)
            for k in range(n):
                # rotate so the largest share of main j lands on slot j
                _add(meals[(j + k) % n]["items"], cid, parts[k])
            for k in range(n):
                if slot_main[k] is None and meals[k]["items"].get(cid):
                    slot_main[k] = cid

    # 3. guarantee sides: every meal receives >= 1 side. Side material =
    # splittable pools (Phase B's fluid) — a demoted main already counts.
    # Waived at n == 1 (documented in the regime note): the one meal IS the
    # whole plate — the composition rule has nothing to guarantee.
    pool_left = {cid: g for cid, g in pools}
    floor_g = W["MIN_SUBPORTION_G"]
    for k in range(n if n > 1 else 0):
        meal = meals[k]
        has_side = any(comps[cid]["role"] != "main" for cid in meal["items"]) \
            or len([c for c in meal["items"]
                    if comps[c]["role"] == "main"]) > 1
        if has_side:
            continue
        carved = False
        for cid in sorted(pool_left, key=lambda c: (-pool_left[c], c)):
            left, u = pool_left[cid], _grid(comps[cid])
            want = int(-(-floor_g // u) * u) if u > 1 else floor_g
            if left >= want:
                _add(meal["items"], cid, want)
                pool_left[cid] = left - want
                carved = True
                break
            if left > 0:
                # remaining pool smaller than the floor: deal it whole,
                # loudly (never silently relaxed)
                _add(meal["items"], cid, left)
                pool_left[cid] = 0
                meal["flags"].append(dict(
                    code="subportion_floor_relaxed", slot=meal["slot"],
                    component=cid, portion_g=left, floor_g=floor_g,
                    message=(f"'{cid}' had only {left}g left for "
                             f"'{meal['slot']}' — below the "
                             f"{floor_g}g sub-portion floor; dealt whole")))
                carved = True
                break
        if not carved and len(meal["items"]) <= 1:
            # single-component (or empty) meal with no side material left on
            # the plate: ships anyway, loudly flagged (P6/P8) — never
            # silently a bucket
            meal["flags"].append(dict(
                code="composition_unsatisfiable", slot=meal["slot"],
                missing_role="main" if M == 0 else "side",
                plate_components=sorted(day_plate),
                message=(f"'{meal['slot']}' cannot be composed as main + "
                         "side from this plate — no "
                         f"{'main' if M == 0 else 'side'} material "
                         "is left to attach")))

    # 4. attach accents/drinks — never alone (every meal already has
    # material). Affinity: pairs_with data if present, else slot-main
    # cuisine; no match → lowest-kcal meal; ties → main id lexicographic,
    # then slot order.
    for cid, g in extras:
        comp = comps[cid]
        k = _affinity_slot(comp, slot_main, meals, comps, n)
        _add(meals[k]["items"], cid, g)

    # ---- §3.2 Phase B — macro balance (greedy quanta + bounded polish) -----
    if n > 1:
        for cid, g in pools:
            left, u = pool_left.get(cid, 0), _grid(comps[cid])
            if left <= 0:
                continue
            q = u if comps[cid].get("unit_g") else W["QUANTUM_G"]
            q = int(round(q)) or 1
            while left >= q:
                vecs = [_macros_of(meal["items"], comps) for meal in meals]
                k = max(range(n),
                        key=lambda i: (_deficit(vecs[i], targets), -i))
                _add(meals[k]["items"], cid, q)
                left -= q
            if left > 0:
                # integerization graft: remainder dealt largest-deficit-first
                vecs = [_macros_of(meal["items"], comps) for meal in meals]
                k = max(range(n),
                        key=lambda i: (_deficit(vecs[i], targets), -i))
                _add(meals[k]["items"], cid, left)
        _polish(meals, comps, targets, W, n)
    else:
        # n == 1 short-circuit: whole remaining pool to the one meal through
        # the same emission path — zero Phase-B work
        for cid, g in pools:
            _add(meals[0]["items"], cid, pool_left.get(cid, 0))

    # ---- §9.1 conservation — the load-bearing invariant --------------------
    _conserve(day_plate, meals)

    for meal in meals:
        _finish_meal(meal, comps, targets, W, person)
    return dict(meals=meals, notes=notes)


def _affinity_slot(comp, slot_main, meals, comps, n):
    """Accent/drink attachment slot (M19_SPEC §3.1.4)."""
    def _rank(k):
        return (slot_main[k] or "￿", k)     # main id lexicographic
    if comp["role"] == "accent":
        pw = comp.get("pairs_with") or []
        hits = [k for k in range(n) if slot_main[k] and slot_main[k] in pw]
        if hits:
            return min(hits, key=_rank)
        hits = [k for k in range(n) if slot_main[k]
                and comps[slot_main[k]]["cuisine"] == comp["cuisine"]]
        if hits:
            return min(hits, key=_rank)
    kcals = [(_macros_of(meals[k]["items"], comps)["kcal"], k)
             for k in range(n)]
    return min(kcals)[1]


def _conserve(day_plate, meals):
    """Hard invariant: per component, meal grams sum EXACTLY to the day
    plate (int grams). The dealer can never make a day wrong."""
    dealt = {}
    for meal in meals:
        for cid, g in meal["items"].items():
            dealt[cid] = dealt.get(cid, 0) + g
    assert dealt == {c: g for c, g in day_plate.items() if g}, (
        f"meal dealer broke gram conservation: dealt {dealt} != "
        f"plate {day_plate}")


def _band_j(meals_macros, targets, band):
    """Objective J: only violations beyond the soft band score, plus an
    epsilon within-band smoothness tiebreak (M19_SPEC §3.2)."""
    j = 0.0
    for vec in meals_macros:
        for m in MACROS:
            t = targets[m]
            j += max(0.0, abs(vec[m] - t) - band * t)
            j += _SMOOTH_EPS * abs(vec[m] - t)
    return j


def _polish(meals, comps, targets, W, n):
    """Bounded first-improvement local search (RNG-free, fixed scan order).
    Moves: transfer one quantum of a splittable between meals; swap two
    atoms (mains / demoted mains) between meals. Accept iff J strictly
    drops. Guard: a move may never empty a meal."""
    band = W["MEAL_BAND"]
    max_iters = W["POLISH_MAX_ITERS"]
    accepted = 0
    improved = True
    while improved and accepted < max_iters:
        improved = False
        j0 = _band_j([_macros_of(x["items"], comps) for x in meals], targets,
                     band)
        # move 1: quantum transfers of splittables between meal pairs
        cids = sorted({c for meal in meals for c in meal["items"]
                       if comps[c]["role"] in SPLITTABLE_ROLES})
        for cid in cids:
            u = comps[cid].get("unit_g")
            q = int(round(u)) if u else W["QUANTUM_G"]
            for a in range(n):
                ga = meals[a]["items"].get(cid, 0)
                if ga < q or (len(meals[a]["items"]) == 1 and ga <= q):
                    continue
                for b in range(n):
                    if b == a:
                        continue
                    _transfer(meals, cid, a, b, q)
                    j1 = _band_j([_macros_of(x["items"], comps)
                                  for x in meals], targets, band)
                    if j1 < j0 - 1e-9:
                        j0, improved = j1, True
                        accepted += 1
                        break
                    _transfer(meals, cid, b, a, q)       # revert
                if improved:
                    break
            if improved or accepted >= max_iters:
                break
        if improved or accepted >= max_iters:
            continue
        # move 2: swap two atoms between meals (side atoms / slot mains)
        atoms = sorted({c for meal in meals for c in meal["items"]
                        if comps[c]["role"] in ATOMIC_ROLES})
        for a in range(n):
            for b in range(a + 1, n):
                for ca in sorted(meals[a]["items"]):
                    if ca not in atoms:
                        continue
                    for cb in sorted(meals[b]["items"]):
                        if cb not in atoms or cb == ca:
                            continue
                        # spec §3.2: same-role swaps only (side atom for
                        # side atom, slot-main for slot-main) — a cross-role
                        # swap accepted on macro-J alone could strip a
                        # meal's only main AFTER composition flags were
                        # emitted, producing an unflagged mainless meal
                        if comps[ca]["role"] != comps[cb]["role"]:
                            continue
                        if ca in meals[b]["items"] or cb in meals[a]["items"]:
                            continue
                        _swap_atoms(meals, a, b, ca, cb)
                        j1 = _band_j([_macros_of(x["items"], comps)
                                      for x in meals], targets, band)
                        if j1 < j0 - 1e-9:
                            j0, improved = j1, True
                            accepted += 1
                        else:
                            _swap_atoms(meals, a, b, cb, ca)   # revert
                        if improved:
                            break
                    if improved:
                        break
                if improved:
                    break
            if improved:
                break


def _transfer(meals, cid, src, dst, q):
    meals[src]["items"][cid] -= q
    if meals[src]["items"][cid] <= 0:
        del meals[src]["items"][cid]
    _add(meals[dst]["items"], cid, q)


def _swap_atoms(meals, a, b, ca, cb):
    ga = meals[a]["items"].pop(ca)
    gb = meals[b]["items"].pop(cb)
    meals[a]["items"][cb] = gb
    meals[b]["items"][ca] = ga


def _finish_meal(meal, comps, targets, W, person, band_flags=True):
    """Final per-meal bookkeeping: sorted items, macro vector, targets,
    band_unmet + sub_serving flags (soft, reported, never enforced by
    relaxation — P8). ``band_flags=False`` on the empty-day regime: the
    hole is explained upstream (build_week) and this layer invents no new
    failure (M19_SPEC §3.0)."""
    meal["items"] = {cid: meal["items"][cid]
                     for cid in sorted(meal["items"])}
    vec = _macros_of(meal["items"], comps)
    meal["macros"] = {m: round(vec[m], 1) for m in MACROS} \
        | {"kcal": round(vec["kcal"], 1)}
    meal["target"] = {m: round(targets[m], 1) for m in MACROS}
    band = W["MEAL_BAND"]
    for m in (MACROS if band_flags else ()):
        t = targets[m]
        lo, hi = t * (1 - band), t * (1 + band)
        if lo - 1e-9 <= vec[m] <= hi + 1e-9:
            continue
        # responsible component: the largest contributor of this macro
        resp = max(meal["items"],
                   key=lambda c: (meal["items"][c]
                                  * comps[c]["per100"][m] / 100, c),
                   default=None) if meal["items"] else None
        delta = vec[m] - t
        meal["flags"].append(dict(
            code="band_unmet", slot=meal["slot"], macro=m,
            achieved_g=round(vec[m], 1), band=[round(lo, 1), round(hi, 1)],
            target_g=round(t, 1), delta_g=round(delta, 1),
            responsible_component=resp,
            message=(f"'{meal['slot']}' is {abs(round(delta))}g "
                     f"{'over' if delta > 0 else 'short'} of its {m} slot "
                     f"target" + (f" — {resp}" if resp else ""))))
    # §3.3 sub-serving diagnostic: dealt portion below the person's
    # effective serve min — diagnostic only, zero constraints (pre-collects
    # the M1.6 evidence on serve_g's per-serving vs per-day semantics)
    for cid, g in meal["items"].items():
        lo, _hi, _w = effective_serve_bounds(comps[cid], person)
        if g < lo:
            meal["flags"].append(dict(
                code="sub_serving", slot=meal["slot"], component=cid,
                portion_g=g, serve_min_g=lo,
                message=(f"{g}g of '{cid}' in '{meal['slot']}' is below "
                         f"the {lo}g authored serve minimum (serve_g is "
                         "authored per serving; the day pool applies it "
                         "to day totals)")))


# --------------------------------------------------------------------------- #
#  §3.5 picker-nudge dealability term (called from engine.build_week)
# --------------------------------------------------------------------------- #
def dealability_penalty(pl, comps, n, mains_available, config=None):
    """Zero-LP score term added to build_week's candidate-picking loop when
    the person has a meal structure: prefer candidate plates with >=
    min(mains_available, n) distinct mains and >= n × MIN_SUBPORTION_G
    splittable side mass. Returns 0 when the plate satisfies both — and the
    CALLER contributes 0 when meals_per_day is unset, so existing goldens
    stay byte-identical (M19_SPEC §3.5 guard)."""
    W = dict(MEAL_WEIGHTS)
    W.update(config or {})
    pen = 0
    plate_mains = len({c for c in pl if comps[c]["role"] == "main"})
    need_mains = min(mains_available, n)
    if plate_mains < need_mains:
        pen += W["dealability_mains"] * (need_mains - plate_mains)
    side_mass = sum(g for c, g in pl.items()
                    if comps[c]["role"] in SPLITTABLE_ROLES)
    if side_mass < n * W["MIN_SUBPORTION_G"]:
        pen += W["dealability_side_mass"]
    return pen


# --------------------------------------------------------------------------- #
#  §3.4 interchangeable containers (M1.9-IC, opt-in per slot)
# --------------------------------------------------------------------------- #
def equalize_interchangeable(mealdays, slots, *, config=None,
                             group_key=None):
    """Cross-day post-pass for slots with ``interchangeable: true``
    (PRD §4.0 Amendment 2 — a household choice, NEVER a default): permute
    which composed meal occupies the slot each day to minimize within-slot
    cross-day macro spread. Best-effort minimization, not a guarantee —
    achieved spread is reported per (slot, macro); outside
    ``INTERCHANGE_EPSILON`` an ``interchange_unmet`` note carries the
    numbers (day-target cycling at M1.11 will make exact equalization
    impossible by construction). The output explicitly states variety is
    subordinated for the slot. Deterministic greedy (day order, fixed
    tie-breaks); conservation is untouched — meals move between slots
    whole, within their own day.

    ``group_key``: forward-compat equivalence-class key — ``(slot,)`` now,
    ``(slot, day_type)`` at M1.11. Only the default grouping is
    implemented in M1.9.
    """
    W = dict(MEAL_WEIGHTS)
    W.update(config or {})
    eps = W["INTERCHANGE_EPSILON"]
    for si, slot in enumerate(slots):
        if not slot.get("interchangeable"):
            continue
        # STRICT OPT-IN (PRD §4.0 Amendment 2): equalization may only trade
        # meals between slots that ALL opted in — a within-day swap changes
        # both positions' content, so a non-interchangeable slot must never
        # be a donor. With a single opted-in slot there is nothing to trade;
        # the spread report below still runs (honest), plus a note.
        donor_idx = [k for k, s in enumerate(slots)
                     if s.get("interchangeable")]
        chosen = []                # (day, macro vector) actually in the slot
        for md in mealdays:
            meals = md["meals"]
            if not any(meal["items"] for meal in meals):
                continue           # empty day: nothing to permute
            if chosen and len(donor_idx) > 1:
                mean = {m: sum(v[m] for _, v in chosen) / len(chosen)
                        for m in MACROS}
                # pick the day's meal closest to the running slot mean,
                # among opted-in positions only
                best = min(donor_idx, key=lambda k: (
                    max(abs(meals[k].get("macros", {}).get(m, 0.0) - mean[m])
                        / max(mean[m], 1e-9) for m in MACROS), k))
                if best != si:
                    _swap_slot_positions(meals, si, best)
            chosen.append((md, {m: meals[si]["macros"][m] for m in MACROS}))
        if len(donor_idx) == 1:
            for md in mealdays:
                md["notes"].append(dict(
                    code="interchange_limited", slot=slot["name"],
                    message=(f"slot '{slot['name']}' is interchangeable but "
                             "no other slot opted in — meals cannot be "
                             "traded across non-interchangeable slots "
                             "(strict opt-in); cross-day equivalence is "
                             "whatever the dealer produced")))
        # report achieved spread per macro
        tnotes = [dict(code="interchange_applied", slot=slot["name"],
                       message=(f"slot '{slot['name']}' is interchangeable: "
                                "meals were permuted for cross-day macro "
                                "equivalence — variety is subordinated for "
                                "this slot (opt-in, PRD §4.0 Amendment 2)"))]
        if chosen:
            target = chosen[0][0]["meals"][si]["target"]
            for m in MACROS:
                vals = [v[m] for _, v in chosen]
                t = max(target[m], 1e-9)
                spread = (max(vals) - min(vals)) / t
                if spread > eps:
                    mean = sum(vals) / len(vals)
                    days_out = sum(1 for v in vals
                                   if abs(v - mean) > eps * t)
                    tnotes.append(dict(
                        code="interchange_unmet", slot=slot["name"],
                        macro=m, spread=round(spread, 3), epsilon=eps,
                        days_out=days_out,
                        message=(f"slot '{slot['name']}' {m} spreads "
                                 f"{spread:.0%} across days vs the "
                                 f"{eps:.0%} target — {days_out} day(s) "
                                 "outside the band")))
        for md in mealdays:
            md["notes"].extend(dict(x) for x in tnotes)
    return mealdays


def _swap_slot_positions(meals, a, b):
    """Swap the composed meals at slot positions a and b: content moves,
    slot identity (name + serving model) stays with the position."""
    ident_a = (meals[a]["slot"], meals[a]["serving_model"])
    ident_b = (meals[b]["slot"], meals[b]["serving_model"])
    meals[a], meals[b] = meals[b], meals[a]
    for k, (nm, sm) in ((a, ident_a), (b, ident_b)):
        old = meals[k]["slot"]
        meals[k]["slot"], meals[k]["serving_model"] = nm, sm
        for f in meals[k]["flags"]:
            if f.get("slot") == old:
                f["slot"] = nm


# --------------------------------------------------------------------------- #
#  week-level driver (P10: the ONE place meal structure is derived)
# --------------------------------------------------------------------------- #
def deal_week(people, comps, weeks, *, config=None, anchor=None):
    """Deal every configured person-day of a solved week. Returns
    ``{person: [MealDay per day]}`` covering ONLY the people with a meal
    structure — an empty dict when nobody configures meals (the layer is
    inert; the pipeline stays byte-identical, M19_SPEC §1). Called by the
    week pipeline after build_week; rebalance re-deals through the same
    path (re-solve the day, then re-deal it).

    ``anchor`` (M1.11): each day deals against the DAY-VIEW person
    (``model.person_for_day``), so per-meal slot targets are the resolved
    day-type targets over n. Identity when nobody authors profiles."""
    out = {}
    for pname in sorted(weeks):
        person = people[pname]
        slots = resolve_meal_slots(person)
        if not slots:
            continue
        days = [deal_day(person_for_day(person, d, anchor), comps, pl,
                         slots, config=config, day=d)
                for d, pl in enumerate(weeks[pname])]
        if any(s.get("interchangeable") for s in slots):
            days = equalize_interchangeable(days, slots, config=config)
        out[pname] = days
    return out
