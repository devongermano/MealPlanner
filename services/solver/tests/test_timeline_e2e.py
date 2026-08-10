"""M1.12 — the ONE slow e2e for the timeline compiler: the examples
corpus with --methods and ``cook_plan_style: timeline``, run through the
real pipeline twice, byte-stable (the M0 determinism gate property
extended to the scheduled stream).

The examples library ships recipe-style (the default); this test copies
it to tmp and flips the one setting — the scheduler must run ONLY because
the style says so.
"""

import shutil
from pathlib import Path

import yaml

from mealplan import cli

REPO = Path(__file__).resolve().parents[3]


def _timeline_library(tmp_path):
    lib = tmp_path / "lib"
    shutil.copytree(REPO / "examples", lib)
    people = lib / "people.yaml"
    doc = yaml.safe_load(people.read_text())
    doc["settings"]["cook_plan_style"] = "timeline"
    people.write_text(yaml.safe_dump(doc, sort_keys=False))
    return lib


def _run(tmp_path, lib, tag):
    d = tmp_path / tag
    cli.main(["week", "--library", str(lib),
              "--methods", str(REPO / "data" / "methods-draft"),
              "--seed", "1", "--n", "8",
              "--out", str(tmp_path / f"plan_{tag}.md"),
              "--artifacts", str(d)])
    files = {p.name: p.read_bytes() for p in d.iterdir()}
    files["plan.md"] = (tmp_path / f"plan_{tag}.md").read_bytes()
    return files


def test_examples_timeline_cook_plan_byte_stable(tmp_path, capsys):
    lib = _timeline_library(tmp_path)
    a = _run(tmp_path, lib, "a")
    b = _run(tmp_path, lib, "b")
    capsys.readouterr()

    cook = a["cook_plan.md"].decode()
    # the interleaved stream, with the honesty header
    assert "**Timeline cook plan** (M1.12)" in cook
    assert "Times are ESTIMATES — calibrate by cooking" in cook
    assert "Timeline — 0:00 is when you start" in cook
    # timestamped entries and timer callouts for passive waits
    assert "- [ ] **0:00–" in cook
    assert "⏱ set a timer for" in cook
    # the honest makespan-vs-naive line per session
    assert "min** start to finish vs" in cook
    assert "one-thing-at-a-time" in cook
    # portioning matrix present (examples has meal structure) — either
    # injected into an idle-hands window or appended after the stream
    assert "Portion & pack — session" in cook
    # technique glossary still resolves stream-referenced operations
    assert "## Techniques" in cook and "- **[" in cook

    # the plan.md report carries the SAME compiled stream (h one level
    # down — one renderer, two outputs)
    plan = a["plan.md"].decode()
    assert "Timeline — 0:00 is when you start" in plan

    # byte-identical across two runs — scheduler + renderer deterministic
    assert a == b
