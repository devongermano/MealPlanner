#!/usr/bin/env python3
"""Lint a mealplan-format ingredients.yaml against physics and the FDC extract.

STANDALONE by design: parses the YAML directly and never imports the mealplan
package — this tool must keep working while the engine is being rebuilt, and
it must never be able to corrupt engine state (it only reads).

Checks per ingredient (macros are per-100g of the raw / as-purchased item):
  ERROR  p + f + c > 100 g per 100 g          (physically impossible)
  ERROR  any macro negative
  ERROR  implied Atwater density 4p+9f+4c outside [0, 900] kcal/100g
  WARN   best FDC candidate deviates > 15% on any macro (with a 1.5 g/100g
         absolute floor so near-zero macros don't spam relative noise)
  INFO   no plausible FDC candidate found (name didn't match — not an error;
         corpus keys are shorthand, e.g. "salsa_verde")

Usage:
    python tools/fdc/lint_ingredients.py path/to/ingredients.yaml \
        [--extract data/fdc/extract.json] [--max-dev 0.15]

Exit code: 1 if any ERROR, else 0 (warnings don't fail).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Sibling module, same directory — keep the tool runnable as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from index import build_index, load_extract, normalize, search  # noqa: E402

ATWATER = {"p": 4.0, "f": 9.0, "c": 4.0}
ABS_FLOOR_G = 1.5  # ignore relative deviation when both values this close


def atwater_kcal(p: float, f: float, c: float) -> float:
    return ATWATER["p"] * p + ATWATER["f"] * f + ATWATER["c"] * c


def physics_findings(name: str, ing: dict) -> list[dict]:
    """Pure plausibility checks on one ingredient dict with p/f/c keys."""
    out = []
    p, f, c = (float(ing.get(k, 0) or 0) for k in ("p", "f", "c"))
    for k, v in (("p", p), ("f", f), ("c", c)):
        if v < 0:
            out.append({"level": "ERROR", "name": name, "check": "negative_macro",
                        "detail": f"{k}={v} g/100g is negative"})
    total = p + f + c
    if total > 100.0:
        out.append({"level": "ERROR", "name": name, "check": "mass_overflow",
                    "detail": f"p+f+c = {total:.1f} g per 100 g (> 100)"})
    kcal = atwater_kcal(p, f, c)
    if not (0.0 <= kcal <= 900.0):
        out.append({"level": "ERROR", "name": name, "check": "kcal_density",
                    "detail": f"Atwater density {kcal:.0f} kcal/100g outside [0, 900]"})
    return out


def query_for(name: str) -> str:
    """Turn a corpus key like 'ground_beef_85' into a search query."""
    return " ".join(normalize(name.replace("_", " ")))


def fdc_findings(name: str, ing: dict, indexed: list[dict], snapshot: str,
                 max_dev: float) -> list[dict]:
    """Best-effort FDC comparison for one ingredient."""
    candidates = search(query_for(name), indexed, limit=3, min_coverage=0.99)
    weak = False
    if not candidates:
        # Retry allowing partial matches before giving up.
        candidates = search(query_for(name), indexed, limit=3, min_coverage=0.5)
        weak = True
    if not candidates:
        return [{"level": "INFO", "name": name, "check": "no_fdc_match",
                 "detail": "no FDC candidate for this name"}]
    best = candidates[0]
    if weak:
        # Partial name match: candidate is a hint for a human, not evidence of
        # a macro problem (this is where "brown_sugar" -> "Sugar-apples" lives).
        return [{
            "level": "INFO", "name": name, "check": "weak_fdc_match",
            "detail": f"only partial name match (score {best['score']})",
            "candidate": f"usda_fdc:{best['fdc_id']}@{snapshot} {best['description']}",
        }]
    devs = []
    for k in ("p", "f", "c"):
        ours = float(ing.get(k, 0) or 0)
        theirs = float(best[k])
        if abs(ours - theirs) <= ABS_FLOOR_G:
            continue
        base = max(theirs, 1e-9)
        rel = abs(ours - theirs) / base
        if rel > max_dev:
            devs.append(f"{k}: ours {ours} vs FDC {theirs} ({rel * 100:.0f}%)")
    if devs:
        return [{
            "level": "WARN", "name": name, "check": "fdc_macro_deviation",
            "detail": "; ".join(devs),
            "candidate": f"usda_fdc:{best['fdc_id']}@{snapshot} {best['description']}",
        }]
    return [{
        "level": "OK", "name": name, "check": "fdc_match",
        "candidate": f"usda_fdc:{best['fdc_id']}@{snapshot} {best['description']}",
    }]


def lint(doc: dict, indexed: list[dict] | None, snapshot: str,
         max_dev: float = 0.15) -> list[dict]:
    """Lint a parsed ingredients.yaml document. Returns all findings."""
    ingredients = doc.get("ingredients")
    if not isinstance(ingredients, dict):
        return [{"level": "ERROR", "name": "<file>", "check": "format",
                 "detail": "no top-level 'ingredients' mapping"}]
    findings = []
    for name in sorted(ingredients):
        ing = ingredients[name]
        if not isinstance(ing, dict):
            findings.append({"level": "ERROR", "name": name, "check": "format",
                             "detail": "ingredient is not a mapping"})
            continue
        findings.extend(physics_findings(name, ing))
        if indexed is not None:
            findings.extend(fdc_findings(name, ing, indexed, snapshot, max_dev))
    return findings


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ingredients_yaml", type=Path)
    ap.add_argument("--extract", type=Path, default=repo / "data" / "fdc" / "extract.json")
    ap.add_argument("--max-dev", type=float, default=0.15)
    ap.add_argument("--no-fdc", action="store_true", help="physics checks only")
    args = ap.parse_args(argv)

    doc = yaml.safe_load(args.ingredients_yaml.read_text())
    indexed, snapshot = None, ""
    if not args.no_fdc:
        extract = load_extract(args.extract)
        indexed = build_index(extract)
        snapshot = extract["snapshot"]

    findings = lint(doc, indexed, snapshot, max_dev=args.max_dev)
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0, "OK": 0}
    for f in findings:
        counts[f["level"]] += 1
        if f["level"] == "OK":
            continue
        line = f"{f['level']:5}  {f['name']:22} {f['check']}: {f.get('detail', '')}"
        if "candidate" in f:
            line += f"\n       -> {f['candidate']}"
        print(line)
    print(f"\n{counts['ERROR']} error(s), {counts['WARN']} warning(s), "
          f"{counts['INFO']} unmatched, {counts['OK']} clean FDC matches")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
