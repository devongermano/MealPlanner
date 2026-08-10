#!/usr/bin/env python3
"""Lint the draft dish layer against examples/components.yaml.

STANDALONE by design, same posture as tools/fdc/lint_ingredients.py: this parses
the YAML directly and never imports the mealplan package. It must keep working
while the engine is being rebuilt, and it must never be able to corrupt engine
state — it only reads.

Checks (ERROR fails the run, WARN does not). Rules marked M1.13 come from
services/solver/M113_SPEC.md §2, so this lint and the engine's future validator
agree about what a well-formed dish is:

  ERROR  unknown component id anywhere in a dish (member, accent or side)
  ERROR  a dish with no components at all
  ERROR  `accents` overlapping `components`         (M1.13 §2: disjoint)
  ERROR  band not sane: needs 0 < min_g <= base_g <= max_g, whole grams
         (M1.13 §2: a core member is a `components` entry with min_g > 0)
  ERROR  min_g above the component's serve_g.max — one serving could not exist
         inside the authored absolutes (M1.13 §2 dish_band_exceeds_serve;
         PRD Appendix B item 2, the lard-beans ruling)
  ERROR  a discrete (unit_g) member whose [min_g, max_g] contains no multiple of
         unit_g — unservable at one serving (M1.13 §2)
  ERROR  an accent id whose component role is not `accent`
  ERROR  an accent with no entry in accent_baselines.yaml
  ERROR  a `main` component that belongs to no dish (orphan main)
  ERROR  invalid meal_affinity label, empty affinity, or `any` mixed with slots
  ERROR  invalid `reconstruction` value, duplicate dish id, missing name/source
  ERROR  a compatible_side that is also a component of the same dish

  WARN   max_g above serve_g.max — legal, but the per-meal cap truncates it
  WARN   min_g below serve_g.min, or base_g off the unit grid
  WARN   dish_band_loose (M1.13 §2): the bands permit a degenerate ratio —
         worst-case pairwise (max_i/min_j)/(base_i/base_j) over 3.0. Every dish
         currently trips this; see the band-width note in dishes.yaml for why
         and what the decision is
  WARN   affinity_slot_mismatch (M1.13 §2): a person whose configured slot names
         match none of the affinity labels in use — affinity is inert for them
  WARN   an accent component that no dish attaches
  WARN   a non-main component (starch/veg) in no dish at all

Accents carry no per-dish band in dishes.yaml (M1.13 §5 gives them
{0} u [serve_min, serve_max] of the component itself). The per-serving amounts
the reconstruction authored live in accent_baselines.yaml, which is what
DISH_REVIEW.md shows the owner and what the macro table below includes.

Also prints, per dish, the derived per-serving macros at baseline. Macros are
DERIVED from ingredients (PRD P3) exactly as the engine derives them —
ingredient grams x edible_fraction x per-100g, over the batch yield — so the
numbers here are checkable against the same authority, never hand-entered.

Usage:
    python3 data/dishes-draft/lint.py                     # from the repo root
    python3 data/dishes-draft/lint.py --quiet             # findings only

Reads examples/people.yaml (read-only) to resolve the household's real meal-slot
names, so affinity labels are checked against slots that actually exist.

Exit code: 1 if any ERROR, else 0.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ATWATER = {"p": 4.0, "f": 9.0, "c": 4.0}
# Reserved slot-name trio (M1.13 §2). Household slot names are added at runtime
# from examples/people.yaml — an affinity label matching no person's slot name is
# inert for that person, so the lint says so before the engine does.
AFFINITIES = {"breakfast", "lunch", "dinner", "any"}
BAND_LOOSE_RATIO = 3.0   # M1.13 §2 DISH_BAND_LOOSE_RATIO (provisional, P9)
RECONSTRUCTIONS = {"from_source", "inferred", "invented"}

REPO = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------- loading ---

def load(path: Path):
    with path.open() as fh:
        return yaml.safe_load(fh)


def household_slots_by_person(path: Path) -> dict:
    """Per-person resolved slot names — see household_slot_names."""
    out = {}
    for name, person in (load(path).get("people") or {}).items():
        slots = person.get("meal_slots")
        if slots:
            out[name] = [s["name"] for s in slots
                         if isinstance(s, dict) and "name" in s]
        elif person.get("meals_per_day"):
            out[name] = [f"meal_{i}"
                         for i in range(1, int(person["meals_per_day"]) + 1)]
        else:
            out[name] = []
    return out


def household_slot_names(path: Path) -> set:
    """Slot names any person has configured. Mirrors the engine's resolution
    (model.resolve_meal_slots) without importing it: explicit `meal_slots` names,
    else `meals_per_day` generates meal_1..meal_n — breakfast/lunch/dinner are
    never guessed from a bare count (M19 §2)."""
    names = set()
    for person in (load(path).get("people") or {}).values():
        slots = person.get("meal_slots")
        if slots:
            names |= {s["name"] for s in slots if isinstance(s, dict) and "name" in s}
        elif person.get("meals_per_day"):
            names |= {f"meal_{i}" for i in range(1, int(person["meals_per_day"]) + 1)}
    return names


def component_map(raw) -> dict:
    return {c["id"]: c for c in raw["components"]}


# -------------------------------------------------------------- derivation ---

def component_per100(comp: dict, ings: dict) -> dict:
    """Per-100g macros of a finished component, derived from its ingredients."""
    tot = {"p": 0.0, "f": 0.0, "c": 0.0}
    for ing_id, grams in comp["ingredients"].items():
        ing = ings[ing_id]
        edible = float(ing.get("edible_fraction", 1.0))
        for k in tot:
            tot[k] += float(grams) * edible * float(ing[k]) / 100.0
    yield_g = float(comp["yield_g"])
    return {k: 100.0 * v / yield_g for k, v in tot.items()}


def serving_macros(dish: dict, per100: dict, baselines: dict, *,
                   skip_accents=False) -> dict:
    """Macros of one baseline serving: core members at base_g, plus accents at the
    amounts recorded in accent_baselines.yaml (which is what DISH_REVIEW.md shows
    the owner — accents carry no band in dishes.yaml itself, M1.13 §2/§5)."""
    out = {"p": 0.0, "f": 0.0, "c": 0.0}
    grams = {cid: b["base_g"] for cid, b in dish["components"].items()}
    if not skip_accents:
        for cid, b in baselines.get(dish["id"], {}).items():
            grams[cid] = b["base_g"]
    for cid, g in grams.items():
        m = per100[cid]
        for k in out:
            out[k] += float(g) * m[k] / 100.0
    out["kcal"] = sum(ATWATER[k] * out[k] for k in ("p", "f", "c"))
    return out


# ------------------------------------------------------------------ checks ---

def check_dishes(dishes: list, comps: dict, baselines: dict,
                 slot_names: set, by_person: dict) -> list[dict]:
    findings: list[dict] = []

    def err(dish_id, check, detail):
        findings.append({"level": "ERROR", "dish": dish_id, "check": check, "detail": detail})

    def warn(dish_id, check, detail):
        findings.append({"level": "WARN", "dish": dish_id, "check": check, "detail": detail})

    seen_ids: set[str] = set()
    used_as_component: set[str] = set()
    used_as_accent: set[str] = set()
    used_as_side: set[str] = set()

    for dish in dishes:
        did = dish.get("id", "<missing id>")
        if did in seen_ids:
            err(did, "duplicate_id", "dish id appears more than once")
        seen_ids.add(did)

        for field in ("name", "source", "reconstruction"):
            if not dish.get(field):
                err(did, "missing_field", f"`{field}` is required")

        recon = dish.get("reconstruction")
        if recon is not None and recon not in RECONSTRUCTIONS:
            err(did, "bad_reconstruction",
                f"`{recon}` not one of {sorted(RECONSTRUCTIONS)}")

        affinity = dish.get("meal_affinity") or []
        if not affinity:
            err(did, "empty_affinity", "meal_affinity must name at least one slot")
        allowed = AFFINITIES | slot_names
        bad = [a for a in affinity if a not in allowed]
        if bad:
            err(did, "bad_affinity", f"{bad} not in {sorted(allowed)}")
        inert = [a for a in affinity if a not in slot_names and a != "any"]
        if inert:
            warn(did, "affinity_slot_mismatch",
                 f"{inert} matches no meal slot any person has configured — "
                 "affinity is inert for everyone until a slot is named that")
        if "any" in affinity and len(affinity) > 1:
            err(did, "bad_affinity", "`any` may not be mixed with specific slots")

        bands = dish.get("components") or {}
        accents = list(dish.get("accents") or [])
        sides = list(dish.get("compatible_sides") or [])

        if not bands:
            err(did, "no_components", "dish has no components")
        overlap = sorted(set(accents) & set(bands))
        if overlap:
            err(did, "accent_not_disjoint",
                f"{overlap} appear in both `accents` and `components`; M1.13 §2 "
                "requires them disjoint (accents take their range from the "
                "component's own serve_g)")

        for cid, band in bands.items():
            comp = comps.get(cid)
            if comp is None:
                err(did, "unknown_component", f"`{cid}` is not in examples/components.yaml")
                continue
            used_as_component.add(cid)

            vals = {}
            ok = True
            for key in ("base_g", "min_g", "max_g"):
                v = band.get(key)
                if not isinstance(v, int):
                    err(did, "band_type", f"{cid}.{key} = {v!r} must be a whole number of grams")
                    ok = False
                else:
                    vals[key] = v
            if not ok:
                continue

            if not (0 < vals["min_g"] <= vals["base_g"] <= vals["max_g"]):
                err(did, "band_order",
                    f"{cid}: needs 0 < min {vals['min_g']} <= base {vals['base_g']} "
                    f"<= max {vals['max_g']} (M1.13 §2: a core member has min_g > 0)")

            serve = comp["serve_g"]
            # M1.13 §2 dish_band_exceeds_serve: min above the absolute is an ERROR
            # (one serving cannot exist inside it); max above it only truncates.
            if vals["min_g"] > serve["max"]:
                err(did, "dish_band_exceeds_serve",
                    f"{cid}: dish min {vals['min_g']} g > component serve_g.max "
                    f"{serve['max']} g — one serving cannot exist")
            elif vals["max_g"] > serve["max"]:
                warn(did, "dish_band_truncated",
                     f"{cid}: dish max {vals['max_g']} g > component serve_g.max "
                     f"{serve['max']} g; the per-meal cap will truncate the band")
            if vals["min_g"] < serve["min"]:
                warn(did, "below_serve_min",
                     f"{cid}: dish min {vals['min_g']} g < component serve_g.min "
                     f"{serve['min']} g")

            unit = comp.get("unit_g")
            if unit:
                # M1.13 §2: the band must admit at least one grid point at t = 1.
                if not any(g % unit == 0 for g in range(vals["min_g"], vals["max_g"] + 1)):
                    err(did, "unit_grid",
                        f"{cid}: [{vals['min_g']}, {vals['max_g']}] contains no multiple "
                        f"of unit_g {unit} — unservable at one serving")
                elif vals["base_g"] % unit:
                    warn(did, "base_off_grid",
                         f"{cid}: baseline {vals['base_g']} g is not a multiple of "
                         f"unit_g {unit}")

        # M1.13 §2 dish_band_loose: bands that permit a degenerate ratio.
        loose, pair = 0.0, None
        for i, bi in bands.items():
            for j, bj in bands.items():
                if i == j or not bj.get("min_g"):
                    continue
                r = (bi["max_g"] / bj["min_g"]) / (bi["base_g"] / bj["base_g"])
                if r > loose:
                    loose, pair = r, (i, j)
        if pair and loose > BAND_LOOSE_RATIO:
            warn(did, "dish_band_loose",
                 f"worst-case ratio error {loose:.2f} > {BAND_LOOSE_RATIO} "
                 f"(max {pair[0]} against min {pair[1]}) — see the band-width note "
                 "in dishes.yaml")

        for cid in accents:
            used_as_accent.add(cid)
            comp = comps.get(cid)
            if comp is None:
                err(did, "unknown_accent", f"`{cid}` is not in examples/components.yaml")
            elif comp.get("role") != "accent":
                err(did, "accent_role",
                    f"{cid}: listed as an accent but its role is `{comp.get('role')}`")
            if cid not in baselines.get(did, {}):
                err(did, "accent_baseline_missing",
                    f"`{cid}` has no entry in accent_baselines.yaml")

        for cid in sides:
            if cid not in comps:
                err(did, "unknown_side", f"`{cid}` is not in examples/components.yaml")
                continue
            used_as_side.add(cid)
            if cid in bands:
                err(did, "side_is_component",
                    f"`{cid}` is both a component of this dish and a side of it")

    # ---- per-person affinity reachability (M1.13 §2) -------------------------
    labels = {a for d in dishes for a in (d.get("meal_affinity") or [])} - {"any"}
    for person, slots in sorted(by_person.items()):
        inert = sorted(labels - set(slots))
        if inert and labels:
            findings.append({
                "level": "WARN", "dish": "-", "check": "affinity_slot_mismatch",
                "detail": f"{person}: slots {slots or '[]'} match none of {inert} — "
                          "every dish affinity is inert for this person until the "
                          "slots are named (examples/people.yaml)"})

    # ---- corpus-wide coverage -------------------------------------------------
    for cid, comp in sorted(comps.items()):
        role = comp.get("role")
        if role == "main" and cid not in used_as_component:
            findings.append({"level": "ERROR", "dish": "-", "check": "orphan_main",
                             "detail": f"`{cid}` is a main and belongs to no dish"})
        elif role == "accent" and cid not in used_as_accent:
            findings.append({"level": "WARN", "dish": "-", "check": "orphan_accent",
                             "detail": f"`{cid}` is an accent that no dish attaches"})
        elif role in ("starch", "veg") and cid not in used_as_component | used_as_side:
            findings.append({"level": "WARN", "dish": "-", "check": "orphan_component",
                             "detail": f"`{cid}` ({role}) appears in no dish, as part or side"})
    return findings


# -------------------------------------------------------------------- main ---

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dishes", default=str(REPO / "data/dishes-draft/dishes.yaml"))
    ap.add_argument("--components", default=str(REPO / "examples/components.yaml"))
    ap.add_argument("--ingredients", default=str(REPO / "examples/ingredients.yaml"))
    ap.add_argument("--baselines",
                    default=str(REPO / "data/dishes-draft/accent_baselines.yaml"))
    ap.add_argument("--people", default=str(REPO / "examples/people.yaml"))
    ap.add_argument("--quiet", action="store_true", help="findings only, no macro table")
    args = ap.parse_args()

    raw_dishes = load(Path(args.dishes))
    baselines = load(Path(args.baselines))["accent_baselines"]
    slot_names = household_slot_names(Path(args.people))
    by_person = household_slots_by_person(Path(args.people))
    comps = component_map(load(Path(args.components)))
    ings = load(Path(args.ingredients))["ingredients"]
    dishes = raw_dishes["dishes"]

    per100 = {cid: component_per100(c, ings) for cid, c in comps.items()}
    findings = check_dishes(dishes, comps, baselines, slot_names, by_person)

    if not args.quiet:
        print(f"{len(dishes)} dishes over {len(comps)} components "
              f"({sum(1 for c in comps.values() if c['role'] == 'main')} mains)\n")
        print("PER-SERVING MACROS AT BASELINE (derived from ingredients — P3)")
        print(f"  {'dish':32} {'recon':11} {'p':>6} {'f':>6} {'c':>6} {'kcal':>6}   "
              f"{'kcal no-accents':>15}")
        for d in dishes:
            full = serving_macros(d, per100, baselines)
            bare = serving_macros(d, per100, baselines, skip_accents=True)
            print(f"  {d['id']:32} {d.get('reconstruction',''):11} "
                  f"{full['p']:6.1f} {full['f']:6.1f} {full['c']:6.1f} {full['kcal']:6.0f}   "
                  f"{bare['kcal']:15.0f}")
        print()

    errors = [f for f in findings if f["level"] == "ERROR"]
    warns = [f for f in findings if f["level"] == "WARN"]
    for f in findings:
        print(f"{f['level']:5} {f['dish']:32} {f['check']:22} {f['detail']}")
    print(f"\n{len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
