#!/usr/bin/env python3
"""Transform cached USDA FDC CSVs into the committed data/fdc/extract.json.

The extract is the vendored raw-ingredient macro authority: plausibly-raw,
single-ingredient foods only, per-100g protein/fat/carb grams. Everything
prepared/branded/restaurant/baby is filtered out via a curated category
allowlist plus a cooked/processed-state description blocklist.

Output is deterministic: entries sorted by fdc_id, one compact JSON object per
line (diff-reviewable on snapshot upgrades), stable key order.

Usage:
    python tools/fdc/transform.py [--cache-dir /tmp/fdc-cache] [--out data/fdc/extract.json]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

# FDC nutrient ids (nutrient_nbr 203/204/205), grams per 100 g.
NUTRIENT_IDS = {"1003": "p", "1004": "f", "1005": "c"}

# FDC category description -> coarse mealplan category. Anything not listed is
# dropped (Baked Products, Baby Foods, Fast Foods, Restaurant Foods, Snacks,
# Sweets, Beverages, Breakfast Cereals, Soups/Sauces/Gravies, Meals/Entrees,
# American Indian/Alaska Native Foods — prepared or multi-ingredient).
CATEGORY_ALLOWLIST = {
    "Beef Products": "meat",
    "Pork Products": "meat",
    "Lamb, Veal, and Game Products": "meat",
    "Poultry Products": "poultry",
    "Finfish and Shellfish Products": "seafood",
    "Vegetables and Vegetable Products": "produce",
    "Fruits and Fruit Juices": "produce",
    "Cereal Grains and Pasta": "grain",
    "Dairy and Egg Products": "dairy_egg",
    "Fats and Oils": "fat_oil",
    "Legumes and Legume Products": "legume",
    "Nut and Seed Products": "nut_seed",
    "Spices and Herbs": "spice",
}

# Sausages are mostly prepared lunch meats; keep only explicitly raw/fresh ones
# (our own corpus buys raw chorizo / Italian sausage as ingredients).
CONDITIONAL_CATEGORIES = {"Sausages and Luncheon Meats": "meat"}
CONDITIONAL_REQUIRE_RE = re.compile(r"\b(raw|fresh)\b", re.IGNORECASE)

# Cooked / processed states that disqualify an entry as a raw purchasable
# ingredient. Word-boundary match, case-insensitive. "canned" and "dried" stay
# allowed (canned beans/tomatoes and dry grains are as-purchased ingredients).
COOKED_BLOCKLIST_RE = re.compile(
    r"\b("
    r"cooked|braised|broiled|grilled|roasted|fried|baked|boiled|stewed|"
    r"microwaved|steamed|poached|sauteed|sautéed|scrambled|heated|"
    r"rotisserie|breaded|battered|restructured|"
    r"juice|nectar|sauce|syrup|candied|sweetened|"
    r"babyfood|formula|toddler"
    r")\b",
    re.IGNORECASE,
)

# Categories where an absent carbohydrate value plausibly means zero
# (Foundation Foods often omits carb-by-difference for meats/fish/oils).
ZERO_CARB_OK = {"meat", "poultry", "seafood", "fat_oil"}


def find_csv_dir(dataset_dir: Path) -> Path:
    """The zip extracts into a single dated subdirectory; find it."""
    if (dataset_dir / "food.csv").exists():
        return dataset_dir
    subdirs = [d for d in dataset_dir.iterdir() if (d / "food.csv").exists()]
    if len(subdirs) != 1:
        raise FileNotFoundError(f"no unique food.csv under {dataset_dir}")
    return subdirs[0]


def load_dataset(csv_dir: Path, data_type: str) -> tuple[list[dict], dict]:
    """Load one FDC dataset dir -> (entries, stats). Pure file->data, no output."""
    categories = {}
    with open(csv_dir / "food_category.csv", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            categories[row["id"]] = row["description"]

    foods = {}  # fdc_id -> partial entry
    stats = {
        "total": 0,
        "wrong_data_type": 0,
        "category_dropped": 0,
        "blocklist_dropped": 0,
        "missing_macros": 0,
        "clamped_negative": 0,
        "implausible": 0,
        "kept": 0,
    }
    with open(csv_dir / "food.csv", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            stats["total"] += 1
            if row["data_type"] != data_type:
                stats["wrong_data_type"] += 1
                continue
            fdc_cat = categories.get(row["food_category_id"], "")
            desc = row["description"].strip()
            if fdc_cat in CONDITIONAL_CATEGORIES:
                if not CONDITIONAL_REQUIRE_RE.search(desc):
                    stats["category_dropped"] += 1
                    continue
                coarse = CONDITIONAL_CATEGORIES[fdc_cat]
            elif fdc_cat in CATEGORY_ALLOWLIST:
                coarse = CATEGORY_ALLOWLIST[fdc_cat]
            else:
                stats["category_dropped"] += 1
                continue
            if COOKED_BLOCKLIST_RE.search(desc):
                stats["blocklist_dropped"] += 1
                continue
            foods[row["fdc_id"]] = {
                "fdc_id": int(row["fdc_id"]),
                "description": desc,
                "data_type": data_type,
                "category": coarse,
                "fdc_category": fdc_cat,
            }

    macros: dict[str, dict[str, float]] = {}
    with open(csv_dir / "food_nutrient.csv", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            key = NUTRIENT_IDS.get(row["nutrient_id"])
            if key is None or row["fdc_id"] not in foods:
                continue
            if row["amount"] == "":
                continue
            # First row wins if a nutrient appears twice (stable across runs:
            # csv order is fixed in the source file).
            macros.setdefault(row["fdc_id"], {}).setdefault(key, float(row["amount"]))

    entries = []
    for fid, entry in foods.items():
        m = macros.get(fid, {})
        if "c" not in m and entry["category"] in ZERO_CARB_OK:
            m = {**m, "c": 0.0}
        if not all(k in m for k in ("p", "f", "c")):
            stats["missing_macros"] += 1
            continue
        p, fat, c = m["p"], m["f"], m["c"]
        # Foundation Foods reports carbohydrate *by difference*, which comes out
        # slightly negative for raw meats/fish (lab artifact). Clamp small
        # negatives to zero; anything worse is a real data problem.
        clamped = []
        for name, v in (("p", p), ("f", fat), ("c", c)):
            if -1.0 <= v < 0.0:
                clamped.append(name)
        if clamped:
            stats["clamped_negative"] += 1
            p, fat, c = (max(v, 0.0) for v in (p, fat, c))
        # Plausibility gate on the source itself: macro grams must fit in 100 g
        # (tiny rounding slack) and be non-negative.
        if p < 0 or fat < 0 or c < 0 or p + fat + c > 101.0:
            stats["implausible"] += 1
            continue
        entry["p"] = round(p, 2)
        entry["f"] = round(fat, 2)
        entry["c"] = round(c, 2)
        entries.append(entry)
        stats["kept"] += 1
    return entries, stats


ENTRY_KEYS = ["fdc_id", "description", "data_type", "category", "fdc_category", "p", "f", "c"]


def render_extract(entries: list[dict], snapshot: str) -> str:
    """Deterministic JSON text: sorted entries, one compact object per line."""
    entries = sorted(entries, key=lambda e: e["fdc_id"])
    lines = ['{', f'"snapshot": {json.dumps(snapshot)},', '"entries": [']
    for i, e in enumerate(entries):
        obj = json.dumps({k: e[k] for k in ENTRY_KEYS}, ensure_ascii=False, separators=(", ", ": "))
        lines.append(obj + ("," if i < len(entries) - 1 else ""))
    lines.append("]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def read_snapshot(manifest_path: Path) -> str:
    for line in manifest_path.read_text().splitlines():
        if line.startswith("snapshot:"):
            return line.split(":", 1)[1].strip()
    raise ValueError(f"no snapshot line in {manifest_path}")


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", type=Path, default=Path("/tmp/fdc-cache"))
    ap.add_argument("--out", type=Path, default=repo / "data" / "fdc" / "extract.json")
    ap.add_argument("--manifest", type=Path, default=repo / "data" / "fdc" / "MANIFEST.yaml")
    args = ap.parse_args(argv)

    snapshot = read_snapshot(args.manifest)
    all_entries: list[dict] = []
    for name, data_type in (("sr_legacy", "sr_legacy_food"), ("foundation", "foundation_food")):
        csv_dir = find_csv_dir(args.cache_dir / name)
        entries, stats = load_dataset(csv_dir, data_type)
        print(f"{name}: {stats}")
        all_entries.extend(entries)

    text = render_extract(all_entries, snapshot)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    from collections import Counter

    cats = Counter(e["category"] for e in all_entries)
    print(f"wrote {args.out}: {len(all_entries)} entries, {len(text.encode()) / 1e6:.2f} MB")
    print("by category:", dict(sorted(cats.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
