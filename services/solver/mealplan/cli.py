"""CLI front end + report rendering. Extracted from plan.py's render/frontier/
parse_budget/main, behavior-preserving.

Commands:
    mealplan doctor                  what the library can and cannot hit, and why
    mealplan menu [--n 12]           choose this week's components
    mealplan week  [--menu a,b,c]    full 7-day plan + cook list
    mealplan shop  [--menu a,b,c]    shopping list, purchase units, waste
    mealplan all                     everything, written to plan.md
    mealplan frontier                budget sweep: what each level buys
    mealplan lock --date D           M1.3: solve + write the immutable locked
                                     plan artifact under plans/<key>/
    mealplan verify-plan PATH        M1.3: re-solve a locked plan from its
                                     embedded snapshot; check hash + portions

The library location is caller-supplied via --library (default: ./examples
relative to the current directory — the package hardcodes NO repo paths, PRD P1).

--json contract (M1.4, PRD §8.4): every command accepts --json. stdout then
carries EXACTLY ONE JSON document::

    {"schema": "mealplan/v2", "command": <name>, "ok": <bool>,
     "result": {...}}        # or "error": {...} instead of "result"

``result`` carries the SAME engine/costing structures the renderer consumes
(P10: JSON-safe conversion only — serialize.jsonable — no reshaping). All
human logs and warnings go to stderr. ``ok`` is true iff the exit code is 0.

Exit codes (PRD §8.4) — "computed but infeasible" is a RESULT, not an error:
    0   ok
    2   computed but infeasible — the JSON document is still emitted, with
        feasible flags and directional misses inside
    3   validation / structured error (JSON error document carries the
        complete all-errors issues list)
    4   bad arguments (also used by argparse itself — never 2, which is
        reserved for infeasibility)

Non-json behavior is unchanged from M0/M1.1 for the pre-existing commands;
the new lock/verify-plan commands use the §8.4 exit codes in both modes.
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

from . import artifacts, instrument, io_yaml, lockplan, meals
from . import dishes as dishes_mod
from . import methods as methods_mod
from .costing import (age_pantry, attribute, budget_ceiling, cooked_leftovers,
                      menu_cost, purchase, session_plan)
from .engine import (available_on, build_week, choose_menu, from_freezer,
                     reset_solve_counts, score_menu, solve_counts)
from .model import Pantry
from .serialize import canonical_json, jsonable
from .units import MACROS, fmt_miss, kcal_of

EXIT_OK = 0
EXIT_INFEASIBLE = 2      # computed-but-infeasible: a result, not an error
EXIT_ERROR = 3           # validation / structured error
EXIT_USAGE = 4           # bad arguments

JSON_SCHEMA = "mealplan/v2"


class CliError(Exception):
    """Structured CLI error (PRD §8.4).

    --json mode: rendered as the single JSON error document on stdout and
    the process exits with ``exit_code`` (3 or 4). Non-json mode: the
    pre-existing commands keep their historical ``sys.exit(message)``
    behavior byte-for-byte; the NEW commands (lock, verify-plan) print the
    message to stderr and exit with the contract code in both modes.
    """

    def __init__(self, code, message, exit_code=EXIT_ERROR, issues=None,
                 details=None):
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.issues = list(issues or [])
        self.details = details
        super().__init__(message)


class _Parser(argparse.ArgumentParser):
    """argparse's default bad-argument exit code is 2 — which PRD §8.4
    reserves for computed-but-infeasible. Bad arguments exit 4."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


# --------------------------------------------------------------------------- #
#  report
# --------------------------------------------------------------------------- #
def render(comps, ing, people, settings, menu, weeks, demand, docmsg, menuinfo,
           sp, pantry=None, diag=None, meals=None, methods=None,
           techniques=None, dish_menu=None, dishes=None):
    """``sp`` is the canonical session plan (costing.session_plan, M0.4/P10):
    the ONE source for the cook plan, minutes, purchasing, and cost below.
    ``pantry`` (M0.12, optional): stock is deducted from the shopping list —
    and therefore the cost — before pack rounding, inside purchase().
    ``diag`` (P8, optional): build_week's diagnostics dict. When present,
    every day the relaxation ladder loosened the variety caps for is flagged
    inline and summarized up top — no silent caps, drops, or relaxations.
    ``meals``/``methods``/``techniques`` (M1.10): the cook-plan section is
    the SAME compiled session script the cook_plan.md artifact renders
    (artifacts.cook_script_lines — one renderer, two outputs, no
    divergence), including shared-prep consolidation and the portioning
    matrix when meal structure exists."""
    relax = (diag or {}).get("relax_tiers", {})
    L = ["# Week plan\n", docmsg]
    # M1.13: in dish mode the menu IS dishes — say so first (the sheet
    # finally SAYS the dish); the component closure follows as the batch
    # view. Heritage renders byte-identically (dish_menu None).
    if dish_menu and dishes:
        from .dishes import core_members, dish_cuisine
        L.append("\n## Dishes\n")
        for j in dish_menu:
            d = dishes[j]
            rec = d.get("reconstruction") or "?"
            L.append(
                f"- **{d.get('name') or j}** (`{j}`) — "
                f"{dish_cuisine(d, comps) or '?'}, {rec} — "
                + " + ".join(core_members(d))
                + (f" — accents: {', '.join(d['accents'])}"
                   if d["accents"] else "")
                + (f" — sides: {', '.join(d['compatible_sides'])}"
                   if d["compatible_sides"] else ""))
        L.append("\n## Components (batch closure)\n")
    else:
        L.append("\n## Menu\n")
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

    # P8: no silent relaxations — if any day's plate only exists because the
    # variety caps were loosened, the plan says so up front.
    relaxed_days = sorted((pn, d, t)
                          for pn, tiers in relax.items()
                          for d, t in enumerate(tiers) if t)
    if relaxed_days:
        L.append("> **Variety caps relaxed** (P8: solver relaxations are "
                 "never silent): "
                 + "; ".join(f"{pn} day {d + 1} (tier {t})"
                             for pn, d, t in relaxed_days)
                 + ". Tier 1 allows +1 repeat day/batch per component, "
                   "tier 2 removes the day cap and allows +2 batches.\n")

    # cook plan — straight from the canonical session plan (M0.4), compiled
    # by THE one session-script renderer (M1.10: artifacts.cook_script_lines
    # — the same code path as cook_plan.md, so the two can never diverge).
    L.append("## Cook plan\n")
    batches = sp["batches"]
    matrix = artifacts.build_portioning(sp, weeks, people, meals, comps)
    script, used_ops = artifacts.cook_script_lines(
        comps, settings, sp, methods=methods, techniques=techniques,
        matrix=matrix, h="###")
    L.extend(script)
    # M1.8: demand fed from cooked pantry leftovers (already paid for —
    # consumed before any fresh batch)
    for n in sp.get("leftover", []):
        L.append(f"- LEFTOVER: {n['note']}")
    for u in sp["unattributed"]:
        L.append(f"- WARNING: {u['grams']}g of `{u['component']}` demanded on "
                 f"day {u['day']} but no cook session can feed that day — "
                 "run `mealplan doctor`.")
    L.extend(artifacts.technique_glossary_lines(used_ops, techniques,
                                                h="###"))

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
            tiers = relax.get(pname) or []
            tier = tiers[d - 1] if d - 1 < len(tiers) else None
            L.append(f"**Day {d}** — {kcal_of(tot):.0f} kcal, "
                     f"{tot['protein']:.0f}p / {tot['fat']:.0f}f / {tot['carb']:.0f}c"
                     + (f" — *variety caps relaxed (tier {tier})*"
                        if tier else ""))
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
def frontier(comps, ing, people, settings, lo, hi, step, n, seed=0, must=None,
             echo=True, rows=None):
    """What does each budget level actually buy? This is the answer to
    'how much variety can we afford' — a curve, not a number.

    ``echo`` prints the historical table to stdout; pass ``rows`` (a list)
    to also collect one structured dict per budget point (--json, M1.4 —
    same numbers the table shows, no reshaping)."""
    lines = [f"{'budget':>8} {'spend':>8} {'dishes':>7} {'cuisines':>9} "
             f"{'waste':>7} {'feasible':>9}",
             "-" * 54]
    seen_menus = {}
    for cap in range(lo, hi + 1, step):
        st = dict(settings)
        st["budget"] = {"mode": "shared", "total": cap}
        menu, info, feas, broke = choose_menu(comps, ing, people, st, n=n,
                                              seed=seed, must=must)
        spend = menu_cost(comps, ing, menu, people=people, settings=st)
        mains = len([i for i in menu if comps[i]["role"] in ("main", "starch")])
        lines.append(f"{cap:>8} {spend:>8.0f} {mains:>7} {info['cuisines']:>9} "
                     f"{info['waste_perishable']:>6}g {'yes' if feas else 'NO':>9}")
        seen_menus[cap] = (menu, feas)
        if rows is not None:
            rows.append(dict(budget=cap, spend=spend, dishes=mains,
                             cuisines=info["cuisines"],
                             waste_perishable_g=info["waste_perishable"],
                             feasible=feas, misses=broke, menu=menu))
    if echo:
        print("\n".join(lines))
    return seen_menus


def parse_budget(txt):
    if not txt:
        return None
    if "=" in txt:
        pp = dict(kv.split("=") for kv in txt.split(","))
        return {"mode": "per_person", "per_person": {k: float(v) for k, v in pp.items()}}
    return {"mode": "shared", "total": float(txt)}


# --------------------------------------------------------------------------- #
#  --json plumbing (M1.4)
# --------------------------------------------------------------------------- #
def _emit_json(command, ok, result=None, error=None):
    """The ONE stdout write in --json mode: exactly one JSON document."""
    doc = {"schema": JSON_SCHEMA, "command": command, "ok": bool(ok)}
    if error is not None:
        doc["error"] = error
    else:
        doc["result"] = jsonable(result)
    print(json.dumps(doc, indent=2, allow_nan=False))


def _issue_dicts(issues):
    return [dict(code=i.code, where=i.where, message=i.message,
                 severity=i.severity) for i in issues]


# --------------------------------------------------------------------------- #
#  shared pipeline pieces — used by the CLI path AND verify-plan's re-solve
#  from a locked snapshot, so the two can never drift (P10)
# --------------------------------------------------------------------------- #
def _pantry_effects(pantry, ing, comps, settings, plan_date):
    """M1.8 aging + cooked leftovers. Returns (pantry_eff, leftovers,
    stock_warnings); warnings also go to stderr, never stdout."""
    leftovers, stock_warns = [], []
    if pantry is not None and (pantry.stock or pantry.cooked):
        if plan_date is None:
            raise CliError(
                "date_required",
                "--date YYYY-MM-DD is required when the pantry has "
                "stock or cooked leftovers: stock aging and leftover "
                "residual life are computed relative to the plan start "
                "date, and the engine reads no wall clock (M1.8, "
                "PRD §8.1)", EXIT_USAGE)
        try:
            pantry_eff, stock_warnings = age_pantry(pantry, ing, settings,
                                                   plan_date)
        except ValueError as e:
            raise CliError("bad_pantry", str(e), EXIT_ERROR)
        leftovers, leftover_warnings = cooked_leftovers(pantry, comps,
                                                        settings, plan_date)
        for w in stock_warnings + leftover_warnings:
            print(f"[warning:{w['code']}] {w['message']}", file=sys.stderr)
        pantry = pantry_eff
        stock_warns = stock_warnings
    return pantry, leftovers, stock_warns


def _load_methods(methods_arg, comps):
    """M1.10: resolve + load the method-fragment directory and technique
    library for the compiled cook script. Explicit ``--methods PATH`` must
    exist (bad arguments, exit 4); the default (./data/methods-draft
    relative to the cwd — no hardcoded package paths, P1) applies only when
    present. Missing/invalid content degrades gracefully: structured
    warnings on stderr, components without fragments keep ingredient-list
    rendering. Returns ``(methods, techniques)`` — (None, None) when no
    directory resolves."""
    if methods_arg:
        mdir = Path(methods_arg)
        if not mdir.is_dir():
            raise CliError(
                "missing_methods_dir",
                f"--methods {methods_arg}: not a directory", EXIT_USAGE)
    else:
        mdir = methods_mod.default_methods_dir()
        if mdir is None:
            return None, None
    techniques, twarns = methods_mod.load_techniques(
        methods_mod.default_techniques_path(mdir))
    fragments, mwarns = methods_mod.load_methods(
        mdir, known_components=set(comps),
        known_operations=set(techniques) if techniques else None)
    for w in twarns + mwarns:
        print(f"[warning:{w['code']}] {w['message']}", file=sys.stderr)
    return fragments, techniques


def _apply_overrides(comps, people, settings, budget, mass, exclude, force,
                     bad_args_exit=EXIT_USAGE, extra_ids=()):
    """Budget/mass/exclude/force overrides, exactly the historical order.
    Returns the validated forced-component list. ``extra_ids`` (M1.13):
    additional valid --force ids — dish ids in dish mode (a forced
    component id maps through dish membership downstream, §3)."""
    if budget:
        settings["budget"] = parse_budget(budget)
    if mass:
        for kv in mass.split(","):
            k, v = kv.split("=")
            people[k]["max_daily_mass_g"] = float(v)
    for cid in [x for x in (exclude or "").split(",") if x]:
        comps.pop(cid, None)
    # M0.5: --force is WIRED — forced components ride through choose_menu's
    # ``must`` list; unknown ids (or ids just removed by --exclude) are a
    # CLI error naming them.
    forced = [x.strip() for x in (force or "").split(",") if x.strip()]
    unknown_forced = [x for x in forced
                      if x not in comps and x not in extra_ids]
    if unknown_forced:
        raise CliError(
            "unknown_component",
            f"unknown components in --force: {', '.join(unknown_forced)}",
            bad_args_exit)
    return forced


def _load_dish_layer(dishes_arg, implicit, lib, comps, people,
                     bad_args_exit=EXIT_USAGE):
    """M1.13: resolve + load the dish layer. The MODE KEY is the presence
    of dishes.yaml (M113_SPEC §1): explicit ``--dishes PATH`` must exist
    (bad arguments); the default ``<library>/dishes.yaml`` applies only
    when present. Returns ``{dish_id: Dish}`` or None (heritage mode —
    the entire layer dormant, pipeline byte-identical)."""
    path = Path(dishes_arg) if dishes_arg else lib / "dishes.yaml"
    if dishes_arg and not path.exists():
        raise CliError("missing_dishes_file",
                       f"--dishes {dishes_arg}: file not found",
                       bad_args_exit)
    dishes_map = None
    if path.exists():
        try:
            dishes_map = io_yaml.load_dishes(path, comps=comps,
                                             people=people)
        except io_yaml.ValidationError as e:
            raise CliError("invalid_dishes", str(e), EXIT_ERROR,
                           issues=_issue_dicts(e.issues))
    if implicit:
        extra, warns = dishes_mod.implicit_dishes(comps,
                                                  existing=dishes_map)
        for w in warns:
            print(f"[warning:{w['code']}] {w['message']}", file=sys.stderr)
        dishes_map = {**(dishes_map or {}), **extra}
    return dishes_map or None


def _check_menu_size(n, comps, exit_code=EXIT_USAGE):
    """Queued minor A: --n larger than the eligible library is a friendly
    structured error naming both numbers — never a random.sample traceback."""
    if n > len(comps):
        raise CliError(
            "n_exceeds_library",
            f"--n {n} asks for a {n}-component menu but only {len(comps)} "
            f"component{'s are' if len(comps) != 1 else ' is'} eligible "
            "(after any --exclude) — lower --n, trim --exclude, or add "
            "components to the library", exit_code)


def _resolve_menu(comps, ing, people, settings, menu_arg, n, seed, force,
                  timer, bad_args_exit=EXIT_USAGE):
    """Explicit --menu (scored, feasibility not solved -> feasible=None) or
    seeded choose_menu. Infeasibility advice goes to stderr (PRD §8.3)."""
    if menu_arg:
        menu = [x.strip() for x in menu_arg.split(",")]
        unknown = [x for x in menu if x not in comps]
        if unknown:
            raise CliError("unknown_component",
                           f"unknown components: {unknown}", bad_args_exit)
        _, menuinfo = score_menu(comps, ing, menu, settings)
        return menu, menuinfo, None, {}
    _check_menu_size(n, comps, exit_code=bad_args_exit)
    with timer.span("choose_menu"):
        menu, menuinfo, feas, broke = choose_menu(comps, ing, people,
                                                  settings, n=n,
                                                  seed=seed, must=force)
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
    return menu, menuinfo, feas, broke


def _resolve_menu_dishes(comps, ing, people, settings, dishes_map, menu_arg,
                         n, seed, force, timer, bad_args_exit=EXIT_USAGE):
    """Dish-mode menu resolution (M1.13, M113_SPEC §3): explicit --menu
    names DISHES; the seeded search is choose_menu_dishes. Returns
    ``(dish_menu, menuinfo, feas, broke)`` — ``menuinfo['closure']`` is
    the derived component closure everything downstream runs on."""
    if menu_arg:
        menu = [x.strip() for x in menu_arg.split(",")]
        unknown = [x for x in menu if x not in dishes_map]
        if unknown:
            raise CliError(
                "unknown_dish",
                f"unknown dishes: {unknown} — in dish mode --menu names "
                f"dish ids from dishes.yaml", bad_args_exit)
        _, menuinfo = dishes_mod.score_menu_dishes(comps, ing, dishes_map,
                                                   menu, settings, people)
        return menu, menuinfo, None, {}
    # n means DISHES here; choose_menu_dishes clamps n to the corpus size
    # (a small dish library must not brick the default --n)
    with timer.span("choose_menu"):
        menu, menuinfo, feas, broke = dishes_mod.choose_menu_dishes(
            comps, ing, people, settings, dishes_map, n=n, seed=seed,
            must=force)
    if not feas:
        print("!! best dish menu found is not feasible for everyone:",
              file=sys.stderr)
        for who, miss in broke.items():
            print(f"   {who}: {fmt_miss(miss)}", file=sys.stderr)
        print("   -> run `mealplan doctor` — the dish section names the "
              "gap (eligibility kill, dead dish, slot capacity); widen a "
              "band someone would actually eat, author a heartier "
              "compatible side, or raise --n. loosening tolerance is the "
              "LAST resort.\n", file=sys.stderr)
    return menu, menuinfo, feas, broke


def _assemble_dishes(comps, people, settings, dishes_map, menu, seed, ing,
                     leftovers, timer):
    """Dish-mode assembly (M113_SPEC §1 steps 2–4): skeleton + one
    dish-blocked LP per person-day; MealDay emitted directly from the
    solve — no dealer pass. Session plan runs on the derived component
    demand unchanged (downstream blindness)."""
    diag = {}
    with timer.span("build_week"):
        weeks, demand, mealdays = dishes_mod.build_week_dishes(
            comps, people, settings, dishes_map, menu, seed=seed, ing=ing,
            diag=diag, leftovers=leftovers)
    with timer.span("session_plan"):
        sp = session_plan(comps, ing, settings, weeks, leftovers=leftovers)
    return weeks, demand, sp, diag, mealdays


def _assemble(comps, people, settings, menu, seed, ing, leftovers, timer):
    diag = {}      # P8: relaxation-ladder tiers surface in the rendered plan
    with timer.span("build_week"):
        weeks, demand = build_week(comps, people, settings, menu, seed=seed,
                                   ing=ing, diag=diag, leftovers=leftovers)
    with timer.span("session_plan"):
        sp = session_plan(comps, ing, settings, weeks, leftovers=leftovers)
    # M1.9: deal each configured person-day into composed meals — zero LP
    # solves (the meal-alloc stage is timing only). Empty dict when nobody
    # configures meals: the layer is inert, pipeline byte-identical.
    with timer.span("meal-alloc"):
        mealdays = meals.deal_week(people, comps, weeks)
    return weeks, demand, sp, diag, mealdays


def _solve_from_snapshot(snap, timer):
    """Re-solve EXACTLY the pipeline `lock` ran, from a locked plan's
    embedded inputs snapshot (M1.3 reproducibility). Every step reuses the
    shared helpers above — the verify path cannot drift from the CLI path."""
    lib_docs = snap["library"]
    try:
        ing, comps, people, settings = io_yaml.load_docs(
            lib_docs["ingredients"], lib_docs["components"],
            lib_docs["people"])
    except io_yaml.ValidationError as e:
        raise CliError("invalid_snapshot",
                       f"snapshot library does not validate: {e}",
                       EXIT_ERROR, issues=_issue_dicts(e.issues))
    pantry = None
    if snap.get("pantry") is not None:
        issues = io_yaml.validate_pantry_doc(
            snap["pantry"], known_ingredients=set(ing),
            known_components=set(comps))
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            raise CliError("invalid_snapshot",
                           "snapshot pantry does not validate",
                           EXIT_ERROR, issues=_issue_dicts(issues))
        pantry = Pantry.from_raw(snap["pantry"])
    plan_date = datetime.date.fromisoformat(snap["plan_date"])
    seed = snap["seed"]
    ov = snap.get("overrides") or {}
    # M1.13: a snapshot carrying a dishes document re-solves in dish mode —
    # the snapshot IS the mode key, exactly like the library directory
    dishes_map = None
    if lib_docs.get("dishes") is not None:
        issues = io_yaml.validate_dishes_doc(lib_docs["dishes"],
                                             comps=comps, people=people)
        if [i for i in issues if i.severity == "error"]:
            raise CliError("invalid_snapshot",
                           "snapshot dishes document does not validate",
                           EXIT_ERROR, issues=_issue_dicts(issues))
        from .model import Dish
        dishes_map = {d["id"]: Dish.from_raw(d)
                      for d in lib_docs["dishes"]["dishes"]}
    if ov.get("implicit_dishes"):
        extra, _warns = dishes_mod.implicit_dishes(comps,
                                                   existing=dishes_map)
        dishes_map = {**(dishes_map or {}), **extra}
    pantry, leftovers, _ = _pantry_effects(pantry, ing, comps, settings,
                                           plan_date)
    force = _apply_overrides(comps, people, settings, ov.get("budget"),
                             ov.get("mass"), ov.get("exclude"),
                             ov.get("force"), bad_args_exit=EXIT_ERROR,
                             extra_ids=set(dishes_map or ()))
    if dishes_map:
        menu, menuinfo, feas, broke = _resolve_menu_dishes(
            comps, ing, people, settings, dishes_map, ov.get("menu"),
            ov.get("n"), seed, force, timer, bad_args_exit=EXIT_ERROR)
        weeks, demand, sp, diag, mealdays = _assemble_dishes(
            comps, people, settings, dishes_map, menu, seed, ing,
            leftovers, timer)
    else:
        menu, menuinfo, feas, broke = _resolve_menu(
            comps, ing, people, settings, ov.get("menu"), ov.get("n"),
            seed, force, timer, bad_args_exit=EXIT_ERROR)
        weeks, demand, sp, diag, mealdays = _assemble(comps, people,
                                                      settings, menu, seed,
                                                      ing, leftovers, timer)
    return dict(menu=menu, menuinfo=menuinfo, feasible=feas, misses=broke,
                weeks=weeks, demand=demand, sp=sp, diag=diag,
                meals=mealdays)


# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = _Parser(prog="mealplan")
    ap.add_argument("cmd", choices=["doctor", "menu", "week", "shop", "all",
                                    "frontier", "lock", "verify-plan"])
    ap.add_argument("plan", nargs="?", default=None, metavar="PLAN_YAML",
                    help="verify-plan only: path to a locked "
                         "plans/<key>/plan.yaml")
    ap.add_argument("--library", default=None, metavar="PATH",
                    help="library directory holding ingredients/components/people "
                         "yaml (default: ./examples)")
    ap.add_argument("--pantry", default=None, metavar="PATH",
                    help="optional pantry.yaml; on-hand stock is deducted from "
                         "the shopping list before pack rounding (M0.12), "
                         "aged by --date (M1.8); cooked leftovers join "
                         "availability and are eaten before fresh batches")
    ap.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                    help="plan start date (ISO). REQUIRED when the pantry "
                         "has stock or cooked leftovers — aging and residual "
                         "life are computed relative to it; the engine reads "
                         "no wall clock (M1.8). REQUIRED for lock: the "
                         "artifact key is the primary trip date (PRD §8.2) "
                         "= this date advanced by sorted(shop_days)[0] days")
    ap.add_argument("--dishes", default=None, metavar="PATH",
                    help="dishes.yaml for THE DISH LAYER (M1.13, PRD §4.0): "
                         "meals become one dish portioned within its ratio "
                         "bands plus compatible sides; menu selection picks "
                         "dishes. Default: <library>/dishes.yaml when it "
                         "exists — the file's PRESENCE is the mode key; "
                         "without one the entire layer is dormant and the "
                         "pipeline is byte-identical heritage")
    ap.add_argument("--implicit-dishes", action="store_true",
                    help="synthesize one dish per legacy main not covered "
                         "by dishes.yaml (M113_SPEC §9 migration shim): "
                         "main + pairs_with accents, all starch/veg as "
                         "compatible sides, bands from serve_g "
                         "min/midpoint/max — every synthesized dish is "
                         "flagged implicit_dish")
    ap.add_argument("--diagnose", action="store_true",
                    help="also run the full doctor diagnostics before "
                         "week/menu/shop/all and include the report (§8.3: "
                         "diagnostics are on demand, no longer implicit in "
                         "every command)")
    ap.add_argument("--n", type=int, default=12,
                    help="menu size: how many components choose_menu selects "
                         "(default: %(default)s)")
    ap.add_argument("--menu", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None,
                    help="week/all only: where the rendered plan is written "
                         "(default: plan.md; ignored with --json — the JSON "
                         "document on stdout IS the output)")
    ap.add_argument("--methods", default=None, metavar="PATH",
                    help="method-fragment directory for the compiled cook "
                         "script (M1.10, PRD §6: recipes are ground truth, "
                         "never rendered — cook plans compile these step "
                         "fragments per session). Default: ./data/"
                         "methods-draft relative to the cwd when it exists; "
                         "otherwise cook plans render ingredient lists "
                         "only. Techniques resolve from "
                         "<PATH>/../techniques/techniques.yaml")
    ap.add_argument("--artifacts", default=None, metavar="DIR",
                    help="week/all only: also write the three human-readable "
                         "deliverables (M1.1, PRD §4.3 step 4) into DIR — "
                         "shopping_list.md, cook_plan.md, and one "
                         "eat_<person>.md per person")
    ap.add_argument("--budget", default=None,
                    help="override: '550' for a shared pot, or 'alice=320,bob=240'")
    ap.add_argument("--mass", default=None, help="override: 'alice=2200'")
    ap.add_argument("--exclude", default="", help="components to keep off the menu")
    ap.add_argument("--force", default="", help="components that must be on the menu")
    ap.add_argument("--range", default="400:700:50", help="frontier sweep lo:hi:step")
    ap.add_argument("--json", action="store_true",
                    help="emit exactly one mealplan/v2 JSON document on "
                         "stdout (M1.4, PRD §8.4); all human logs go to "
                         "stderr. Exit codes: 0 ok, 2 computed-but-"
                         "infeasible (document still emitted), 3 validation/"
                         "structured error, 4 bad arguments")
    ap.add_argument("--plans", default="plans", metavar="DIR",
                    help="lock only: root directory for locked plan "
                         "artifacts (default: ./plans — git-tracked data; "
                         "the artifact IS the record). The plan lands in "
                         "DIR/<primary-trip-date>/")
    ap.add_argument("--supersede", action="store_true",
                    help="lock only: if a plan already exists at the key, "
                         "RENAME it to plan.superseded-<n>.yaml (content "
                         "untouched — plans are immutable, PRD §8.1) and "
                         "lock a new plan.yaml that names what it "
                         "superseded. Without this flag an existing plan "
                         "makes lock refuse (exit 3)")
    ap.add_argument("--stats", action="store_true",
                    help="after the command, print LP-solve counts by stage "
                         "and wall-clock stage timings to stderr (M0.14)")
    a = ap.parse_args(argv)

    # M0.14: counts are deterministic engine bookkeeping; timing wraps the
    # engine calls from OUT HERE (instrument.py) and never feeds them.
    reset_solve_counts()
    timer = instrument.StageTimer()
    try:
        try:
            _run(a, timer)
        except CliError as e:
            if a.json:
                err = {"code": e.code, "message": e.message,
                       "issues": e.issues}
                if e.details is not None:
                    err["details"] = jsonable(e.details)
                _emit_json(a.cmd, False, error=err)
                raise SystemExit(e.exit_code)
            if a.cmd in ("lock", "verify-plan"):
                # new commands use the §8.4 exit codes in both modes
                print(f"error: {e.message}", file=sys.stderr)
                raise SystemExit(e.exit_code)
            sys.exit(e.message)   # pre-M1.4 commands: behavior unchanged
    finally:
        if a.stats:
            print(instrument.format_stats(solve_counts(), timer.spans),
                  file=sys.stderr)


def _run(a, timer):
    if a.cmd == "verify-plan":
        _cmd_verify_plan(a, timer)
        return

    lib = Path(a.library) if a.library else Path.cwd() / "examples"
    try:
        with timer.span("load"):
            ing, comps, people, settings = io_yaml.load(lib)
    except io_yaml.ValidationError as e:
        raise CliError("invalid_library", str(e), EXIT_ERROR,
                       issues=_issue_dicts(e.issues))
    # M1.10: method fragments + techniques for the compiled cook script —
    # loaded against the FULL library (before --exclude), rendering-only
    fragments, techniques = _load_methods(a.methods, comps)
    pantry = None
    if a.pantry:
        try:
            pantry = io_yaml.load_pantry(a.pantry,
                                         known_ingredients=set(ing),
                                         known_components=set(comps))
        except io_yaml.ValidationError as e:
            raise CliError("invalid_pantry", str(e), EXIT_ERROR,
                           issues=_issue_dicts(e.issues))
    # M1.8: the plan start date is an explicit input — no wall-clock default
    plan_date = None
    if a.date:
        try:
            plan_date = datetime.date.fromisoformat(a.date)
        except ValueError:
            raise CliError("bad_date",
                           f"--date must be an ISO date (YYYY-MM-DD), "
                           f"got {a.date!r}", EXIT_USAGE)
    if a.cmd == "lock" and plan_date is None:
        raise CliError(
            "date_required",
            "--date YYYY-MM-DD is REQUIRED for lock: the artifact key is "
            "the primary trip date — the plan start date advanced by "
            "sorted(shop_days)[0] days (PRD §8.2) — and the engine reads "
            "no wall clock", EXIT_USAGE)
    pantry, leftovers, stock_warns = _pantry_effects(pantry, ing, comps,
                                                     settings, plan_date)
    # M1.13: dish mode is keyed on the PRESENCE of dishes.yaml — loaded
    # against the full corpus, BEFORE --exclude trims components (a dish
    # whose core member is excluded dies later with the killer named).
    dishes_map = _load_dish_layer(a.dishes, a.implicit_dishes, lib, comps,
                                  people)
    force = _apply_overrides(comps, people, settings, a.budget, a.mass,
                             a.exclude, a.force,
                             extra_ids=set(dishes_map or ()))
    if a.cmd == "frontier":
        if dishes_map:
            # dish-mode budget frontier is a future task — never silently
            # pretend the sweep understood dishes
            print("[note] frontier sweeps the COMPONENT library; the dish "
                  "layer is ignored for this command (dish-mode frontier "
                  "is not built yet)", file=sys.stderr)
        lo, hi, st = (int(x) for x in a.range.split(":"))
        _check_menu_size(a.n, comps)
        rows = [] if a.json else None
        with timer.span("frontier"):
            frontier(comps, ing, people, settings, lo, hi, st, a.n,
                     seed=a.seed, must=[x for x in force if x in comps],
                     echo=not a.json, rows=rows)
        if a.json:
            _emit_json("frontier", True, result={"range": [lo, hi, st],
                                                 "points": rows})
        return
    # M1.0 (§8.3): diagnostics run ON DEMAND — the doctor command and the
    # --diagnose flag — never implicitly before every write/render (the old
    # always-run cost was ~2.7s per command on the examples corpus).
    docmsg, docdata = "", None
    if a.cmd == "doctor" or a.diagnose:
        from .engine import doctor as _doctor
        with timer.span("doctor"):
            docmsg, docdata = _doctor(comps, people, settings, ing=ing,
                                      dishes=dishes_map)

    if a.cmd == "doctor":
        if a.json:
            _emit_json("doctor", True, result={"report": docmsg,
                                               "data": docdata})
        else:
            print(docmsg)
        return

    if dishes_map:
        menu, menuinfo, feas, broke = _resolve_menu_dishes(
            comps, ing, people, settings, dishes_map, a.menu, a.n, a.seed,
            force, timer)
        comp_menu = menuinfo["closure"]
    else:
        menu, menuinfo, feas, broke = _resolve_menu(
            comps, ing, people, settings, a.menu, a.n, a.seed, force, timer)
        comp_menu = menu

    if a.cmd == "menu":
        if a.json:
            result = {"menu": menu, "menu_info": menuinfo,
                      "feasible": feas, "misses": broke}
            if dishes_map:
                result["dish_mode"] = True
                result["closure"] = comp_menu
            _emit_json("menu", feas is not False, result=result)
            if feas is False:
                raise SystemExit(EXIT_INFEASIBLE)
            return
        if dishes_map:
            from .dishes import dish_cuisine
            for j in menu:
                print(f"{j:24s} {dish_cuisine(dishes_map[j], comps) or '?':9s} "
                      "dish")
            print(f"\ncomponent closure ({len(comp_menu)}): "
                  + ", ".join(comp_menu))
        else:
            for i in menu:
                print(f"{i:24s} {comps[i]['cuisine']:9s} {comps[i]['role']}")
        print(f"\nactive {menuinfo['active_min']}min, "
              f"perishable waste {menuinfo['waste_perishable']}g, "
              f"cuisines {menuinfo['cuisines']}")
        return

    if dishes_map:
        weeks, demand, sp, diag, mealdays = _assemble_dishes(
            comps, people, settings, dishes_map, menu, a.seed, ing,
            leftovers, timer)
    else:
        weeks, demand, sp, diag, mealdays = _assemble(comps, people,
                                                      settings, menu,
                                                      a.seed, ing,
                                                      leftovers, timer)
    batches = sp["batches"]
    chosen = [i for i in comp_menu if batches.get(i)]
    rows, wp, wt = purchase(comps, ing, chosen, batches, pantry=pantry)
    total = menu_cost(comps, ing, chosen, batches, pantry=pantry)

    if a.cmd == "lock":
        _cmd_lock(a, timer, lib=lib, settings=settings, plan_date=plan_date,
                  comps=comps, ing=ing, people=people, menu=menu,
                  menuinfo=menuinfo, feas=feas, broke=broke, weeks=weeks,
                  sp=sp, diag=diag, pantry=pantry, stock_warns=stock_warns,
                  rows=rows, total=total, mealdays=mealdays,
                  fragments=fragments, techniques=techniques,
                  dishes_map=dishes_map, comp_menu=comp_menu)
        return

    # M1.1: the three human-readable deliverables (PRD §4.3 step 4).
    # Rendering only — every input below is an already-solved structure.
    if a.artifacts and a.cmd in ("week", "all"):
        with timer.span("artifacts"):
            meta = dict(seed=a.seed, library=str(lib),
                        date=a.date or "unspecified")
            files = artifacts.render_artifacts(
                comps, ing, people, settings, comp_menu, weeks, sp, rows,
                total, pantry=pantry, stock_warnings=stock_warns, diag=diag,
                meta=meta, meals=mealdays, methods=fragments,
                techniques=techniques)
            written = artifacts.write_artifacts(a.artifacts, files)
        print(f"[artifacts written to {a.artifacts}: "
              + ", ".join(p.name for p in written) + "]", file=sys.stderr)

    if a.json:
        result = {
            "library": str(lib), "seed": a.seed, "date": a.date,
            "menu": menu, "menu_info": menuinfo,
            "feasible": feas, "misses": broke,
            "purchase_rows": rows, "waste_perishable_g": wp,
            "total_cost": total, "stock_warnings": stock_warns,
        }
        if dishes_map:
            # M1.13 (additive): dish mode names itself; menu is dish ids
            # and the derived component closure rides along. Heritage
            # documents are byte-identical (keys absent).
            result["dish_mode"] = True
            result["closure"] = comp_menu
        if a.cmd in ("week", "all"):
            result.update(
                weeks=weeks, demand=demand, session_plan=sp,
                relax_tiers=(diag or {}).get("relax_tiers"))
            if dishes_map:
                result["dish_diag"] = {
                    "dish_retries": (diag or {}).get("dish_retries"),
                    "dish_flag_counts":
                        (diag or {}).get("dish_flag_counts"),
                    "no_dish_assignable":
                        (diag or {}).get("no_dish_assignable", [])}
            # M1.9: meal structure rides in the result ONLY when somebody
            # configures meals — the no-meals document is byte-identical
            if mealdays:
                result["meals"] = mealdays
            if a.diagnose:
                result["doctor"] = {"report": docmsg, "data": docdata}
        _emit_json(a.cmd, feas is not False, result=result)
        if feas is False:
            raise SystemExit(EXIT_INFEASIBLE)
        return

    with timer.span("render"):
        out = render(comps, ing, people, settings, comp_menu, weeks, demand,
                     docmsg, menuinfo, sp, pantry=pantry, diag=diag,
                     meals=mealdays, methods=fragments,
                     techniques=techniques, dish_menu=menu if dishes_map
                     else None, dishes=dishes_map)

    if a.cmd == "shop":
        print(out.split("## Shopping list")[1])
        return
    outpath = Path(a.out or "plan.md")
    outpath.write_text(out)
    print(out)
    print(f"\n\n[written to {outpath}]", file=sys.stderr)


# --------------------------------------------------------------------------- #
#  lock / verify-plan (M1.3, PRD §4.3 / §8.1 / §8.2)
# --------------------------------------------------------------------------- #
def _cmd_lock(a, timer, *, lib, settings, plan_date, comps, ing, people,
              menu, menuinfo, feas, broke, weeks, sp, diag, pantry,
              stock_warns, rows, total, mealdays=None, fragments=None,
              techniques=None, dishes_map=None, comp_menu=None):
    """Write the immutable locked plan artifact: plans/<key>/plan.yaml plus
    the three M1.1 deliverables rendered alongside. M1.13: in dish mode
    the dishes.yaml document joins the verbatim inputs snapshot (so the
    inputs hash covers it automatically) and ``menu`` locks as dish ids;
    a heritage lock document is byte-identical to pre-M1.13."""
    key = lockplan.primary_trip_date(plan_date, settings)
    raw_docs = io_yaml.load_raw_docs(lib)
    if dishes_map and not a.implicit_dishes:
        dishes_path = Path(a.dishes) if a.dishes else lib / "dishes.yaml"
        raw_docs["dishes"] = yaml.safe_load(dishes_path.read_text())
    elif dishes_map:
        # --implicit-dishes: the authored doc (when present) snapshots
        # verbatim; the synthesized dishes re-derive at verify time from
        # the override flag (never snapshotted — they are derived state)
        dishes_path = Path(a.dishes) if a.dishes else lib / "dishes.yaml"
        if dishes_path.exists():
            raw_docs["dishes"] = yaml.safe_load(dishes_path.read_text())
    pantry_doc = None
    if a.pantry:
        pantry_doc = yaml.safe_load(Path(a.pantry).read_text())
    overrides = {"budget": a.budget, "mass": a.mass,
                 "exclude": a.exclude or "", "force": a.force or "",
                 "n": a.n, "menu": a.menu}
    if a.implicit_dishes:
        # present ONLY when set: heritage snapshots stay byte-identical
        overrides["implicit_dishes"] = True
    snapshot = lockplan.build_snapshot(raw_docs, pantry_doc, overrides,
                                       a.seed, plan_date)
    doc = lockplan.build_plan_doc(
        snapshot, key, plan_date, menu, weeks, sp, feas, broke,
        (diag or {}).get("relax_tiers"), stock_warns, meals=mealdays)
    plan_dir = Path(a.plans) / key.isoformat()
    with timer.span("lock"):
        try:
            path, superseded = lockplan.write_plan(plan_dir, doc,
                                                   supersede=a.supersede)
        except lockplan.LockExists as e:
            raise CliError("plan_exists", str(e), EXIT_ERROR,
                           details={"path": str(e.path)})
        # the three deliverables land NEXT TO the plan — the plan dir is the
        # complete record of the locked week
        meta = dict(seed=a.seed, library=str(lib), date=plan_date.isoformat())
        files = artifacts.render_artifacts(
            comps, ing, people, settings, comp_menu or menu, weeks, sp,
            rows, total, pantry=pantry, stock_warnings=stock_warns,
            diag=diag, meta=meta, meals=mealdays, methods=fragments,
            techniques=techniques)
        written = artifacts.write_artifacts(plan_dir, files)
    result = {
        "key": key.isoformat(), "plan_path": str(path),
        "plan_dir": str(plan_dir), "inputs_sha256": doc["inputs_sha256"],
        "supersedes": superseded, "menu": menu, "feasible": feas,
        "misses": broke, "files": [p.name for p in written],
    }
    if a.json:
        _emit_json("lock", feas is not False, result=result)
    else:
        print(f"locked plan {key.isoformat()} -> {path}")
        print(f"inputs sha256: {doc['inputs_sha256']}")
        if superseded:
            print("superseded: " + ", ".join(superseded))
        print("deliverables: " + ", ".join(p.name for p in written))
        print(f"verify anytime: mealplan verify-plan {path}")
    if feas is False:
        raise SystemExit(EXIT_INFEASIBLE)


def _cmd_verify_plan(a, timer):
    """Re-solve a locked plan from its embedded snapshot and check the
    inputs hash + menu + per-person portions + session plan (M1.3
    reproducibility). A fresh lock verifies clean on the reference
    environment; a tampered file or altered snapshot fails loudly
    (exit 3) — including a hand-edited session_plan (batch counts and
    cook minutes the household acts on), which is covered by neither
    the inputs hash nor the portions check."""
    if not a.plan:
        raise CliError("missing_argument",
                       "verify-plan requires the path to a locked plan: "
                       "mealplan verify-plan plans/<key>/plan.yaml",
                       EXIT_USAGE)
    p = Path(a.plan)
    if not p.exists():
        raise CliError("missing_file", f"{p}: plan file not found",
                       EXIT_ERROR)
    try:
        doc = lockplan.load_plan(p)
    except (yaml.YAMLError, ValueError) as e:
        raise CliError("bad_plan", str(e), EXIT_ERROR)
    report = {"path": str(p), "key": doc.get("key"),
              "inputs_sha256": doc["inputs_sha256"],
              "hash_ok": None, "menu_ok": None, "portions_ok": None,
              "session_plan_ok": None, "verified": False}
    # M1.9: a plan that carries meal structure is re-dealt and compared too;
    # a meal-less plan's report keeps its historical shape byte-for-byte
    has_meals = doc.get("meals") is not None
    if has_meals:
        report["meals_ok"] = None
    report["hash_ok"] = lockplan.check_hash(doc)
    if not report["hash_ok"]:
        raise CliError(
            "verify_failed",
            f"{p}: inputs hash mismatch — the embedded snapshot does not "
            "hash to the recorded inputs_sha256; the snapshot or the hash "
            "was altered after lock", EXIT_ERROR, details=report)
    with timer.span("verify-plan"):
        solved = _solve_from_snapshot(doc["inputs"], timer)
    report["menu_ok"] = (canonical_json(solved["menu"])
                         == canonical_json(doc["menu"]))
    report["portions_ok"] = (canonical_json(solved["weeks"])
                             == canonical_json(doc["portions"]))
    report["session_plan_ok"] = (canonical_json(solved["sp"])
                                 == canonical_json(doc["session_plan"]))
    checks = ["menu_ok", "portions_ok", "session_plan_ok"]
    if has_meals:
        report["meals_ok"] = (canonical_json(solved["meals"])
                              == canonical_json(doc["meals"]))
        checks.append("meals_ok")
    report["verified"] = all(report[k] for k in checks)
    if not report["verified"]:
        bad = [k for k in checks if not report[k]]
        raise CliError(
            "verify_failed",
            f"{p}: re-solve from the embedded snapshot diverges from the "
            f"stored plan ({', '.join(bad)}) — the plan body was altered "
            "after lock, or this is not the reference environment "
            "(PRD §9 golden policy)", EXIT_ERROR, details=report)
    if a.json:
        _emit_json("verify-plan", True, result=report)
    else:
        print(f"verified: {p}")
        print(f"inputs sha256 ok; menu, per-person portions and session "
              f"plan reproduce from the embedded snapshot "
              f"(seed {doc['inputs'].get('seed')})")


if __name__ == "__main__":
    main()
