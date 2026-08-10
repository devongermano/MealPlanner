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
#  deliverable 2 — cook_plan.md
# --------------------------------------------------------------------------- #
def render_cook_plan(comps, settings, sp, meta=None):
    """``sp`` is costing.session_plan output — THE canonical session plan
    (M0.4/P10). This function scales recipe quantities by the session's
    batch count and formats; it never re-attributes anything."""
    budget = settings["active_min_budget"]
    L = ["# Cook plan\n"]
    over = sp["minutes"] - budget
    L.append(f"Hands-on total: **{sp['minutes']} min** vs a {budget} min "
             "weekly budget — "
             + (f"**OVER budget by {over} min** (never silently fit: cut a "
                "batch, drop a dish, or raise the budget)" if over > 0
                else f"{-over} min under budget")
             + ".\n")
    for s in sp["sessions"]:
        L.append(f"## Session {s['index']} — cook day {s['start']} — "
                 f"{s['minutes']} min hands-on\n")
        if not s["batches"]:
            L.append("_nothing to cook this session_\n")
            continue
        for cid in sorted(s["batches"]):
            b = s["batches"][cid]
            c = comps[cid]
            L.append(f"### {c['name']} × {b} batch{'es' if b != 1 else ''} "
                     f"(makes {s['made_g'][cid]:g}g, need "
                     f"{s['demand_g'][cid]:g}g)\n")
            L.append(f"Scaled for {b} batch{'es' if b != 1 else ''}:")
            for iname, grams in c["ingredients"].items():
                L.append(f"- [ ] {iname}: {grams * b:g}g")
            for step in (c.get("method") or []):
                L.append(f"1. {step}")
            L.append("")
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
    for n in sp.get("leftover", []):
        L.append(f"- LEFTOVER FIRST: {n['grams']:g}g of "
                 f"'{n['component']}' from the fridge before cooking "
                 f"(day {n['day']}) — {n['note']}")
    for u in sp["unattributed"]:
        L.append(f"- WARNING: {u['grams']}g of `{u['component']}` demanded "
                 f"on day {u['day']} but no cook session can feed that day "
                 "— run `mealplan doctor`.")
    L.append(footer(meta))
    return "\n".join(L)


# --------------------------------------------------------------------------- #
#  deliverable 3 — eat_<person>.md
# --------------------------------------------------------------------------- #
def _macro_status(total, target, tol):
    """Per-macro tolerance status: 'hit' inside the ±tol band, else the
    SIGNED gram delta vs the target ('+Ng over' / '-Ng short')."""
    lo, hi = target * (1 - tol), target * (1 + tol)
    if lo - 1e-9 <= total <= hi + 1e-9:
        return "hit"
    d = round(total - target)
    return f"+{d}g over" if d > 0 else f"-{abs(d)}g short"


def render_eat_sheet(pname, person, comps, week, settings, menu, ing=None,
                     relax_tiers=None, meta=None):
    """One person's per-day assembly sheet. ``week`` is that person's list
    of solved day plates ({cid: grams}); empty/None days are EXPLAINED
    holes, never silent. ``relax_tiers`` is diag["relax_tiers"][pname]."""
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
        for c, g in sorted(pl.items(), key=lambda x: (-x[1], x[0])):
            qty, implied = render_portion(comps[c], g, mode)
            entries.append((c, float(g), implied))
            extra = ""
            if from_freezer(comps[c], d - 1, settings, ing):
                extra = "  — from freezer — thaw ahead"
            L.append(f"- {comps[c]['name']}: {qty}{extra}")
        L.append("")
        for m in MACROS:
            L.append(f"- {m}: {tot[m]:.0f}g of {t[m]}g target — "
                     f"{_macro_status(tot[m], t[m], tol)}")
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
                     stock_warnings=None, diag=None, meta=None):
    """All three deliverables as ``{filename: markdown}``. Pure composition
    of the renderers above — every input is an already-solved structure."""
    relax = (diag or {}).get("relax_tiers", {})
    files = {
        "shopping_list.md": render_shopping_list(
            ing, purchase_rows, total_cost, pantry=pantry,
            stock_warnings=stock_warnings, meta=meta),
        "cook_plan.md": render_cook_plan(comps, settings, sp, meta=meta),
    }
    for pname, wk in weeks.items():
        files[f"eat_{pname}.md"] = render_eat_sheet(
            pname, people[pname], comps, wk, settings, menu, ing=ing,
            relax_tiers=relax.get(pname), meta=meta)
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
