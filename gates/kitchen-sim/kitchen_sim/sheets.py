"""Reading the sheets the way a machine can, and reconciling the trip.

Two jobs:

1. PARSE the semi-structured parts of the cook-facing artifacts. The planner
   renders more structure than it realises — shopping rows carry pack counts
   and gram needs, mise lines carry ingredient ids and batch-scaled grams —
   so a great deal can be checked without asking anyone's opinion.

2. RECONCILE, at checkout, what was bought against what the plan needs. This
   runs on the simulator's side, not the cook's. That asymmetry is
   deliberate: a gate that depends on the cook *noticing* a missing
   ingredient inherits the unfalsifiability we are trying to escape. The
   arithmetic notices whether anyone was looking or not.

The salt-and-water check deserves a note, because it is the one place this
module reasons about prose. Cook steps are natural language ("Whisk the eggs
with salt"), so there is no structured field to read. Instead we scan step
text against THE STORE'S OWN VOCABULARY — every product name and alias the
shelves know. A word that names a real purchasable food, appears in a cooking
step, and is bought by no shopping row, is an ingredient the plan forgot to
put on the list. The store supplies the noun list, so the check needs no
hand-maintained list of "things recipes might mention" and grows automatically
with the catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# `- [ ] chicken_breast — 5 × 32 oz (need 4083g, 452g leftover) — keeps 3d raw`
ROW_RE = re.compile(
    r"^- \[ \] (?P<ing>[a-z0-9_]+) — (?P<packs>\d+) × (?P<pack>[^(]+?)"
    r"\s*\(need (?P<need>[\d.]+)g, (?P<left>[\d.]+)g leftover\)"
    r"(?: — keeps (?P<keeps>\d+)d raw)?\s*$")

# a pantry-covered row buys nothing: `- ~~lard~~ — covered by pantry ...`
COVERED_RE = re.compile(r"^- ~~(?P<ing>[a-z0-9_]+)~~ — covered by pantry")

# `- [ ] beef_chuck: 2720g`  (inside a "Scaled for N batches:" block)
MISE_RE = re.compile(r"^- \[ \] (?P<ing>[a-z0-9_]+): (?P<g>[\d.]+)g\s*$")

TOTAL_RE = re.compile(r"\*\*Total estimated cost: \$(?P<total>[\d,.]+)\*\*")

# a rendered cook step: `- [ ] Sear the chuck ... — _stove · active ~14 min_`
STEP_RE = re.compile(r"^- (?:\[ \] |~~)?(?P<text>[^_]+?)\s*(?:—\s*_|$)")


@dataclass
class ShoppingRow:
    line_no: int
    raw: str
    ingredient: str
    packs: int = 0
    pack_desc: str = ""
    need_g: float = 0.0
    leftover_g: float = 0.0
    keeps_days: Optional[int] = None
    buys_nothing: bool = False       # pantry-covered


@dataclass
class CookPlanFacts:
    mise: dict                        # ingredient -> total grams across blocks
    mise_lines: dict                  # ingredient -> first line number
    step_lines: list                  # [(line_no, text)]


def parse_shopping_list(text: str) -> list[ShoppingRow]:
    rows = []
    for i, line in enumerate(text.splitlines(), 1):
        m = ROW_RE.match(line)
        if m:
            rows.append(ShoppingRow(
                line_no=i, raw=line, ingredient=m["ing"],
                packs=int(m["packs"]), pack_desc=m["pack"].strip(),
                need_g=float(m["need"]), leftover_g=float(m["left"]),
                keeps_days=int(m["keeps"]) if m["keeps"] else None))
            continue
        c = COVERED_RE.match(line)
        if c:
            rows.append(ShoppingRow(line_no=i, raw=line, ingredient=c["ing"],
                                    buys_nothing=True))
    return rows


def parse_cook_plan(text: str) -> CookPlanFacts:
    mise, mise_lines, steps = {}, {}, []
    for i, line in enumerate(text.splitlines(), 1):
        m = MISE_RE.match(line)
        if m:
            mise[m["ing"]] = mise.get(m["ing"], 0.0) + float(m["g"])
            mise_lines.setdefault(m["ing"], i)
            continue
        if line.startswith("- ") and "·" in line:
            s = STEP_RE.match(line)
            if s and s["text"].strip():
                steps.append((i, s["text"].strip()))
    return CookPlanFacts(mise, mise_lines, steps)


def stated_total(text: str) -> Optional[float]:
    m = TOTAL_RE.search(text)
    return float(m["total"].replace(",", "")) if m else None


# --------------------------------------------------------------------------- #
#  reconciliation
# --------------------------------------------------------------------------- #
def _ev(sheet: str, line_no: int, text: str) -> dict:
    return {"sheet": sheet, "line_no": line_no, "text": text}


def _stem(word: str) -> str:
    """Crude English plural folding — enough to make 'limes' and 'lime',
    'potatoes' and 'potato', 'berries' and 'berry' compare equal. A real
    stemmer would be overkill: the vocabulary here is food nouns."""
    w = word.lower()
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and w[-3] in "shxzo":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _stems(words) -> set[str]:
    return {_stem(w) for w in words if w}


def reconcile(world) -> None:
    """Called by World.checkout(). Records findings; never raises.

    Six checks, all arithmetic:
      1. every shopping row actually bought something
      2. what was bought covers the grams the row asked for
      3. the receipt resembles the total the sheet promised
      4. every ingredient the cook plan MEASURES OUT is on the list
      5. every ingredient the cook plan's PROSE names is on the list
      6. rows expressed in grams for things the world sells by bunch/count
    """
    sl_text = world.sheets.text.get("shopping_list", "")
    cp_text = world.sheets.text.get("cook_plan", "")
    rows = parse_shopping_list(sl_text)
    facts = parse_cook_plan(cp_text)

    # map cart lines to rows: explicit for_row first, then a name fallback so
    # a cook who forgot the bookkeeping is not punished for it
    by_row: dict[int, list] = {}
    unassigned = []
    for line in world.cart:
        if line.for_row:
            by_row.setdefault(line.for_row, []).append(line)
        else:
            unassigned.append(line)
    for line in unassigned:
        hay = set(re.split(r"[^a-z0-9]+", line.sku.search_text))
        best, best_score = None, 0
        for r in rows:
            toks = set(r.ingredient.split("_"))
            score = len(toks & hay)
            if score > best_score:
                best, best_score = r, score
        if best is not None and best_score:
            by_row.setdefault(best.line_no, []).append(line)

    bought_ingredients = set()
    for r in rows:
        lines = by_row.get(r.line_no, [])
        if lines:
            bought_ingredients.add(r.ingredient)

        # 1. nothing bought for this row
        if not lines and not r.buys_nothing:
            world.record(
                "product_not_found",
                f"nothing in the cart satisfies the shopping row for "
                f"'{r.ingredient}' — the trip ended with this row unbought",
                evidence=[_ev("shopping_list", r.line_no, r.raw)],
                ingredient=r.ingredient, need_g=r.need_g)
            continue
        if r.buys_nothing:
            continue

        # 2. short of the stated need
        got = sum(c.grams for c in lines)
        if got + 1e-6 < r.need_g:
            world.record(
                "quantity_deviation",
                f"the plan needs {r.need_g:g}g of '{r.ingredient}' but the "
                f"largest honest purchase was {got:g}g — short by "
                f"{r.need_g - got:g}g",
                evidence=[_ev("shopping_list", r.line_no, r.raw)],
                ingredient=r.ingredient, need_g=r.need_g, bought_g=got)

        # 6. unit the store does not sell in
        for c in lines:
            if c.sku.sold_by in ("bunch", "count"):
                world.record(
                    "unit_untranslatable",
                    f"the row asks for '{r.ingredient}' by weight "
                    f"({r.need_g:g}g in {r.packs} × {r.pack_desc}) but the "
                    f"store sells it by {c.sku.sold_by} "
                    f"({c.sku.pack_phrase()}) — a shopper cannot weigh this "
                    f"at the shelf",
                    evidence=[_ev("shopping_list", r.line_no, r.raw)],
                    ingredient=r.ingredient, sold_by=c.sku.sold_by,
                    pack_g=c.sku.pack_g)
                break

    # 3. cost
    promised = stated_total(sl_text)
    if promised is not None and world.receipt:
        actual = world.receipt["total"]
        if abs(actual - promised) > max(5.0, 0.10 * promised):
            tl = next((i for i, ln in enumerate(sl_text.splitlines(), 1)
                       if TOTAL_RE.search(ln)), 0)
            world.record(
                "cost_deviation",
                f"the sheet estimates ${promised:.2f}; the actual register "
                f"total for the same food was ${actual:.2f} "
                f"({actual - promised:+.2f})",
                evidence=[_ev("shopping_list", tl,
                              world.sheets.line("shopping_list", tl))],
                promised=promised, actual=actual)

    # 4/5. ingredients the plan uses but never buys
    listed = {r.ingredient for r in rows}
    for ing, grams in sorted(facts.mise.items()):
        if ing not in listed:
            ln = facts.mise_lines.get(ing, 0)
            world.record(
                "ingredient_absent_from_list",
                f"the cook plan measures out {grams:g}g of '{ing}' but no "
                f"shopping row buys it",
                evidence=[_ev("cook_plan", ln,
                              world.sheets.line("cook_plan", ln))],
                ingredient=ing, grams=grams)

    # Prose scan, matched by PRODUCT rather than by word.
    #
    # Word matching is a trap here: "limes" does not equal the row's "lime",
    # "tomatoes" does not equal "roma_tomato", and "chile broth" names an
    # intermediate the cook just made from stock they did buy. Comparing
    # strings produced a 78% false-positive rate on the founder corpus.
    #
    # Resolving both sides to a SKU instead collapses all of that: plural and
    # singular land on the same product, "broth" and "chicken_stock" land on
    # the same product, and only a step ingredient that no row could have
    # bought survives. That is what leaves salt and water standing.
    vocab = world.store.vocabulary()
    listed = _stems({t for r in rows for t in r.ingredient.split("_")}
                    | {t for c in world.cart
                       for t in re.split(r"[^a-z0-9]+",
                                         c.sku.search_text.lower())})

    seen: set[str] = set()
    for line_no, text in facts.step_lines:
        low = " ".join(re.split(r"[^a-z0-9]+", text.lower()))
        for phrase in vocab:
            if len(phrase) < 3 or phrase in seen:
                continue
            if not re.search(rf"\b{re.escape(phrase)}s?\b", low):
                continue
            # Covered if EVERY content word of the phrase already appears
            # somewhere in what the list buys. Requiring all tokens (rather
            # than any) keeps "chicken stock" from being excused by a
            # chicken row alone; stemming keeps "limes"/"potatoes" from
            # being flagged against "lime"/"potato_russet".
            if _stems(set(phrase.split())) <= listed:
                continue
            seen.add(phrase)
            world.record(
                "ingredient_absent_from_list",
                f"a cooking step calls for '{phrase}' — a real product "
                f"this store sells — but no shopping row buys it",
                evidence=[_ev("cook_plan", line_no,
                              world.sheets.line("cook_plan", line_no))],
                ingredient=phrase, sku=vocab[phrase], from_prose=True)
