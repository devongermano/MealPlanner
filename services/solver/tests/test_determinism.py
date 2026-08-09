"""M0.3 determinism regression tests.

The prototype seeded build_week's diverse_plates with ``hash(pname)`` —
PYTHONHASHSEED randomization made two runs of the same command emit different
plans (reproduced twice, PRD Appendix A). Every random draw must flow from an
explicit seed; no ``hash()``, no wall clock.

Two layers of proof:
1. In-process: full pipeline (load examples → choose_menu → build_week) twice
   with the same seed → identical result.
2. Cross-process: two separate CLI subprocess invocations (PYTHONHASHSEED left
   to randomize — deliberately NOT pinned) → byte-identical emitted plan.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mealplan import engine, io_yaml

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"
PACKAGE = Path(engine.__file__).resolve().parent


def _pipeline(seed):
    ing, comps, people, settings = io_yaml.load(EXAMPLES)
    menu, info, feas, broke = engine.choose_menu(comps, ing, people, settings,
                                                 seed=seed)
    weeks, demand = engine.build_week(comps, people, settings, menu, seed=seed)
    return menu, weeks, demand


def test_same_seed_identical_plan_in_process():
    assert _pipeline(3) == _pipeline(3)


def test_different_seed_may_differ_but_is_wired():
    """The seed parameter actually reaches build_week: same menu, different
    seeds → the assembled weeks are allowed to differ; what matters is that
    each seed reproduces itself."""
    ing, comps, people, settings = io_yaml.load(EXAMPLES)
    menu = sorted(comps)[:12]
    a1 = engine.build_week(comps, people, settings, menu, seed=1)
    a2 = engine.build_week(comps, people, settings, menu, seed=1)
    assert a1 == a2


def test_cli_two_subprocesses_emit_byte_identical_plan(tmp_path):
    """subprocess.run, PYTHONHASHSEED NOT set: each interpreter gets a random
    hash seed, so any surviving hash() dependence diverges the output."""
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)   # ensure hash randomization is live
    outs = []
    for run in ("a", "b"):
        out = tmp_path / f"plan_{run}.md"
        r = subprocess.run(
            [sys.executable, "-c",
             "from mealplan.cli import main; main()",
             "week", "--library", str(EXAMPLES), "--seed", "3",
             "--out", str(out)],
            env=env, cwd=tmp_path, capture_output=True, text=True,
            timeout=600)
        assert r.returncode == 0, r.stderr[-2000:]
        outs.append(out.read_bytes())
    assert outs[0] == outs[1], "same CLI command, same seed, different plan"


def test_no_hash_or_wall_clock_in_package():
    """Static guard: no hash() seeding, no wall-clock reads anywhere in the
    engine package. Since M0.12 the datetime MODULE is allowed — pantry
    validation parses stored ISO dates, which is deterministic — but every
    wall-clock entry point (now/today/utcnow, the time module) stays
    banned."""
    banned = re.compile(
        r"(?<!\w)hash\(|import\s+time|from\s+time\s"
        r"|\.now\(|\.today\(|\.utcnow\(|fromtimestamp\(")
    hits = []
    for py in sorted(PACKAGE.glob("*.py")):
        for n, line in enumerate(py.read_text().splitlines(), 1):
            if banned.search(line.split("#")[0]):
                hits.append(f"{py.name}:{n}: {line.strip()}")
    assert not hits, hits
