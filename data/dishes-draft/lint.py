#!/usr/bin/env python3
"""Lint the draft dish layer against examples/components.yaml.

STANDALONE by design, same posture as tools/fdc/lint_ingredients.py: this parses
the YAML directly and never imports the mealplan package. It must keep working
while the engine is being rebuilt, and it must never be able to corrupt engine
state — it only reads.

Checks (ERROR fails the run, WARN does not):

  ERROR  unknown component id anywhere in a dish
  ERROR  dish has no non-accent component (a dish that is only garnish)
  ERROR  band not sane: needs 0 <= min_g <= base_g <= max_g, whole grams
  ERROR  accent min_g != 0            (P4: an accent must be omittable)
  ERROR  accent id whose component role is not `accent`
  ERROR  accent id not present in the dish's `components` map
  ERROR  max_g above the component's serve_g.max, or non-accent min_g below
         serve_g.min  (PRD Appendix B item 2 — serve_g bounds are per-dish
         palatability absolutes; a dish may not authorise a portion the
         component itself calls inedible)
  ERROR  base/min/max off the unit_g grid for discrete components
  ERROR  a `main` component that belongs to no dish (orphan main)
  ERROR  invalid meal_affinity value, empty affinity, or `any` mixed with slots
  ERROR  invalid `reconstruction` value, duplicate dish id, missing name/source
  ERROR  a compatible_side that is also a component of the same dish

  WARN   an accent component that no dish attaches
  WARN   a non-main component (starch/veg) in no dish at all
  WARN   base_g below the component's serve_g.min (legal but odd)

Also prints, per dish, the derived per-serving macros at baseline. Macros are
DERIVED from ingredients (PRD P3) exactly as the engine derives them —
ingredient grams x edible_fraction x per-100g, over the batch yield — so the
numbers here are checkable against the same authority, never hand-entered.

Usage:
    python3 data/dishes-draft/lint.py                     # from the repo root
    python3 data/dishes-draft/lint.py --quiet             # findings only

Exit code: 1 if any ERROR, else 0.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ATWATER = {"p": 4.0, "f": 9.0, "c": 4.0}
AFFINITIES = {"breakfast", "lunch", "dinner", "any"}
RECONSTRUCTIONS = {"from_source", "inferred", "invented"}

REPO = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------- loading ---

def load(path: Path):
    with path.open() as fh:
        return yaml.safe_load(fh)


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


def serving_macros(dish: dict, comps: dict, per100: dict, *, skip_accents=False) -> dict:
    """Macros of one baseline serving of a dish."""
    out = {"p": 0.0, "f": 0.0, "c": 0.0}
    accents = set(dish.get("accents") or [])
    for cid, band in dish["components"].items():
        if skip_accents and cid in accents:
            continue
        m = per100[cid]
        for k in out:
            out[k] += float(band["base_g"]) * m[k] / 100.0
    out["kcal"] = sum(ATWATER[k] * out[k] for k in ("p", "f", "c"))
    return out


# ------------------------------------------------------------------ checks ---

def check_dishes(dishes: list, comps: dict) -> list[dict]:
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
        bad = [a for a in affinity if a not in AFFINITIES]
        if bad:
            err(did, "bad_affinity", f"{bad} not in {sorted(AFFINITIES)}")
        if "any" in affinity and len(affinity) > 1:
            err(did, "bad_affinity", "`any` may not be mixed with specific slots")

        bands = dish.get("components") or {}
        accents = list(dish.get("accents") or [])
        sides = list(dish.get("compatible_sides") or [])

        if not bands:
            err(did, "no_components", "dish has no components")
        if not [c for c in bands if c not in accents]:
            err(did, "accents_only", "dish has no non-accent component")

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

            if not (0 <= vals["min_g"] <= vals["base_g"] <= vals["max_g"]):
                err(did, "band_order",
                    f"{cid}: needs 0 <= min {vals['min_g']} <= base {vals['base_g']} "
                    f"<= max {vals['max_g']}")

            is_accent = cid in accents
            serve = comp["serve_g"]
            if vals["max_g"] > serve["max"]:
                err(did, "exceeds_serve_max",
                    f"{cid}: dish max {vals['max_g']} g > component serve_g.max "
                    f"{serve['max']} g (palatability absolute)")
            if is_accent:
                if vals["min_g"] != 0:
                    err(did, "accent_min_nonzero",
                        f"{cid}: accent min_g must be 0 so it can be omitted (P4), got "
                        f"{vals['min_g']}")
                if comp.get("role") != "accent":
                    err(did, "accent_role",
                        f"{cid}: listed as an accent but its role is `{comp.get('role')}`")
            else:
                if vals["min_g"] < serve["min"]:
                    err(did, "below_serve_min",
                        f"{cid}: dish min {vals['min_g']} g < component serve_g.min "
                        f"{serve['min']} g")
            if vals["base_g"] < serve["min"]:
                warn(did, "base_below_serve_min",
                     f"{cid}: baseline {vals['base_g']} g is under the component's "
                     f"minimum sensible serving ({serve['min']} g)")

            unit = comp.get("unit_g")
            if unit:
                off = [f"{k}={vals[k]}" for k in ("base_g", "min_g", "max_g") if vals[k] % unit]
                if off:
                    err(did, "unit_grid",
                        f"{cid}: {', '.join(off)} not a multiple of unit_g {unit}")

        for cid in accents:
            used_as_accent.add(cid)
            if cid not in bands:
                err(did, "accent_unbanded",
                    f"`{cid}` is listed in accents but has no band in components")

        for cid in sides:
            if cid not in comps:
                err(did, "unknown_side", f"`{cid}` is not in examples/components.yaml")
                continue
            used_as_side.add(cid)
            if cid in bands:
                err(did, "side_is_component",
                    f"`{cid}` is both a component of this dish and a side of it")

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
    ap.add_argument("--quiet", action="store_true", help="findings only, no macro table")
    args = ap.parse_args()

    raw_dishes = load(Path(args.dishes))
    comps = component_map(load(Path(args.components)))
    ings = load(Path(args.ingredients))["ingredients"]
    dishes = raw_dishes["dishes"]

    per100 = {cid: component_per100(c, ings) for cid, c in comps.items()}
    findings = check_dishes(dishes, comps)

    if not args.quiet:
        print(f"{len(dishes)} dishes over {len(comps)} components "
              f"({sum(1 for c in comps.values() if c['role'] == 'main')} mains)\n")
        print("PER-SERVING MACROS AT BASELINE (derived from ingredients — P3)")
        print(f"  {'dish':32} {'recon':11} {'p':>6} {'f':>6} {'c':>6} {'kcal':>6}   "
              f"{'kcal no-accents':>15}")
        for d in dishes:
            full = serving_macros(d, comps, per100)
            bare = serving_macros(d, comps, per100, skip_accents=True)
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
