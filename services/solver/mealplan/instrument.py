"""Wall-clock stage timing for the CLI layer (M0.14, PRD §8.5).

This module is the ONE place in the package allowed to touch the wall clock,
and tests/test_determinism.py's static scan explicitly allowlists it — the
allowance stays meaningful because timing here WRAPS engine calls from the
OUTSIDE and never feeds them: no engine input, seed, branch, or result ever
depends on a measured duration. The deterministic half of the
instrumentation — LP solve COUNTS — lives in engine.py (engine.SOLVE_COUNTS)
and stays fully under the scan.

Times are recorded-only (BASELINES.md via ``make baseline``, ``--stats`` on
the CLI); CI asserts solve-count budgets, never wall clocks (PRD §8.5).
"""

import time
from contextlib import contextmanager


class StageTimer:
    """Collects ``(label, seconds)`` spans around engine calls."""

    def __init__(self):
        self.spans = []            # [(label, seconds)] in call order

    @contextmanager
    def span(self, label):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.spans.append((label, time.perf_counter() - t0))


def format_stats(solve_counts, spans):
    """Render ``--stats`` output: solve counts by stage + stage timings."""
    lines = ["-- stats (M0.14, PRD §8.5) --",
             f"LP solves (CBC invocations): {sum(solve_counts.values())}"]
    for stage in sorted(solve_counts):
        lines.append(f"  {stage:<20s} {solve_counts[stage]:>6d}")
    if spans:
        lines.append("stage timings (wall clock, collected outside the "
                     "engine; recorded-only, never asserted):")
        for label, secs in spans:
            lines.append(f"  {label:<20s} {secs * 1000:>10.1f} ms")
    return "\n".join(lines)
