"""M0.10 score scale consistency regression tests.

The prototype's score_menu (plan.py:341) computed its perishable-waste term
from purchase() at 1 batch per component while the cost term used
estimate_batches — two different demand scales inside the same score. Fixed:
ALL score estimates (waste, time, cost) are computed at the one
estimated-batch scale, and the frontier reports that same waste.

Tests:
- the waste term responds to demand scale: the same menu scored against
  people with doubled targets yields a larger waste estimate (the 1-batch
  bug made it constant in demand);
- score_menu's waste equals purchase() waste at estimate_batches scale;
- a frontier point's reported waste equals the estimated-batch purchase
  waste for the menu it chose.
"""

import re
from pathlib import Path

from mealplan import io_yaml
from mealplan.cli import frontier
from mealplan.costing import estimate_batches, purchase
from mealplan.engine import score_menu
from mealplan.model import Person

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"

# fixed menu: deterministic, independent of the search
MENU = ["birria_chuck", "bulgogi_flank", "carnitas", "charred_salsa_roja",
        "cheddar_shred", "cilantro_lime_rice", "egg_potato_scramble",
        "flour_tortilla", "fusilli_cooked", "gordita_shell"]


def load():
    return io_yaml.load(EXAMPLES)


def doubled(people):
    return {pn: Person.from_raw(pn, {**p.raw,
                                     "targets": {k: v * 2 for k, v
                                                 in p.raw["targets"].items()}})
            for pn, p in people.items()}


def test_score_waste_is_at_estimated_batch_scale():
    ing, comps, people, settings = load()
    _, info = score_menu(comps, ing, MENU, settings, people)
    eb = estimate_batches(comps, people, settings, MENU)
    _, wp_est, _ = purchase(comps, ing, MENU, eb)
    assert info["waste_perishable"] == wp_est
    # and the estimated scale genuinely differs from the 1-batch scale here,
    # so this assertion distinguishes the fix from the prototype bug
    _, wp_one, _ = purchase(comps, ing, MENU)
    assert wp_est != wp_one


def test_score_waste_responds_to_demand_scale():
    ing, comps, people, settings = load()
    _, info1 = score_menu(comps, ing, MENU, settings, people)
    people2 = doubled(people)
    _, info2 = score_menu(comps, ing, MENU, settings, people2)
    # doubled targets -> more estimated batches -> more perishable leftover
    # on this fixture. The 1-batch bug made these identical for ANY demand.
    eb1 = estimate_batches(comps, people, settings, MENU)
    eb2 = estimate_batches(comps, people2, settings, MENU)
    assert max(eb2.values()) > max(eb1.values())
    assert info2["waste_perishable"] > info1["waste_perishable"]


def test_frontier_reported_waste_matches_estimated_batch_purchase(capsys):
    ing, comps, people, settings = load()
    cap = 600
    seen = frontier(comps, ing, people, settings, lo=cap, hi=cap, step=50,
                    n=8, seed=0)
    out = capsys.readouterr().out
    menu, _feas = seen[cap]
    # parse the single data row: budget spend dishes cuisines waste feasible
    row = [ln for ln in out.splitlines() if ln.strip().startswith(str(cap))]
    assert row, out
    m = re.search(r"(\d+(?:\.\d+)?)g", row[0])
    assert m, row[0]
    reported = float(m.group(1))

    st = dict(settings)
    st["budget"] = {"mode": "shared", "total": cap}
    eb = estimate_batches(comps, people, st, menu)
    _, wp, _ = purchase(comps, ing, menu, eb)
    assert reported == wp
