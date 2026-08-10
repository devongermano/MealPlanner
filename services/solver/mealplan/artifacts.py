"""artifacts.py — the three human-readable deliverables (M1.1, PRD §4.3
step 4) + relaxed-mode rendering with honest error bars (M1.2, PRD §4.1).

RENDERING ONLY — zero solver logic (P10: consumes engine/costing outputs
verbatim). Every renderer takes ALREADY-SOLVED structures:

- ``weeks``          engine.build_week output ({person: [{cid: g} per day]})
- ``sp``             costing.session_plan output (the canonical session plan)
- ``purchase_rows``  costing.purchase output rows
- ``total_cost``     costing.menu_cost output

so unit tests build tiny plans by hand with no LP solve. The only engine
imports are the availability predicates (``available_on``/``from_freezer``)
— pure shelf-life arithmetic that the existing cli.render also calls at
render time; reusing them IS the P10 discipline (never re-derive).

Deliverables (each carries the seed + library + date footer for
traceability toward the M1.3 locked artifact):

1. shopping_list.md   store units (units.human_pack), perishable vs
                      shelf-stable groups, checkbox rows, pantry-deduction
                      notes, expiring-unused pantry warnings, estimated
                      total cost ("at estimated prices" — prices carry
                      estimate provenance).
2. cook_plan.md       per session: component × batches, scaled ingredient
                      grams (method steps when the recipe has them),
                      hands-on minutes vs active_min_budget with an explicit
                      OVER flag (never silently fit), thaw notes,
                      leftover-first notes, shortest-keeps callout.
3. eat_<person>.md    per day: portions sorted desc; day macro totals vs
                      targets with per-macro tolerance status (hit / over /
                      short); explained holes and freezer/thaw markings;
                      relaxed people get household-unit rendering with
                      HONEST error bars (real arithmetic from the rounding
                      actually applied — see day_error_bars).

Relaxed rendering (M1.2 — eat sheets ONLY; the engine still solves grams):
- unit_g components      -> counts ("4 tortillas")
- household_unit         -> nearest HALF unit ("about 2 cups (~370g)")
- everything else        -> friendly batch fractions (1/4, 1/3, 1/2, 2/3,
                            3/4, whole) of yield_g ("about 1/3 of the
                            batch (~417g)")
Precision people see exact grams.
"""

import math
from pathlib import Path

from . import methods as methods_mod
from . import schedule as schedule_mod
from .engine import available_on, from_freezer
from .units import MACROS, human_pack, kcal_of

# Friendly fraction grid for batch-fraction rendering (M1.2). 0 is
# deliberately absent: a served portion never renders as "none".
FRIENDLY_FRACTIONS = ((1, 4), (1, 3), (1, 2), (2, 3), (3, 4))


# --------------------------------------------------------------------------- #
#  relaxed-mode portion rendering (M1.2)
# --------------------------------------------------------------------------- #
def _plural(name, n):
    """Naive pluralizer for household-unit nouns ('cup' -> 'cups').
    Quantities <= 1 ('1/2 cup', '1 cup') stay singular."""
    if n <= 1 or name.endswith("s"):
        return name
    return name + "s"


def _fmt_halves(x):
    """1.5 -> '1 1/2', 0.5 -> '1/2', 2.0 -> '2'. ``x`` is a half-multiple."""
    whole, half = int(x), (x - int(x)) >= 0.5
    if whole and half:
        return f"{whole} 1/2"
    if half:
        return "1/2"
    return str(whole)


def _fmt_fraction(x):
    """A friendly-grid value as text: 0.333.. -> '1/3', 1.5 -> '1 1/2'."""
    whole = int(x + 1e-9)
    frac = x - whole
    if frac < 1e-9:
        return str(whole)
    for n, d in FRIENDLY_FRACTIONS:
        if abs(frac - n / d) < 1e-9:
            return f"{whole} {n}/{d}" if whole else f"{n}/{d}"
    return f"{x:g}"                      # unreachable for grid values


def friendly_batch_fraction(x):
    """Nearest value to ``x`` on the friendly grid {w + f : w in 0..,
    f in {0, 1/4, 1/3, 1/2, 2/3, 3/4}} \\ {0}. Ties break toward the
    SMALLER value (deterministic)."""
    cands = set()
    for w in range(int(x) + 2):
        cands.add(float(w))
        for n, d in FRIENDLY_FRACTIONS:
            cands.add(w + n / d)
    cands.discard(0.0)                   # never render "none of the batch"
    return min(sorted(cands), key=lambda c: (abs(c - x), c))


def render_portion(comp, grams, mode):
    """One portion -> ``(text, implied_g)``.

    ``implied_g`` is the grams the rendered instruction actually implies —
    for precision rendering it equals the solved grams; for relaxed
    rendering it is the ROUNDED quantity times the unit size. The delta
    (implied - solved) is the raw material of the honest error bars.
    """
    g = grams
    if mode != "relaxed":
        return f"**{g:g}g**", float(g)
    hu = comp.get("household_unit")
    u = comp.get("unit_g")
    if u:
        # discrete pieces: the solver already snapped to whole units
        n = int(round(g / u))
        noun = _plural(hu["name"], n) if hu else f"× {comp['name']}"
        implied = n * u
        return f"**{n} {noun}** (~{implied:.0f}g)", float(implied)
    if hu:
        # household units, rounded to the nearest HALF unit (never zero)
        units = g / hu["grams"]
        r = max(0.5, round(units * 2) / 2)
        implied = r * hu["grams"]
        return (f"**about {_fmt_halves(r)} {_plural(hu['name'], r)}** "
                f"(~{implied:.0f}g)", float(implied))
    # batch fraction of yield_g on the friendly grid
    frac = friendly_batch_fraction(g / comp["yield_g"])
    implied = frac * comp["yield_g"]
    if frac < 1:
        what = f"about {_fmt_fraction(frac)} of the batch"
    elif frac == 1:
        what = "about the whole batch"
    else:
        what = f"about {_fmt_fraction(frac)} batches"
    return f"**{what}** (~{implied:.0f}g)", float(implied)


def day_error_bars(entries, comps):
    """HONEST error bars for one day of relaxed rendering (M1.2).

    ``entries``: list of ``(cid, solved_g, implied_g)`` — the solved grams
    and the grams the rendered instruction implies (render_portion). For
    each macro the bar is the solved day total plus the WORST-CASE
    aggregation of per-item rounding deltas:

        lo_m = solved_m + sum(min(0, delta_i_m))
        hi_m = solved_m + sum(max(0, delta_i_m))

    where ``delta_i_m = (implied_g - solved_g) * per100[m] / 100``. The
    implied day total always lies inside [lo, hi] (it is solved + sum of
    deltas, and each delta is between its own min/max contribution) — the
    property test pins this. Printed bars floor lo and ceil hi so the
    integer bars still CONTAIN the implied totals.

    Returns ``{macro: (lo_int, hi_int)}`` plus ``{"solved": {macro: g}}``.
    """
    solved = {m: 0.0 for m in MACROS}
    lo = {m: 0.0 for m in MACROS}
    hi = {m: 0.0 for m in MACROS}
    for cid, sg, ig in entries:
        per = comps[cid]["per100"]
        for m in MACROS:
            solved[m] += sg * per[m] / 100
            delta = (ig - sg) * per[m] / 100
            lo[m] += min(0.0, delta)
            hi[m] += max(0.0, delta)
    return {m: (math.floor(solved[m] + lo[m]), math.ceil(solved[m] + hi[m]))
            for m in MACROS} | {"solved": solved}


# --------------------------------------------------------------------------- #
#  footer — plan traceability (toward the M1.3 locked artifact)
# --------------------------------------------------------------------------- #
def footer(meta):
    """Seed + library identity + date, on every deliverable. ``meta`` is
    ``{"seed": int, "library": str, "date": str}`` — the date is the plan
    date the CALLER passed (never a wall clock: artifacts stay
    byte-deterministic across runs)."""
    m = meta or {}
    return ("\n---\n"
            f"_plan seed {m.get('seed', '?')} · "
            f"library {m.get('library', '?')} · "
            f"date {m.get('date', 'unspecified')} · mealplan schema v1_\n")


# --------------------------------------------------------------------------- #
#  deliverable 1 — shopping_list.md
# --------------------------------------------------------------------------- #
def render_shopping_list(ing, purchase_rows, total_cost, pantry=None,
                         stock_warnings=None, meta=None):
    """``purchase_rows`` is costing.purchase output (pantry ALREADY
    deducted before pack rounding); ``total_cost`` is costing.menu_cost
    output; ``stock_warnings`` are costing.age_pantry's structured
    ``stock_expiring_unused`` warnings. This function only formats."""
    stock = {}
    for r in ((pantry or {}).get("stock") or []):
        stock[r["ingredient"]] = stock.get(r["ingredient"], 0) + r["grams"]

    def line(name, need, units, pack, left):
        have = stock.get(name, 0)
        if units <= 0:
            return (f"- ~~{name}~~ — covered by pantry (have {have:g}g) — "
                    "nothing to buy")
        s = (f"- [ ] {name} — {units} × {human_pack(pack)} "
             f"(need {need:g}g, {left:g}g leftover)")
        if have > 0:
            s += f" — have {have:g}g in pantry, buying the rest"
        return s

    perishable = [r for r in purchase_rows if r[5]]
    stable = [r for r in purchase_rows if not r[5]]
    L = ["# Shopping list\n"]
    L.append("## Perishable (buy fresh — raw shelf life is a constraint)\n")
    if perishable:
        for name, need, units, pack, left, per, keeps in perishable:
            L.append(line(name, need, units, pack, left) + f" — keeps {keeps}d raw")
    else:
        L.append("_nothing perishable this week_")
    L.append("\n## Shelf-stable\n")
    if stable:
        for name, need, units, pack, left, per, keeps in stable:
            L.append(line(name, need, units, pack, left))
    else:
        L.append("_nothing shelf-stable this week_")
    if stock_warnings:
        L.append("\n## Pantry warnings — stock expiring unused\n")
        for w in stock_warnings:
            L.append(f"- WARNING: {w['message']}")
    L.append(f"\n**Total estimated cost: ${total_cost:,.2f}** at estimated "
             "prices (ingredient prices are estimates, not receipts).")
    L.append(footer(meta))
    return "\n".join(L)


# --------------------------------------------------------------------------- #
#  deliverable 2 — cook_plan.md: the COMPILED SESSION SCRIPT (M1.10,
#  PRD §4.0/§6). Recipes are ground truth, never rendered — what the cook
#  follows is compiled per plan from method-step fragments (methods.py),
#  batch-scaled, shared-prep-consolidated, with the portioning matrix
#  attached per session. cook_plan_style "recipe" renders per-dish blocks;
#  "timeline" (the M1.12 interleaved scheduler) falls back to recipe blocks
#  WITH an explicit note until it exists — never silently.
# --------------------------------------------------------------------------- #
def build_portioning(sp, weeks, people, meals, comps):
    """The portioning matrix — a PURE RESHAPE (M19_SPEC §1): join each
    session's canonical day-level attribution (``session.feeds``, the M0.4
    function's own rows — never re-derived) against the dealt MealDay
    structure. Returns ``{session_index: {"portioned": {cid: rows},
    "shared": {cid: {total_g, takers}}, "leftover_notes": [...]}}`` or None
    when nobody configured meals (the matrix belongs to the meal-prep
    model; meal-free plans keep the pre-M1.10 cook plan shape).

    - portioned rows: ``{person, day, slot, grams, pack_at_thaw}`` — one
      per (person, day, slot) container this session packs. Freezer-bridged
      days are marked ``pack_at_thaw`` (M19_SPEC §11.1: a batch headed for
      the freezer cannot be packed into meal containers on cook day —
      recorded, never silently packed).
    - shared: family_style slots AND meal-free people pool into shared
      containers — storage totals with per-taker breakdown ("the eat
      sheets say who takes how much").
    """
    if not meals:
        return None
    out = {}
    for s in sp["sessions"]:
        fed = {}
        for r in s.get("feeds", []):
            fed.setdefault(r["component"], set()).add(r["day"])
        frozen = {(n["component"], n["day"])
                  for n in s.get("freezer_notes", [])}
        portioned, shared = {}, {}
        for pname in sorted(weeks):
            wk = weeks[pname]
            mds = (meals or {}).get(pname)
            for cid in sorted(fed):
                for d in sorted(fed[cid]):
                    if d >= len(wk) or not wk[d]:
                        continue
                    if mds:
                        for meal in mds[d]["meals"]:
                            g = meal["items"].get(cid)
                            if not g:
                                continue
                            row = dict(person=pname, day=d,
                                       slot=meal["slot"], grams=g,
                                       pack_at_thaw=(cid, d) in frozen)
                            if meal["serving_model"] == "portioned":
                                portioned.setdefault(cid, []).append(row)
                            else:
                                e = shared.setdefault(
                                    cid, dict(total_g=0, takers=[]))
                                e["total_g"] += g
                                e["takers"].append(row)
                    else:
                        g = wk[d].get(cid)
                        if not g:
                            continue
                        e = shared.setdefault(cid,
                                              dict(total_g=0, takers=[]))
                        e["total_g"] += g
                        e["takers"].append(dict(
                            person=pname, day=d, slot=None, grams=g,
                            pack_at_thaw=(cid, d) in frozen))
        lo_notes = [n for n in sp.get("leftover", [])
                    if n["day"] in fed.get(n["component"], ())]
        out[s["index"]] = dict(portioned=portioned, shared=shared,
                               leftover_notes=lo_notes)
    return out


def _pp_component_lines(cid, m, comps):
    """One component's rows of the portioning matrix (containers to pack
    plus family-style storage) — shared by the after-stream 'Portion &
    pack' section and the idle-hands injection (M1.12)."""
    L = []
    name = comps[cid]["name"]
    rows = m["portioned"].get(cid)
    if rows:
        L.append(f"- **{name}** — pack {len(rows)} container"
                 f"{'s' if len(rows) != 1 else ''}:")
        for r in rows:
            thaw = (" — **PACK AT THAW** (freezer-bridged day: portion "
                    "when it thaws, not on cook day)"
                    if r["pack_at_thaw"] else "")
            L.append(f"  - [ ] {r['person']} · eat day {r['day'] + 1} · "
                     f"{r['slot']} — {r['grams']:g}g{thaw}")
    e = m["shared"].get(cid)
    if e:
        L.append(f"- **{name}** — family style: store "
                 f"**{e['total_g']:g}g** in a shared container — the "
                 "eat sheets say who takes how much:")
        for r in e["takers"]:
            slot = f" · {r['slot']}" if r["slot"] else ""
            thaw = " — from freezer on that day" if r["pack_at_thaw"] \
                else ""
            L.append(f"  - {r['person']} · eat day {r['day'] + 1}"
                     f"{slot} — takes {r['grams']:g}g{thaw}")
    return L


def _portion_pack_lines(s, matrix, comps, h, skip=frozenset()):
    """The 'Portion & pack' block for one session (portioning matrix).
    Components in ``skip`` were already packed during an idle-hands
    window inside the timeline stream and are omitted here (M1.12)."""
    m = (matrix or {}).get(s["index"])
    if not m or not (m["portioned"] or m["shared"]):
        return []
    cids = [cid for cid in sorted(set(m["portioned"]) | set(m["shared"]))
            if cid not in skip]
    if not cids and not m["leftover_notes"]:
        return []
    L = [f"{h}# Portion & pack — session {s['index']}\n"]
    L.append("Portioned slots pack into per-person, per-meal containers "
             "(rows match the eat sheets); family-style batches stay in "
             "shared containers.\n")
    if skip:
        packed = ", ".join(comps[cid]["name"] for cid in sorted(skip))
        L.append(f"_Already packed during the idle-hands window in the "
                 f"timeline above: {packed}._\n")
    for cid in cids:
        L.extend(_pp_component_lines(cid, m, comps))
    for n in m["leftover_notes"]:
        L.append(f"- note: {n['grams']:g}g of '{n['component']}' for eat "
                 f"day {n['day'] + 1} comes from existing leftovers — pack "
                 "only the remainder from this batch.")
    L.append("")
    return L


def _step_lines(cid, b, comps, steps, merged_keys, used_ops):
    """One dish's method-fragment steps as annotated checkboxes with
    batch-scaled quantities. Steps consolidated into the session's shared
    prep render as pointers, not duplicate work."""
    L = []
    c = comps[cid]
    for idx, st in enumerate(steps):
        if (cid, idx) in merged_keys:
            L.append(f"- ~~{st['text']}~~ — done in Shared prep above")
            continue
        iid, grams = methods_mod.scaled_step_grams(st, c, b)
        qty = f" ({grams:g}g {iid})" if iid else ""
        temp = (f" {st['oven_temp_f']}°F" if st.get("oven_temp_f") is not None
                else "")
        ann = (f"{st['station']}{temp} · {st['mode']} "
               f"~{st['duration_min']:g} min")
        op = st.get("operation")
        ref = ""
        if op:
            used_ops.add(op)
            ref = f" · [{op}]"
        L.append(f"- [ ] {st['text']}{qty} — _{ann}{ref}_")
    return L


def _timeline_entry_line(e, comps, used_ops):
    """One timeline entry as a timestamped checkbox line: relative
    timestamps from 0:00, timer callouts for passive waits, 'Meanwhile'
    framing for active work done while something cooks itself."""
    t0 = schedule_mod.format_min(e["t_start"])
    t1 = schedule_mod.format_min(e["t_end"])
    dur = e["t_end"] - e["t_start"]
    if e["component"] == schedule_mod.SHARED:
        who, text = "Shared prep", e["step_text"]
    else:
        who = comps[e["component"]]["name"]
        qty = (f" ({e['grams']:g}g {e['ingredient']})"
               if e["ingredient"] else "")
        text = e["step_text"] + qty
    temp = (f" {e['oven_temp_f']}°F" if e.get("oven_temp_f") is not None
            else "")
    ref = ""
    if e.get("operation"):
        used_ops.add(e["operation"])
        ref = f" · [{e['operation']}]"
    if e["mode"] == "passive":
        return (f"- [ ] **{t0}–{t1}** {who}: {text} — _{e['station']}{temp} "
                f"· passive{ref}_ — ⏱ set a timer for {dur:g} min "
                f"(up at {t1})")
    mw = "Meanwhile — " if e["meanwhile"] else ""
    return (f"- [ ] **{t0}–{t1}** {mw}{who}: {text} — _{e['station']}{temp} "
            f"· active ~{dur:g} min{ref}_")


def _idle_injection_lines(win, m, comps, cids):
    """Portioning-matrix work injected into an idle-hands window — the
    kitchen is working, the cook is not (M1.12). Only ``cids`` (components
    whose last cook step has finished by the window's start) are injected:
    never tell the cook to pack food that is still cooking. Everything
    else packs after the stream."""
    a, b = win
    L = [f"- **{schedule_mod.format_min(a)}–{schedule_mod.format_min(b)}** "
         "Idle hands — the kitchen is cooking itself; portion & pack "
         "what's already done:"]
    for cid in cids:
        L.extend(_pp_component_lines(cid, m, comps))
    return L


def _timeline_session_lines(s, comps, settings, methods, matrix, used_ops,
                            h):
    """One session as the interleaved timeline stream (M1.12): greedy
    schedule (schedule.compile_session — timeline households only), mise
    en place, timestamped stream with timers and 'meanwhile' framing,
    ready-to-pack portioning work injected into the best idle-hands
    window (readiness-filtered: only components already done cooking),
    recipe blocks for fragment-less components, and the honest
    makespan-vs-naive line."""
    sched = schedule_mod.compile_session(s["batches"], comps, methods,
                                         settings)
    L = []
    saved = sched["naive_min"] - sched["makespan_min"]
    L.append(f"Interleaved: **{sched['makespan_min']} min** start to "
             f"finish vs {sched['naive_min']} min one-thing-at-a-time — "
             + (f"parallelization saves you **{saved} min**."
                if saved > 0 else "no overlap available this session.")
             + " _Times are estimates; calibrate by cooking._\n")
    scheduled_cids = sorted(set(s["batches"]) - set(sched["unscheduled"]))
    if scheduled_cids:
        L.append(f"{h}# Mise en place — batch-scaled quantities\n")
        for cid in scheduled_cids:
            b = s["batches"][cid]
            c = comps[cid]
            ings = ", ".join(f"{iname} {grams * b:g}g"
                             for iname, grams in c["ingredients"].items())
            L.append(f"- [ ] **{c['name']}** × {b} "
                     f"batch{'es' if b != 1 else ''} "
                     f"(makes {s['made_g'][cid]:g}g, need "
                     f"{s['demand_g'][cid]:g}g): {ings}")
        L.append("")
    L.append(f"{h}# Timeline — 0:00 is when you start\n")
    m = (matrix or {}).get(s["index"])
    pp_cids = (sorted(set(m["portioned"]) | set(m["shared"]))
               if m else [])
    # a component is packable in a window only once its LAST cook step
    # has finished by the window's start — never tell the cook to pack
    # food that is still cooking (readiness filter)
    ready_at = {}
    for e in sched["entries"]:
        if e["component"] != schedule_mod.SHARED:
            ready_at[e["component"]] = max(
                ready_at.get(e["component"], 0), e["t_end"])

    def _packable(w):
        return [cid for cid in pp_cids
                if cid in ready_at and ready_at[cid] <= w[0]]

    win, win_cids = None, []
    if pp_cids and sched["idle_windows"]:
        # most packable components wins; widest, then earliest, break
        # ties (deterministic)
        best = max(sched["idle_windows"],
                   key=lambda w: (len(_packable(w)), w[1] - w[0], -w[0]))
        cids = _packable(best)
        if cids:
            win, win_cids = best, cids
    injected = False
    for e in sched["entries"]:
        if win and not injected and e["t_start"] >= win[0]:
            L.extend(_idle_injection_lines(win, m, comps, win_cids))
            injected = True
        L.append(_timeline_entry_line(e, comps, used_ops))
    if win and not injected:
        L.extend(_idle_injection_lines(win, m, comps, win_cids))
        injected = True
    L.append("")
    for cid in sched["unscheduled"]:
        b = s["batches"][cid]
        c = comps[cid]
        L.append(f"{h}# {c['name']} × {b} batch{'es' if b != 1 else ''} "
                 f"(makes {s['made_g'][cid]:g}g, need "
                 f"{s['demand_g'][cid]:g}g)\n")
        L.append(f"Scaled for {b} batch{'es' if b != 1 else ''}:")
        for iname, grams in c["ingredients"].items():
            L.append(f"- [ ] {iname}: {grams * b:g}g")
        for step in (c.get("method") or []):
            L.append(f"1. {step}")
        L.append("")
    # everything NOT packed during the idle-hands window (components
    # still cooking at every window's start, or no window at all) packs
    # here, after the stream
    rest = _portion_pack_lines(s, matrix, comps, h,
                               skip=frozenset(win_cids))
    if rest:
        L.extend(rest)
    for w in sched["warnings"]:
        L.append(f"- NOTE: {w['message']}")
    return L


def cook_script_lines(comps, settings, sp, methods=None, techniques=None,
                      matrix=None, h="##"):
    """The compiled session script — THE one renderer behind both
    cook_plan.md and the plan.md report (no divergence by construction).
    ``methods`` is methods.load_methods output (None/missing components
    degrade gracefully to the ingredient-list rendering); ``matrix`` is
    build_portioning output. Returns ``(lines, used_operations)``."""
    methods = methods or {}
    used_ops = set()
    L = []
    style = settings.get("cook_plan_style") or "recipe"
    timeline = style == "timeline"
    if timeline:
        L.append("> **Timeline cook plan** (M1.12) — one interleaved "
                 "stream per session: longest passive work starts first, "
                 "one active task at a time, a timer for every unattended "
                 "wait. Times are ESTIMATES — calibrate by cooking "
                 "(durations are provisional until cook-day "
                 "calibration).\n")
    for s in sp["sessions"]:
        L.append(f"{h} Session {s['index']} — cook day {s['start']} — "
                 f"{s['minutes']} min hands-on\n")
        if not s["batches"]:
            L.append("_nothing to cook this session_\n")
            continue
        summary = methods_mod.station_summary(s["batches"], methods) \
            if methods else None
        if summary:
            L.append(f"Stations (single-batch step estimates, provisional "
                     f"until cook-day calibration): {summary}\n")
        if timeline and any(methods.get(cid) for cid in s["batches"]):
            # M1.12: the interleaved stream — scheduler runs ONLY here
            L.extend(_timeline_session_lines(s, comps, settings, methods,
                                             matrix, used_ops, h))
            L.extend(_session_tail_lines(s, comps))
            continue
        if timeline:
            L.append("_No method fragments loaded for this session — "
                     "recipe blocks below (run with --methods to get the "
                     "interleaved timeline)._\n")
        merged, merged_keys = (
            methods_mod.consolidate_shared_prep(s["batches"], s["batches"],
                                                comps, methods)
            if methods else ([], set()))
        if merged:
            L.append(f"{h}# Shared prep — consolidated across dishes\n")
            L.append("Identical prep merged once per session "
                     "(same operation, same ingredient):\n")
            for mstep in merged:
                alloc = ", ".join(
                    f"{p['grams']:g}g {p['component']}"
                    for p in mstep["parts"])
                used_ops.add(mstep["operation"])
                L.append(f"- [ ] {mstep['operation'].capitalize()} "
                         f"{mstep['total_g']:g}g {mstep['ingredient']} — "
                         f"{alloc} — _{mstep['station']} · {mstep['mode']} "
                         f"~{mstep['duration_min']:g} min · "
                         f"[{mstep['operation']}]_")
            L.append("")
        for cid in sorted(s["batches"]):
            b = s["batches"][cid]
            c = comps[cid]
            L.append(f"{h}# {c['name']} × {b} batch{'es' if b != 1 else ''} "
                     f"(makes {s['made_g'][cid]:g}g, need "
                     f"{s['demand_g'][cid]:g}g)\n")
            L.append(f"Scaled for {b} batch{'es' if b != 1 else ''}:")
            for iname, grams in c["ingredients"].items():
                L.append(f"- [ ] {iname}: {grams * b:g}g")
            steps = methods.get(cid)
            if steps:
                L.append("")
                L.extend(_step_lines(cid, b, comps, steps, merged_keys,
                                     used_ops))
            else:
                # graceful degradation: no fragment -> the pre-M1.10
                # rendering for this component (legacy inline method prose)
                for step in (c.get("method") or []):
                    L.append(f"1. {step}")
            L.append("")
        L.extend(_portion_pack_lines(s, matrix, comps, h))
        L.extend(_session_tail_lines(s, comps))
    return L, used_ops


def _session_tail_lines(s, comps):
    """Per-session closing notes shared by both cook-plan styles: the
    shortest-keeps callout, thaw notes, freezer notes."""
    L = []
    # shortest cooked shelf life in this session — eat it first
    short = min(s["batches"], key=lambda cid: (comps[cid]["keeps_days"],
                                               cid))
    kd = comps[short]["keeps_days"]
    L.append(f"> Shortest keeps this session: **{comps[short]['name']}** "
             f"— {kd}d cooked; good through day "
             f"{s['start'] + kd - 1}. Eat it first.")
    for n in s["thaw_notes"]:
        L.append(f"- THAW: {n['note']}")
    for n in s.get("freezer_notes", []):
        L.append(f"- FREEZER: {n['note']}")
    L.append("")
    return L


def technique_glossary_lines(used_ops, techniques, h="##"):
    """Technique footnotes: every [operation] referenced by a rendered step
    resolves here against data/techniques (PRD §10 — one explanation reused
    across every step naming the operation)."""
    if not used_ops or not techniques:
        return []
    L = [f"{h} Techniques\n"]
    for op in sorted(used_ops):
        t = techniques.get(op)
        if t:
            L.append(f"- **[{op}]** {t.get('name', op)} — "
                     f"{t.get('one_line', '')}")
    L.append("")
    return L


def render_cook_plan(comps, settings, sp, meta=None, methods=None,
                     techniques=None, matrix=None):
    """``sp`` is costing.session_plan output — THE canonical session plan
    (M0.4/P10). This function compiles and formats (batch-scaling recipe
    quantities, injecting step quantities, consolidating shared prep,
    attaching the portioning matrix); it never re-attributes anything."""
    budget = settings["active_min_budget"]
    L = ["# Cook plan\n"]
    over = sp["minutes"] - budget
    L.append(f"Hands-on total: **{sp['minutes']} min** vs a {budget} min "
             "weekly budget — "
             + (f"**OVER budget by {over} min** (never silently fit: cut a "
                "batch, drop a dish, or raise the budget)" if over > 0
                else f"{-over} min under budget")
             + ".\n")
    lines, used_ops = cook_script_lines(comps, settings, sp, methods=methods,
                                        techniques=techniques, matrix=matrix,
                                        h="##")
    L.extend(lines)
    for n in sp.get("leftover", []):
        L.append(f"- LEFTOVER FIRST: {n['grams']:g}g of "
                 f"'{n['component']}' from the fridge before cooking "
                 f"(day {n['day']}) — {n['note']}")
    for u in sp["unattributed"]:
        L.append(f"- WARNING: {u['grams']}g of `{u['component']}` demanded "
                 f"on day {u['day']} but no cook session can feed that day "
                 "— run `mealplan doctor`.")
    L.extend(technique_glossary_lines(used_ops, techniques, h="##"))
    L.append(footer(meta))
    return "\n".join(L)


# --------------------------------------------------------------------------- #
#  deliverable 3 — eat_<person>.md
# --------------------------------------------------------------------------- #
def attach_accents(items, comps):
    """Group served items into base rows with their accents ATTACHED
    (M1.10, PRD §4.0: family-style phrasing hangs accents on their base —
    "Scrambled eggs — take 300g + 40g cheddar on top").

    Affinity mirrors the meal dealer's attachment data (M19_SPEC §3.1.4):
    ``pairs_with`` match first, else a same-cuisine main, else the largest
    main, else the largest base. Returns ``(rows, orphans)`` where rows are
    ``(base_cid, grams, [(accent_cid, grams), ...])`` sorted grams desc
    then id, and orphans are accents with no base in the item set."""
    bases = sorted(((c, g) for c, g in items.items()
                    if comps[c]["role"] != "accent"),
                   key=lambda x: (-x[1], x[0]))
    accents = sorted(((c, g) for c, g in items.items()
                      if comps[c]["role"] == "accent"))
    attached = {c: [] for c, _ in bases}
    orphans = []
    mains = [c for c, _ in bases if comps[c]["role"] == "main"]
    for c, g in accents:
        comp = comps[c]
        pw = comp.get("pairs_with") or []
        base = next((bc for bc, _ in bases if bc in pw), None)
        if base is None:
            base = next((bc for bc in mains
                         if comps[bc]["cuisine"] == comp["cuisine"]), None)
        if base is None and mains:
            base = mains[0]
        if base is None and bases:
            base = bases[0][0]
        if base is None:
            orphans.append((c, g))
        else:
            attached[base].append((c, g))
    return [(c, g, attached[c]) for c, g in bases], orphans


def _family_style_lines(items, comps, mode, entries, day_ix, settings, ing,
                        shared_note="shared batch"):
    """Take-amount voice for a family-style pool (a meal's items or a whole
    day-pool): base lines with attached accents, household units + error-bar
    entries via the M1.2 machinery."""
    L = []
    rows, orphans = attach_accents(items, comps)
    for c, g, accs in rows:
        qty, implied = render_portion(comps[c], g, mode)
        entries.append((c, float(g), implied))
        extra = ""
        if from_freezer(comps[c], day_ix, settings, ing):
            extra = "  — from freezer — thaw ahead"
        acc_txt = ""
        for ac, ag in accs:
            aqty, aimplied = render_portion(comps[ac], ag, mode)
            entries.append((ac, float(ag), aimplied))
            acc_txt += f" + {aqty} {comps[ac]['name']}"
        if acc_txt:
            acc_txt += " on top"
        L.append(f"- {comps[c]['name']} ({shared_note}) — take "
                 f"{qty}{acc_txt}{extra}")
    for c, g in orphans:
        qty, implied = render_portion(comps[c], g, mode)
        entries.append((c, float(g), implied))
        L.append(f"- {comps[c]['name']} — take {qty}")
    return L


def _macro_status(total, target, tol):
    """Per-macro tolerance status: 'hit' inside the ±tol band, else the
    SIGNED gram delta vs the target ('+Ng over' / '-Ng short')."""
    lo, hi = target * (1 - tol), target * (1 + tol)
    if lo - 1e-9 <= total <= hi + 1e-9:
        return "hit"
    d = round(total - target)
    return f"+{d}g over" if d > 0 else f"-{abs(d)}g short"


def render_eat_sheet(pname, person, comps, week, settings, menu, ing=None,
                     relax_tiers=None, meta=None, meal_days=None):
    """One person's per-day assembly sheet. ``week`` is that person's list
    of solved day plates ({cid: grams}); empty/None days are EXPLAINED
    holes, never silent. ``relax_tiers`` is diag["relax_tiers"][pname].

    ``meal_days`` (M1.9): this person's dealt MealDay list (meals.deal_week
    output). When present, each day renders as Meal 1..n SECTIONS — meal
    heading (slot + serving model), items, per-meal macro subtotal — the
    clean minimal meal-structured sheet; the full sheet rework (portioning
    matrix, family_style vs portioned phrasing) is M1.10. When None the
    sheet is byte-identical to pre-M1.9 (the layer is inert)."""
    mode = person.get("mode") or "precision"
    tol = person["tolerance"]
    t = person["targets"]
    L = [f"# Eat sheet — {pname}\n"]
    L.append(f"Mode: **{mode}**"
             + (" — household units with honest error bars; the plan is "
                "solved in grams underneath" if mode == "relaxed"
                else " — grams; a kitchen scale is strongly preferred")
             + f". Tolerance ±{tol:.0%}.")
    L.append(f"Daily targets: {t['protein']}g protein / {t['fat']}g fat / "
             f"{t['carb']}g carb ({kcal_of(t):.0f} kcal).\n")
    for d, pl in enumerate(week, 1):
        if not pl:
            gone = [comps[i]["name"] for i in menu
                    if not available_on(comps[i], d - 1, settings, ing)]
            L.append(f"## Day {d} — NO FEASIBLE PLATE\n")
            L.append("- past shelf life by this day: "
                     + (", ".join(gone) if gone else "nothing"))
            L.append("- fix one of: move the 2nd cook day later, swap in a "
                     "longer-keeping protein, or freeze half of a batch on "
                     "cook day.\n")
            continue
        tot = {m: sum(g * comps[c]["per100"][m] / 100 for c, g in pl.items())
               for m in MACROS}
        tiers = relax_tiers or []
        tier = tiers[d - 1] if d - 1 < len(tiers) else None
        L.append(f"## Day {d} — {kcal_of(tot):.0f} kcal"
                 + (f" — *variety caps relaxed (tier {tier})*" if tier
                    else "") + "\n")
        entries = []
        md = meal_days[d - 1] if meal_days and d - 1 < len(meal_days) \
            else None
        if md and md.get("meals"):
            # M1.9/M1.10: meal-sectioned day — every gram below comes from
            # the dealt MealDay, which conserves the solved plate exactly.
            # Per-slot serving-model phrasing (PRD §4.0 amendments):
            # portioned = packed-plate voice (container framing);
            # family_style = take-amount voice, accents attached to their
            # base (attach_accents mirrors the dealer's affinity data).
            for meal in md["meals"]:
                # M1.13: the sheet finally SAYS the dish — dish-mode meals
                # carry dish_name (+ dishes on an opt-in second dish); the
                # heading reads "slot — gorditas de picadillo — portioned".
                # Heritage meals lack the key and render byte-identically.
                dish_bit = ""
                if meal.get("dish_name"):
                    names = meal["dish_name"]
                    extra_dishes = (meal.get("dishes") or [])[1:]
                    if extra_dishes:
                        names += " + " + " + ".join(extra_dishes)
                    dish_bit = f" — {names}"
                L.append(f"### {meal['slot']}{dish_bit} — "
                         f"{meal['serving_model'].replace('_', ' ')}\n")
                servings = meal.get("servings") or {}
                for j, t_ in servings.items():
                    L.append(f"- *{j}: {t_:g} serving"
                             f"{'s' if t_ != 1 else ''}, portioned within "
                             "its ratio bands*")
                if not meal["items"]:
                    L.append("_nothing dealt to this meal_")
                elif meal["serving_model"] == "portioned":
                    parts, extras = [], []
                    for c, g in sorted(meal["items"].items(),
                                       key=lambda x: (-x[1], x[0])):
                        qty, implied = render_portion(comps[c], g, mode)
                        entries.append((c, float(g), implied))
                        parts.append(f"{comps[c]['name']} {qty}")
                        if from_freezer(comps[c], d - 1, settings, ing):
                            extras.append(comps[c]["name"])
                    L.append("- packed container: " + " + ".join(parts))
                    for name in extras:
                        L.append(f"- {name} is from the freezer that day — "
                                 "thaw ahead")
                else:
                    L.extend(_family_style_lines(meal["items"], comps, mode,
                                                 entries, d - 1, settings,
                                                 ing))
                mm = meal.get("macros") or {}
                if meal["items"]:
                    L.append(f"- meal subtotal: {mm.get('kcal', 0):.0f} kcal "
                             f"— {mm.get('protein', 0):.1f}p / "
                             f"{mm.get('fat', 0):.1f}f / "
                             f"{mm.get('carb', 0):.1f}c")
                # M19_SPEC §6 / P8: meal-level flags are never machine-only —
                # a human reading the sheet sees what the JSON knows
                # ("dinner is the heavy meal today: +32g fat — carnitas").
                for f in meal.get("flags", []):
                    if f.get("message"):
                        L.append(f"- *{f['message']}*")
                L.append("")
        elif (person.get("serving_model") or "portioned") == "family_style":
            # M1.10: day-pool take-amount voice for a family-style person
            # with no meal structure (PRD §4.0: per day otherwise)
            L.extend(_family_style_lines(pl, comps, mode, entries, d - 1,
                                         settings, ing))
            L.append("")
        else:
            for c, g in sorted(pl.items(), key=lambda x: (-x[1], x[0])):
                qty, implied = render_portion(comps[c], g, mode)
                entries.append((c, float(g), implied))
                extra = ""
                if from_freezer(comps[c], d - 1, settings, ing):
                    extra = "  — from freezer — thaw ahead"
                L.append(f"- {comps[c]['name']}: {qty}{extra}")
            L.append("")
        # One decimal on the day total (M1.4 queued minor B): identical-
        # looking lines must never disagree. The status is therefore
        # computed from the SAME 1-decimal-rounded value that is displayed
        # — exact totals straddling a band edge inside one 0.1g display
        # bucket (e.g. 94.975 vs 95.000 against a 95.0 band edge) would
        # otherwise render the same grams with different statuses. Status
        # is a pure function of (shown, target, tol), so equal-looking
        # lines are equal.
        for m in MACROS:
            shown = round(tot[m], 1)
            L.append(f"- {m}: {shown:.1f}g of {t[m]}g target — "
                     f"{_macro_status(shown, t[m], tol)}")
        if mode == "relaxed":
            bars = day_error_bars(entries, comps)
            L.append("\n> If you eyeball it: "
                     + ", ".join(
                         f"{m} {bars[m][0]}g" if bars[m][0] == bars[m][1]
                         else f"{m} {bars[m][0]}-{bars[m][1]}g"
                         for m in MACROS)
                     + " — real bounds from the rounding above, not "
                       "decoration.")
        L.append("")
    L.append(footer(meta))
    return "\n".join(L)


# --------------------------------------------------------------------------- #
#  compose + write
# --------------------------------------------------------------------------- #
def render_artifacts(comps, ing, people, settings, menu, weeks, sp,
                     purchase_rows, total_cost, pantry=None,
                     stock_warnings=None, diag=None, meta=None, meals=None,
                     methods=None, techniques=None):
    """All three deliverables as ``{filename: markdown}``. Pure composition
    of the renderers above — every input is an already-solved structure.
    ``meals`` (M1.9): meals.deal_week output ({person: [MealDay]}) — eat
    sheets render meal sections for the people present in it.
    ``methods``/``techniques`` (M1.10): methods.load_methods /
    load_techniques output — the cook plan compiles method-step fragments
    into the session script; the portioning matrix is built here as a pure
    reshape of MealDay x the canonical session attribution."""
    relax = (diag or {}).get("relax_tiers", {})
    matrix = build_portioning(sp, weeks, people, meals, comps)
    files = {
        "shopping_list.md": render_shopping_list(
            ing, purchase_rows, total_cost, pantry=pantry,
            stock_warnings=stock_warnings, meta=meta),
        "cook_plan.md": render_cook_plan(comps, settings, sp, meta=meta,
                                         methods=methods,
                                         techniques=techniques,
                                         matrix=matrix),
    }
    for pname, wk in weeks.items():
        files[f"eat_{pname}.md"] = render_eat_sheet(
            pname, people[pname], comps, wk, settings, menu, ing=ing,
            relax_tiers=relax.get(pname), meta=meta,
            meal_days=(meals or {}).get(pname))
    return files


def write_artifacts(dirpath, files):
    """Write the rendered deliverables into ``dirpath`` (created if
    needed). Returns the written paths, sorted."""
    d = Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for name, text in sorted(files.items()):
        p = d / name
        p.write_text(text)
        out.append(p)
    return out
