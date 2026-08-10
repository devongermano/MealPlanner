"""The dumb robot — a deterministic shopper with no judgment whatsoever.

This is the control arm of the gate, and it is not scaffolding.

The metric this whole apparatus exists to produce is *how much human judgment
a plan requires in order to be executable*. You cannot measure that with one
instrument. You need two:

    the floor    what a machine can accomplish holding only these sheets,
                 following them as literally as language allows
    the ceiling  what a thinking cook can accomplish with the same sheets
                 plus real-world knowledge and the willingness to improvise

The gap between them is the answer. A finding the robot hits and the human
also hits is a hard defect — nobody could proceed. A finding the robot hits
but the human sails past is exactly the thing this gate was built to name: a
place where the plan silently outsources a decision to human competence and
would fail anyone who lacked it. And a plan whose floor equals its ceiling is
a plan that needs no cleverness to execute, which is the goal.

The robot is also free, instant, and deterministic, so it runs on every plan
forever, while the LLM cook runs at milestones.

Its policy is deliberately, almost stupidly literal:
  * search the shelves for exactly the word the list used, nothing smarter
  * take the best-ranked match; never weigh alternatives
  * buy the fewest whole packages that cover the stated need
  * never substitute, never infer, never ask
Every place that policy fails is a place the sheet required a human.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .sheets import parse_shopping_list
from .world import World


@dataclass
class ShopResult:
    rows_total: int
    rows_bought: int
    rows_unmatched: list       # ingredient names with no shelf match
    packages: int
    spend: float


def shop(world: World) -> ShopResult:
    """Walk the shopping list top to bottom, buying as literally as
    possible. Records nothing itself — every finding comes from the world's
    own reconciliation at checkout, so the robot and the LLM cook are scored
    by exactly the same instrument."""
    rows = parse_shopping_list(world.sheets.text.get("shopping_list", ""))
    unmatched, bought, packages = [], 0, 0

    for row in rows:
        if row.buys_nothing:            # pantry-covered; nothing to buy
            continue
        # the literal reading: the list said this word, so look for this word
        hits = world.search_product(row.ingredient)
        candidates = world.store.search(row.ingredient)
        if not candidates:
            unmatched.append(row.ingredient)
            continue
        sku = candidates[0]
        need = max(row.need_g, 1.0)
        n = max(1, math.ceil(need / sku.pack_g)) if sku.pack_g else 1
        world.add_to_cart(sku.id, n, for_row=row.line_no)
        bought += 1
        packages += n

    if world.cart:
        world.checkout()

    return ShopResult(
        rows_total=sum(1 for r in rows if not r.buys_nothing),
        rows_bought=bought, rows_unmatched=unmatched, packages=packages,
        spend=world.receipt["total"] if world.receipt else 0.0)
