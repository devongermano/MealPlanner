#!/usr/bin/env python
"""Record measured performance baselines (M0.14, PRD §8.5: measure, then
promise). Invoked by `make baseline`.

Runs the full pipeline N times (default 5) on:

- examples/ (the founder corpus), in the CLI `week` command shape:
  load -> doctor -> choose_menu (CLI default n=12, seed 0) -> build_week ->
  session_plan -> purchase;
- tests/fixtures/solo_lifter with the frozen golden-pipeline parameters
  (test_capabilities.GOLDEN_MENU_KW — the same run the determinism golden
  and the CI solve-budget test assert on);
- (M1.5) the interactive primitives on solo_lifter — one plate LP and one
  replate (day rebalance) — and the full lock round trip through the real
  CLI (`mealplan lock` then `mealplan verify-plan`, M1.3), so the lock and
  verify-plan stages carry recorded baselines too.

Writes BASELINES.md: exact per-stage LP-solve counts (deterministic —
verified identical across all runs, asserted in CI by
tests/test_perf_budget.py) and median/min/max wall timings per stage
(recorded-only; CI never asserts wall clocks), plus machine/env
identification, plus (M1.5, PRD §8.5) a "Targets (provisional)" section:
interactive-latency targets set at 2x headroom over the recorded medians —
labeled provisional and NEVER asserted in tests. The calendar date is
passed in by the Makefile: the engine has no wall clock, and this script
only measures durations (time.perf_counter), never a date.

This script lives OUTSIDE the mealplan package on purpose: the determinism
static scan (tests/test_determinism.py) bans wall-clock reads inside the
package; timing here wraps engine calls and never feeds them.
"""

import argparse
import contextlib
import io
import platform
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

SOLVER = Path(__file__).resolve().parents[1]
REPO = SOLVER.parents[1]
EXAMPLES = REPO / "examples"
SOLO = SOLVER / "tests" / "fixtures" / "solo_lifter"

sys.path.insert(0, str(SOLVER / "tests"))    # for tests/_shared.py

import pulp                                        # noqa: E402
from _shared import GOLDEN_MENU_KW, GOLDEN_SEED    # noqa: E402

from mealplan import cli, costing, engine, io_yaml, meals  # noqa: E402


@contextmanager
def span(timings, label):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timings.setdefault(label, []).append(time.perf_counter() - t0)


def run_examples(timings):
    """The CLI `week` pipeline on the founder corpus, CLI defaults."""
    engine.reset_solve_counts()
    with span(timings, "load"):
        ing, comps, people, settings = io_yaml.load(EXAMPLES)
    with span(timings, "doctor"):
        engine.doctor(comps, people, settings, ing=ing)
    with span(timings, "choose_menu"):
        menu, info, feas, broke = engine.choose_menu(
            comps, ing, people, settings, n=12, seed=0)
    with span(timings, "build_week"):
        weeks, demand = engine.build_week(comps, people, settings, menu,
                                          seed=0, ing=ing)
    with span(timings, "session_plan"):
        sp = costing.session_plan(comps, ing, settings, weeks)
    # M1.9: the meal dealer (PROVISIONAL row — zero LP solves by
    # construction; timing only, so pure-Python regressions stay visible)
    with span(timings, "meal-alloc"):
        meals.deal_week(people, comps, weeks)
    with span(timings, "purchase"):
        # same shape as the CLI week command (cli.py): a menu component the
        # week never serves gets no batch and is not purchased
        costing.purchase(comps, ing,
                         [i for i in menu if sp["batches"].get(i)],
                         sp["batches"])
    return engine.solve_counts()


def run_solo(timings):
    """The frozen golden pipeline (test_capabilities.golden_payload)."""
    engine.reset_solve_counts()
    with span(timings, "load"):
        ing, comps, people, settings = io_yaml.load(SOLO)
    with span(timings, "choose_menu"):
        menu, info, feas, broke = engine.choose_menu(
            comps, ing, people, settings, **GOLDEN_MENU_KW)
    with span(timings, "build_week"):
        weeks, demand = engine.build_week(comps, people, settings, menu,
                                          seed=GOLDEN_SEED, ing=ing)
    with span(timings, "session_plan"):
        sp = costing.session_plan(comps, ing, settings, weeks)
    with span(timings, "purchase"):
        # same menu-filtered shape as the CLI week command (cli.py)
        costing.purchase(comps, ing,
                         [i for i in menu if sp["batches"].get(i)],
                         sp["batches"])
    return engine.solve_counts()


def run_interactive(timings):
    """M1.5: the two interactive primitives, on the golden solo fixture —
    ONE plate LP (the single-plate solve every interactive surface waits
    on) and ONE replate (the §4.4 day rebalance). Menu setup is untimed."""
    engine.reset_solve_counts()
    ing, comps, people, settings = io_yaml.load(SOLO)
    menu, *_ = engine.choose_menu(comps, ing, people, settings,
                                  **GOLDEN_MENU_KW)
    person = next(iter(people.values()))
    with span(timings, "plate"):
        engine.plate(person, comps, menu)
    with span(timings, "replate"):
        engine.replate(person, comps, menu, 0, settings, ing=ing)
    return engine.solve_counts()


def run_lock_roundtrip(timings):
    """M1.5/M1.3: the full lock round trip through the REAL CLI — `mealplan
    lock` (solve + write plans/<key>/) then `mealplan verify-plan`
    (hash check + re-solve from the embedded snapshot). Uses a throwaway
    plans dir per run; the lock/verify-plan stage counters recorded here
    are the CLI's own instrumented stages plus these two wrappers."""
    counts = {}
    with tempfile.TemporaryDirectory() as tmp:
        with contextlib.redirect_stdout(io.StringIO()):
            with span(timings, "lock"):
                cli.main(["lock", "--library", str(SOLO),
                          "--date", "2026-01-05", "--seed", "0", "--n", "6",
                          "--plans", str(Path(tmp) / "plans")])
            for k, v in engine.solve_counts().items():
                counts[k] = counts.get(k, 0) + v
            plan = next(Path(tmp).glob("plans/*/plan.yaml"))
            with span(timings, "verify-plan"):
                cli.main(["verify-plan", str(plan)])
            for k, v in engine.solve_counts().items():
                counts[k] = counts.get(k, 0) + v
    return counts


def measure(runner, runs):
    timings = {}
    counts = None
    for r in range(runs):
        c = runner(timings)
        if counts is None:
            counts = c
        elif c != counts:
            sys.exit(f"NONDETERMINISM: run {r} solve counts {c} != run 0 "
                     f"{counts} — counts must be identical across runs")
    return counts, timings


def fmt_ms(secs):
    return f"{secs * 1000:,.1f}"


def render(name, what, counts, timings, runs):
    total = sum(counts.values())
    L = [f"## {name}", "", what, "",
         f"### LP-solve counts (deterministic; identical across all {runs} "
         "runs)", "",
         "| stage | CBC invocations |", "|---|---|"]
    for stage in sorted(counts):
        L.append(f"| {stage} | {counts[stage]} |")
    L += [f"| **total** | **{total}** |", "",
          f"### Wall timings over {runs} runs (recorded-only — never "
          "asserted in CI, PRD §8.5)", "",
          "| stage | median ms | min ms | max ms |", "|---|---|---|---|"]
    for label, vals in timings.items():
        L.append(f"| {label} | {fmt_ms(statistics.median(vals))} "
                 f"| {fmt_ms(min(vals))} | {fmt_ms(max(vals))} |")
    tot = [sum(vals[r] for vals in timings.values())
           for r in range(runs)]
    L.append(f"| **pipeline total** | **{fmt_ms(statistics.median(tot))}** "
             f"| {fmt_ms(min(tot))} | {fmt_ms(max(tot))} |")
    L.append("")
    return L


def render_targets(int_t, ex_t, lock_t, runs):
    """M1.5 (PRD §8.5, measure-then-promise): provisional interactive-
    latency targets = 2x headroom over the recorded medians."""
    def med(vals):
        return statistics.median(vals)

    def total_med(timings):
        return med([sum(vals[r] for vals in timings.values())
                    for r in range(runs)])

    rows = [
        ("interactive single plate (plate LP, solo golden menu)",
         med(int_t["plate"])),
        ("replate — day rebalance (engine.replate, solo golden menu)",
         med(int_t["replate"])),
        ("full pipeline (`mealplan week` shape, examples corpus)",
         total_med(ex_t)),
        ("lock round trip (`mealplan lock` + `mealplan verify-plan`, "
         "solo_lifter)", total_med(lock_t)),
    ]
    L = ["## Targets (provisional) — M1.5, PRD §8.5", "",
         "Interactive-latency targets set FROM the measured medians above "
         "with **2x headroom**. All four are **provisional**: ratified only "
         "when a re-recorded baseline on the reference machine keeps them "
         "honest, and revised in a reviewed commit like every other number "
         "here. They are **never asserted in tests** — wall clocks stay out "
         "of CI (solve-count budgets are the deterministic CI guard).", "",
         "| interaction | measured median | provisional target (2x) |",
         "|---|---|---|"]
    for name, secs in rows:
        L.append(f"| {name} | {fmt_ms(secs)} ms | {fmt_ms(secs * 2)} ms |")
    L.append("")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True,
                    help="record date (passed in by the Makefile — the "
                         "engine has no wall clock)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args()

    print(f"measuring solo_lifter golden pipeline x{a.runs} ...",
          file=sys.stderr)
    solo_counts, solo_t = measure(run_solo, a.runs)
    print(f"measuring examples week pipeline x{a.runs} ...", file=sys.stderr)
    ex_counts, ex_t = measure(run_examples, a.runs)
    print(f"measuring interactive plate/replate x{a.runs} ...",
          file=sys.stderr)
    int_counts, int_t = measure(run_interactive, a.runs)
    print(f"measuring lock round trip x{a.runs} ...", file=sys.stderr)
    lock_counts, lock_t = measure(run_lock_roundtrip, a.runs)

    cbc = Path(pulp.PULP_CBC_CMD().path).name
    L = ["# Measured performance baselines (M0.14, PRD §8.5)", "",
         "Generated by `make baseline` "
         "(services/solver/tools/baseline.py). Do not hand-edit numbers; "
         "re-record deliberately in a reviewed commit.", "",
         f"- **date:** {a.date}",
         f"- **machine:** {platform.platform()} ({platform.machine()})",
         f"- **python:** {platform.python_version()}",
         f"- **PuLP:** {pulp.__version__} (bundled CBC: `{cbc}`)",
         f"- **runs per pipeline:** {a.runs}", "",
         "Solve counts are deterministic and asserted in CI "
         "(tests/test_perf_budget.py): the golden solo_lifter pipeline "
         "exactly; the examples pipeline against the recorded total + ~20% "
         "headroom. Wall timings are recorded-only — CI never asserts wall "
         "clocks (PRD §8.5).", ""]
    L += render("Golden pipeline — tests/fixtures/solo_lifter",
                "load -> choose_menu"
                f"({', '.join(f'{k}={v}' for k, v in GOLDEN_MENU_KW.items())})"
                " -> build_week -> session_plan -> purchase — the frozen "
                "parameter set of the determinism golden.",
                solo_counts, solo_t, a.runs)
    L += render("Examples corpus — `mealplan week` pipeline",
                "load -> doctor -> choose_menu(n=12, seed=0; CLI defaults) "
                "-> build_week -> session_plan -> purchase on examples/.",
                ex_counts, ex_t, a.runs)
    L += render("Interactive primitives — tests/fixtures/solo_lifter",
                "One plate LP and one replate (day rebalance, §4.4) on the "
                "golden menu — the solves an interactive surface waits on "
                "(M1.5). Counts include the untimed golden menu setup.",
                int_counts, int_t, a.runs)
    L += render("Lock round trip — tests/fixtures/solo_lifter (M1.3)",
                "`mealplan lock --n 6 --seed 0` (solve + write "
                "plans/<key>/) then `mealplan verify-plan` (hash check + "
                "re-solve from the embedded snapshot), through the real "
                "CLI with its default search parameters.",
                lock_counts, lock_t, a.runs)
    L += render_targets(int_t, ex_t, lock_t, a.runs)
    Path(a.out).write_text("\n".join(L))
    print(f"wrote {a.out}", file=sys.stderr)
    print(f"solo counts:     {solo_counts}", file=sys.stderr)
    print(f"examples counts: {ex_counts} "
          f"(total {sum(ex_counts.values())})", file=sys.stderr)


if __name__ == "__main__":
    main()
