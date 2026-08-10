"""One verb per invocation — the cook's world as a command line.

The MCP transport (mcp_server.py) is the intended one: it gives the cook a
tool surface with no filesystem verb anywhere in it, which is isolation by
construction. This CLI is the fallback for when the MCP server has not been
registered/approved in a session yet, and it doubles as the interface a
scripted calibration driver wants.

Its isolation is weaker — a shell is a shell — so the cook's briefing never
mentions a repository, and the only path it is ever given is this command.
Findings are identical either way: both transports drive the same World and
are scored by the same reconciliation.

State persists between invocations through a JSON file, because each call is
a fresh process and a shopping trip is not.

    python -m kitchen_sim.cook_cli look_at shopping_list
    python -m kitchen_sim.cook_cli search_product "chicken breast"
    python -m kitchen_sim.cook_cli add_to_cart chicken_breast_family 4 --row 5
    python -m kitchen_sim.cook_cli checkout
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .errors import SimError
from .world import Sheets, Store, World

HERE = Path(__file__).resolve().parent
POINTER = HERE.parent / ".current_run.json"


def _cfg() -> dict:
    if not POINTER.exists():
        sys.exit("no .current_run.json — the run has not been set up")
    return json.loads(POINTER.read_text())


def _load() -> tuple[World, Path]:
    cfg = _cfg()
    world = World(Store.load(cfg.get("catalog", HERE / "catalog.yaml")),
                  Sheets(cfg["sheets"]), {})
    state = Path(cfg["out"]).with_suffix(".state.json")
    if state.exists():
        world.load_state(json.loads(state.read_text()))
    return world, state


def _persist(world: World, state_path: Path) -> None:
    cfg = _cfg()
    st = world.to_state()
    state_path.write_text(json.dumps(st, indent=1))
    Path(cfg["out"]).write_text(json.dumps(
        {"findings": st["findings"], "events": st["events"],
         "assumptions": st["assumptions"], "receipt": st["receipt"]},
        indent=1))
    # append-only stream so a human can watch the trip happen live
    stream = Path(cfg["out"]).with_suffix(".events.jsonl")
    already = sum(1 for _ in stream.open()) if stream.exists() else 0
    with stream.open("a") as fh:
        for e in st["events"][already:]:
            fh.write(json.dumps(e) + "\n")


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print("usage: cook_cli <verb> [args...]", file=sys.stderr)
        return 2
    verb, args = argv[0], argv[1:]
    row = None
    if "--row" in args:
        i = args.index("--row")
        row = int(args[i + 1])
        args = args[:i] + args[i + 2:]

    world, state_path = _load()
    fn = {
        "look_at": lambda: world.look_at(args[0]),
        "walk_to": lambda: world.walk_to(args[0]),
        "search_product": lambda: world.search_product(" ".join(args)),
        "inspect": lambda: world.inspect(args[0]),
        "add_to_cart": lambda: world.add_to_cart(args[0], int(args[1]), row),
        "remove_from_cart": lambda: world.remove_from_cart(args[0]),
        "view_cart": lambda: world.view_cart(),
        "assume": lambda: world.assume(args[0], args[1], " ".join(args[2:])),
        "note_problem": lambda: world.note_problem(
            args[0], " ".join(args[1:])),
        "give_up": lambda: world.give_up(args[0], " ".join(args[1:])),
        "checkout": lambda: world.checkout(),
    }.get(verb)
    if fn is None:
        print(f"there is no such action as {verb!r}. you can: "
              f"look_at, walk_to, search_product, inspect, add_to_cart, "
              f"remove_from_cart, view_cart, assume, note_problem, give_up, "
              f"checkout", file=sys.stderr)
        return 2

    try:
        out = fn()
    except SimError as e:
        world.log.add(verb, {"args": args}, "refused", e.detail)
        _persist(world, state_path)
        print(f"THAT DID NOT WORK: {e.detail}")
        return 0
    except (IndexError, ValueError) as e:
        print(f"bad arguments for {verb}: {e}", file=sys.stderr)
        return 2
    _persist(world, state_path)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
