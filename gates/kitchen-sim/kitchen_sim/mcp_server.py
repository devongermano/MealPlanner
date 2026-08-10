"""MCP transport — the cook's entire universe, exposed as verbs.

This module is deliberately thin. All behaviour lives in world.py; this file
only decides which verbs exist and how they are described. That separation is
what lets a scripted driver replace the LLM for bulk calibration without
touching a line of the world.

Two things here are load-bearing and easy to get wrong:

1. **The tool set IS the isolation.** There is no Read, no Bash, no Grep —
   nothing that reaches a filesystem. The cook cannot consult the planner's
   source, its PRD, or its YAML, because it has no verb that could. Even the
   sheets arrive through `look_at`, which means the world knows which sheets
   were consulted and when.

2. **The docstrings are the cook's only briefing, so they are written in
   character** — as a person standing in a store, not as an API reference —
   and they are scrupulously neutral. Not one of them hints that anything
   might be wrong, names a thing to look for, or suggests that the sheets
   could be incomplete. A tool description that says "check whether the list
   is missing anything" would manufacture the finding it then congratulates
   itself for making.

Configuration comes from a RUN POINTER file rather than the environment.
MCP servers are launched by the client with a fixed registration, so a static
env block could only ever serve one persona; a pointer file lets `run.py`
re-aim the same registered server at a different persona's sheets between
runs, which is what a four-persona panel needs.

    gates/kitchen-sim/.current_run.json
        {"sheets": "<dir>", "catalog": "<path>", "out": "<path>"}

Environment variables still win if set, for one-off manual runs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .errors import SimError
from .world import Sheets, Store, World

HERE = Path(__file__).resolve().parent
RUN_POINTER = HERE.parent / ".current_run.json"

_cfg = {}
if RUN_POINTER.exists():
    _cfg = json.loads(RUN_POINTER.read_text())

_sheets_dir = os.environ.get("KITCHEN_SIM_SHEETS") or _cfg.get("sheets", "")
_catalog = (os.environ.get("KITCHEN_SIM_CATALOG") or _cfg.get("catalog")
            or str(HERE / "catalog.yaml"))
_out = os.environ.get("KITCHEN_SIM_OUT") or _cfg.get("out", "")

WORLD = World(Store.load(_catalog), Sheets(_sheets_dir), {})

mcp = MCPServer(
    name="kitchen",
    instructions=(
        "You are a person in a kitchen and a grocery store. These tools are "
        "the only things you can do and the only way you can learn anything. "
        "There is no other source of information available to you."))


_flushed = 0


def _stream() -> None:
    """Append newly-happened events to a line-delimited log, so a human can
    watch the cook shop in real time (`tail -f`). Written on every tool call,
    flushed immediately — a cook that hangs or dies still leaves a complete
    trace up to the moment it stopped."""
    global _flushed
    if not _out:
        return
    path = Path(_out).with_suffix(".events.jsonl")
    new = WORLD.log.events[_flushed:]
    if not new:
        return
    with path.open("a") as fh:
        for e in new:
            fh.write(json.dumps({"seq": e.seq, "action": e.action,
                                 "args": e.args, "outcome": e.outcome,
                                 "detail": e.detail}) + "\n")
        fh.flush()
    _flushed = len(WORLD.log.events)


def _save() -> None:
    if not _out:
        return
    _stream()
    Path(_out).write_text(json.dumps({
        "findings": [{"defect_class": f.defect_class, "detail": f.detail,
                      "state": f.state, "evidence": f.evidence,
                      "source": f.source} for f in WORLD.findings],
        "events": [{"seq": e.seq, "action": e.action, "args": e.args,
                    "outcome": e.outcome, "detail": e.detail}
                   for e in WORLD.log.events],
        "assumptions": WORLD.assumptions,
        "receipt": WORLD.receipt}, indent=1))


def _guard(fn, *a, **kw) -> str:
    """Refusals reach the cook as readable text, not stack traces — a real
    shopper is told "we don't carry that", not given a traceback."""
    try:
        out = fn(*a, **kw)
    except SimError as e:
        WORLD.log.add(fn.__name__, {"args": a}, "refused", e.detail)
        _save()
        return f"THAT DID NOT WORK: {e.detail}"
    _save()
    return out


# --------------------------------------------------------------------------- #
#  the verbs
# --------------------------------------------------------------------------- #
@mcp.tool()
def look_at(sheet: str) -> str:
    """Read one of the printed sheets you are carrying. Lines are numbered so
    you can refer to them later. Valid names: 'shopping_list', 'cook_plan'."""
    return _guard(WORLD.look_at, sheet)


@mcp.tool()
def walk_to(aisle: str) -> str:
    """Walk to an aisle and see everything on its shelves."""
    return _guard(WORLD.walk_to, aisle)


@mcp.tool()
def search_product(query: str) -> str:
    """Look for a product anywhere in the store, the way you would scan
    shelves or ask where something is. Returns what is actually stocked."""
    return _guard(WORLD.search_product, query)


@mcp.tool()
def inspect(sku_id: str) -> str:
    """Pick a package up and read its label: size, price, how it is sold,
    how long it keeps, whether it needs refrigerating."""
    return _guard(WORLD.inspect, sku_id)


@mcp.tool()
def add_to_cart(sku_id: str, packages: int, for_row: int = 0) -> str:
    """Put whole packages of a product into your cart. You buy packages, not
    grams — a shelf will not sell you 4083 g of something.

    `for_row` is the line number of the shopping-list row you are satisfying
    with this purchase; pass it so your receipt can be matched up later."""
    return _guard(WORLD.add_to_cart, sku_id, packages, for_row or None)


@mcp.tool()
def remove_from_cart(sku_id: str) -> str:
    """Put a product back on the shelf."""
    return _guard(WORLD.remove_from_cart, sku_id)


@mcp.tool()
def view_cart() -> str:
    """Look in your cart: what is in it, how much, and the running total."""
    return _guard(WORLD.view_cart)


@mcp.tool()
def assume(field: str, value: str, why: str) -> str:
    """Write down something you decided that the sheets did not tell you.

    Use this whenever you supply a fact from your own knowledge or judgment
    rather than from the printed sheets — which brand, which size, how much
    of something unspecified, what a word meant. Recording it is normal and
    expected; it is not a confession of error."""
    return _guard(WORLD.assume, field, value, why)


@mcp.tool()
def note_problem(what: str, why: str, sheet: str = "", line_no: int = 0) -> str:
    """Write down something that struck you as wrong, confusing, or not
    something you would want to eat. Cite the sheet and line if there is
    one."""
    return _guard(WORLD.note_problem, what, why, sheet, line_no)


@mcp.tool()
def give_up(what: str, why: str) -> str:
    """Stop trying to do something because you cannot work out how from what
    you were given."""
    return _guard(WORLD.give_up, what, why)


@mcp.tool()
def checkout() -> str:
    """Pay for what is in your cart and drive home."""
    return _guard(WORLD.checkout)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
