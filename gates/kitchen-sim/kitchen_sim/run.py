"""Run the gate: sheets in, findings out.

    python -m kitchen_sim.run --sheets <dir> [--driver naive] [--json out.json]

`--driver naive` runs the deterministic robot (free, instant, the floor).
The LLM cook runs through the MCP transport instead and writes into the same
World, so both drivers are scored by identical machinery — the only thing
that differs is who is holding the sheets.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .naive_shopper import shop
from .world import Sheets, Store, World

HERE = Path(__file__).resolve().parent
DEFAULT_CATALOG = HERE / "catalog.yaml"


def build_world(sheets_dir: str | Path, catalog: str | Path = DEFAULT_CATALOG,
                persona: dict | None = None) -> World:
    return World(Store.load(catalog), Sheets(sheets_dir), persona or {})


def report(world: World, result=None) -> str:
    L = ["# Kitchen sim — shopping trip", ""]
    if result:
        L += [f"- shopping rows: **{result.rows_total}**, of which "
              f"**{result.rows_bought}** could be bought",
              f"- packages in cart: **{result.packages}**, "
              f"register total: **${result.spend:.2f}**"]
        if result.rows_unmatched:
            L.append(f"- rows with NO shelf match: "
                     f"**{', '.join(result.rows_unmatched)}**")
    counts = Counter(f.defect_class for f in world.findings)
    L += ["", f"## Findings — {len(world.findings)} total", ""]
    for cls, n in counts.most_common():
        L.append(f"- `{cls}` × {n}")
    L.append("")
    for f in world.findings:
        L.append(f"### [{f.defect_class}] {f.detail}")
        for e in f.evidence:
            L.append(f"    {e['sheet']}:{e['line_no']} | {e['text']}")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheets", required=True,
                    help="directory holding the cook-facing .md sheets")
    ap.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    ap.add_argument("--driver", default="naive", choices=["naive"])
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    world = build_world(a.sheets, a.catalog)
    result = shop(world)
    text = report(world, result)

    if a.out:
        Path(a.out).write_text(text)
    else:
        print(text)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(
            {"findings": [{"defect_class": f.defect_class,
                           "detail": f.detail, "state": f.state,
                           "evidence": f.evidence, "source": f.source}
                          for f in world.findings],
             "events": [{"seq": e.seq, "action": e.action, "args": e.args,
                         "outcome": e.outcome, "detail": e.detail}
                        for e in world.log.events],
             "assumptions": world.assumptions,
             "receipt": world.receipt}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
