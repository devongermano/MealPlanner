#!/usr/bin/env python3
"""Offline search over data/fdc/extract.json for ingestion-time disambiguation.

Pure functions, no network, no state beyond the loaded extract. Intended use:
when converting a recipe into the mealplan format, query the raw-ingredient
name ("chicken thigh raw") and present the ranked candidates (fdc_id + macros)
for a human — or an operator-layer LLM under the ingestion protocol — to pick
from. The chosen id becomes provenance: usda_fdc:<fdc_id>@<snapshot>.

Usage (CLI):
    python tools/fdc/index.py "chicken thigh raw" [--limit 8] [--extract data/fdc/extract.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Tokens too generic to signal a match on their own.
_STOPWORDS = {
    "and", "or", "with", "without", "the", "a", "an", "of", "in", "to",
    "all", "types", "type", "varieties", "commercial", "usda", "commodity",
}

# Data-type preference on otherwise-equal scores: Foundation Foods is the
# newer, analytically stronger dataset.
_DATA_TYPE_RANK = {"foundation_food": 0, "sr_legacy_food": 1}


def _stem(token: str) -> str:
    """Light plural folding: carrots->carrot, potatoes->potato, eggs->egg.

    Not a linguistic stemmer — it only has to fold query and description the
    same way ("molasses" becomes "molasse" on both sides, which is fine).
    """
    if len(token) > 4 and token.endswith("oes"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def normalize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, stopwords removed, plural-folded,
    order preserved."""
    return [
        _stem(t) for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS
    ]


def load_extract(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_index(extract: dict) -> list[dict]:
    """Precompute token lists per entry. Returns a list usable by search()."""
    indexed = []
    for e in extract["entries"]:
        tokens = normalize(e["description"])
        indexed.append({**e, "_tokens": tokens, "_token_set": set(tokens)})
    return indexed


def score_entry(query_tokens: list[str], entry: dict) -> tuple:
    """Score one entry against normalized query tokens.

    Higher tuple sorts better. Components:
      coverage  — fraction of query tokens present in the description (dominant)
      position  — earlier matches in the description are stronger (FDC puts the
                  food name first: "Chicken, broilers or fryers, breast, ...")
      raw       — prefer the explicitly raw/fresh entry (this is a
                  raw-ingredient authority; "Egg, whole, raw, fresh" should
                  beat "Egg, whole, dried" for the query "eggs")
      -extra    — fewer unmatched description tokens = more specific match
    """
    tokens: list[str] = entry["_tokens"]
    token_set: set[str] = entry["_token_set"]
    if not query_tokens:
        return (0.0, 0.0, 0, 0, 0)
    matched = [q for q in query_tokens if q in token_set]
    coverage = len(matched) / len(query_tokens)
    if coverage == 0.0:
        return (0.0, 0.0, 0, 0, 0)
    # Position bonus: average earliness of each matched token's first occurrence.
    pos = 0.0
    for q in matched:
        i = tokens.index(q)
        pos += 1.0 / (1 + i)
    pos /= len(query_tokens)
    extra = len(token_set) - len(set(matched))
    raw_bonus = 1 if ({"raw", "fresh"} & token_set) else 0
    return (coverage, pos, raw_bonus, -extra, -_DATA_TYPE_RANK.get(entry["data_type"], 9))


def search(query: str, indexed: list[dict], limit: int = 8, min_coverage: float = 0.5) -> list[dict]:
    """Ranked candidates for a raw-ingredient query.

    Returns entries (without index internals) with a `score` field, best first.
    Entries matching fewer than `min_coverage` of the query tokens are dropped.
    """
    q = normalize(query)
    scored = []
    for entry in indexed:
        s = score_entry(q, entry)
        if s[0] >= min_coverage and s[0] > 0:
            scored.append((s, entry))
    scored.sort(key=lambda t: (tuple(-x if isinstance(x, float) else -x for x in t[0]), t[1]["fdc_id"]))
    out = []
    for s, entry in scored[:limit]:
        clean = {k: v for k, v in entry.items() if not k.startswith("_")}
        clean["score"] = round(s[0], 3)
        out.append(clean)
    return out


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--extract", type=Path, default=repo / "data" / "fdc" / "extract.json")
    args = ap.parse_args(argv)
    extract = load_extract(args.extract)
    indexed = build_index(extract)
    results = search(args.query, indexed, limit=args.limit)
    if not results:
        print("no candidates")
        return 1
    for r in results:
        print(
            f"{r['score']:>5}  usda_fdc:{r['fdc_id']}@{extract['snapshot']}  "
            f"p={r['p']:<6} f={r['f']:<6} c={r['c']:<6} [{r['data_type']}] {r['description']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
