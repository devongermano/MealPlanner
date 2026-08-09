"""CLI front end + report rendering. Extracted from plan.py's render/frontier/
parse_budget/main, behavior-preserving.

Commands:
    mealplan doctor                  what the library can and cannot hit, and why
    mealplan menu [--n 12]           choose this week's components
    mealplan week  [--menu a,b,c]    full 7-day plan + cook list
    mealplan shop  [--menu a,b,c]    shopping list, purchase units, waste
    mealplan all                     everything, written to plan.md
    mealplan frontier                budget sweep: what each level buys

The library location is caller-supplied via --library (default: ./examples
relative to the current directory — the package hardcodes NO repo paths, PRD P1).
"""

import argparse
import sys
from pathlib import Path

from . import instrument, io_yaml
from .costing import (attribute, budget_ceiling, menu_cost, purchase,
                      session_plan)
from .engine import (available_on, build_week, choose_menu, from_freezer,
                     reset_solve_counts, score_menu, solve_counts)
from .units import MACROS, fmt_miss, kcal_of


# --------------------------------------------------------------------------- #
#  report
# --------------------------------------------------------------------------- #
def render(comps, ing, people, settings, menu, weeks, demand, docmsg, menuinfo,
           sp, pantry=None):
    """``sp`` is the canonical session plan (costing.session_plan, M0.4/P10):
    the ONE source for the cook plan, minutes, purchasing, and cost below.
    ``pantry`` (M0.12, optional): stock is deducted from the shopping list —
    and therefore the cost — before pack rounding, inside purchase()."""
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
    L.append(f"\nHands-on total: **{sp['minutes']} min** "
             f"(budget {settings['active_min_budget']}), "
             f"{menuinfo['cuisines']} cuisines.\n")

    # cook plan — straight from the canonical session plan (M0.4). Each
    # session lists what IT cooks; purchasing and cost below consume the
    # summed per-session batches.
    L.append("## Cook plan\n")
    batches = sp["batches"]
    for s in sp["sessions"]:
        L.append(f"### Session {s['index']} — cook day {s['start']} — "
                 f"{s['minutes']} min active\n")
        if not s["batches"]:
            L.append("_nothing to cook this session_\n")
            continue
        L.append("| component | need | batches | cook | leftover |")
        L.append("|---|---|---|---|---|")
        for i in menu:
            b = s["batches"].get(i, 0)
            if not b:
                continue
            need = s["demand_g"][i]
            made = s["made_g"][i]
            L.append(f"| {comps[i]['name']} | {need}g | {b} | {made}g "
                     f"| {made-need}g |")
        for n in s["thaw_notes"]:
            L.append(f"- THAW: {n['note']}")
        for n in s.get("freezer_notes", []):
            L.append(f"- FREEZER: {n['note']}")
        L.append("")
    for u in sp["unattributed"]:
        L.append(f"- WARNING: {u['grams']}g of `{u['component']}` demanded on "
                 f"day {u['day']} but no cook session can feed that day — "
                 "run `mealplan doctor`.")

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
            L.append("_no feasible plate found — run `mealplan doctor`_")
            continue
        for d, pl in enumerate(wk, 1):
            if not pl:
                gone = [comps[i]["name"] for i in menu
                        if not available_on(comps[i], d - 1, settings, ing)]
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
                if from_freezer(comps[c], d - 1, settings, ing):
                    extra += "  — from freezer — thaw ahead"
                L.append(f"  - {comps[c]['name']}: **{g}g**{extra}")
            L.append("")

    # ---- cost + volume ----
    bought = menu_cost(comps, ing, [i for i in menu if batches.get(i)], batches,
                       pantry=pantry)
    shares, eaten = attribute(comps, ing, weeks, bought)
    cap = budget_ceiling(settings, people)
    b = settings["budget"] or {}
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

    rows, wp, wt = purchase(comps, ing, [i for i in menu if batches.get(i)],
                            batches, pantry=pantry)
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
def frontier(comps, ing, people, settings, lo, hi, step, n, seed=0, must=None):
    """What does each budget level actually buy? This is the answer to
    'how much variety can we afford' — a curve, not a number."""
    print(f"{'budget':>8} {'spend':>8} {'dishes':>7} {'cuisines':>9} "
          f"{'waste':>7} {'feasible':>9}")
    print("-" * 54)
    seen_menus = {}
    for cap in range(lo, hi + 1, step):
        st = dict(settings)
        st["budget"] = {"mode": "shared", "total": cap}
        menu, info, feas, broke = choose_menu(comps, ing, people, st, n=n,
                                              seed=seed, must=must)
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mealplan")
    ap.add_argument("cmd", choices=["doctor", "menu", "week", "shop", "all", "frontier"])
    ap.add_argument("--library", default=None, metavar="PATH",
                    help="library directory holding ingredients/components/people "
                         "yaml (default: ./examples)")
    ap.add_argument("--pantry", default=None, metavar="PATH",
                    help="optional pantry.yaml; on-hand stock is deducted from "
                         "the shopping list before pack rounding (M0.12)")
    ap.add_argument("--n", type=int, default=12,
                    help="menu size: how many components choose_menu selects "
                         "(default: %(default)s)")
    ap.add_argument("--menu", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="plan.md")
    ap.add_argument("--budget", default=None,
                    help="override: '550' for a shared pot, or 'devon=320,jimbo=240'")
    ap.add_argument("--mass", default=None, help="override: 'jimbo=2200'")
    ap.add_argument("--exclude", default="", help="components to keep off the menu")
    ap.add_argument("--force", default="", help="components that must be on the menu")
    ap.add_argument("--range", default="400:700:50", help="frontier sweep lo:hi:step")
    ap.add_argument("--stats", action="store_true",
                    help="after the command, print LP-solve counts by stage "
                         "and wall-clock stage timings to stderr (M0.14)")
    a = ap.parse_args(argv)

    # M0.14: counts are deterministic engine bookkeeping; timing wraps the
    # engine calls from OUT HERE (instrument.py) and never feeds them.
    reset_solve_counts()
    timer = instrument.StageTimer()
    try:
        _run(a, timer)
    finally:
        if a.stats:
            print(instrument.format_stats(solve_counts(), timer.spans),
                  file=sys.stderr)


def _run(a, timer):
    lib = Path(a.library) if a.library else Path.cwd() / "examples"
    try:
        with timer.span("load"):
            ing, comps, people, settings = io_yaml.load(lib)
    except io_yaml.ValidationError as e:
        sys.exit(str(e))
    pantry = None
    if a.pantry:
        try:
            pantry = io_yaml.load_pantry(a.pantry,
                                         known_ingredients=set(ing),
                                         known_components=set(comps))
        except io_yaml.ValidationError as e:
            sys.exit(str(e))
    if a.budget:
        settings["budget"] = parse_budget(a.budget)
    if a.mass:
        for kv in a.mass.split(","):
            k, v = kv.split("=")
            people[k]["max_daily_mass_g"] = float(v)
    for cid in [x for x in a.exclude.split(",") if x]:
        comps.pop(cid, None)
    # M0.5: --force is WIRED — forced components ride through choose_menu's
    # ``must`` list; unknown ids (or ids just removed by --exclude) are a
    # CLI error naming them.
    force = [x.strip() for x in a.force.split(",") if x.strip()]
    unknown_forced = [x for x in force if x not in comps]
    if unknown_forced:
        sys.exit(f"unknown components in --force: {', '.join(unknown_forced)}")
    if a.cmd == "frontier":
        lo, hi, st = (int(x) for x in a.range.split(":"))
        with timer.span("frontier"):
            frontier(comps, ing, people, settings, lo, hi, st, a.n,
                     seed=a.seed, must=force)
        return
    from .engine import doctor as _doctor
    with timer.span("doctor"):
        docmsg, _ = _doctor(comps, people, settings, ing=ing)

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
        with timer.span("choose_menu"):
            menu, menuinfo, feas, broke = choose_menu(comps, ing, people,
                                                      settings, n=a.n,
                                                      seed=a.seed, must=force)
        if not feas:
            print("!! best menu found is not feasible for everyone:", file=sys.stderr)
            for who, miss in broke.items():
                print(f"   {who}: {fmt_miss(miss)}", file=sys.stderr)
            # PRD §8.3 playbook: cheapest STRUCTURAL fix first; loosening
            # tolerance is the explicit last resort.
            print("   -> add a component that fixes the gap (run `mealplan "
                  "doctor` — it names the class), raise --n, or move a cook "
                  "day. loosening tolerance in people.yaml is the LAST "
                  "resort: it redefines success instead of fixing the "
                  "plan.\n", file=sys.stderr)

    if a.cmd == "menu":
        for i in menu:
            print(f"{i:24s} {comps[i]['cuisine']:9s} {comps[i]['role']}")
        print(f"\nactive {menuinfo['active_min']}min, "
              f"perishable waste {menuinfo['waste_perishable']}g, "
              f"cuisines {menuinfo['cuisines']}")
        return

    with timer.span("build_week"):
        weeks, demand = build_week(comps, people, settings, menu, seed=a.seed,
                                   ing=ing)
    with timer.span("session_plan"):
        sp = session_plan(comps, ing, settings, weeks)
    with timer.span("render"):
        out = render(comps, ing, people, settings, menu, weeks, demand, docmsg,
                     menuinfo, sp, pantry=pantry)

    if a.cmd == "shop":
        print(out.split("## Shopping list")[1])
        return
    Path(a.out).write_text(out)
    print(out)
    print(f"\n\n[written to {a.out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
