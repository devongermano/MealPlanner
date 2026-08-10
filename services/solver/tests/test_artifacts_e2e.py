"""M1.1 end-to-end artifact test — the ONE artifacts test that runs a real
solve (SLOW_MODULES; every other artifacts test renders hand-built
structures in the fast tier).

solo_lifter --artifacts: all three deliverables exist, carry their
required skeleton, and are byte-identical across two runs with the same
inputs + seed (determinism is the M0 gate property, extended to the M1
deliverables)."""

from pathlib import Path

from mealplan import cli

FIXTURE = Path(__file__).parent / "fixtures" / "solo_lifter"


def _run(tmp_path, tag):
    d = tmp_path / tag
    cli.main(["all", "--library", str(FIXTURE), "--seed", "1", "--n", "6",
              "--out", str(tmp_path / f"plan_{tag}.md"),
              "--artifacts", str(d)])
    return {p.name: p.read_bytes() for p in d.iterdir()}


def test_solo_lifter_artifacts_complete_and_deterministic(tmp_path, capsys):
    a = _run(tmp_path, "a")
    b = _run(tmp_path, "b")
    capsys.readouterr()                 # swallow the plan print

    # all three deliverables, one eat sheet for the one person
    assert set(a) == {"shopping_list.md", "cook_plan.md", "eat_sol.md"}

    shop = a["shopping_list.md"].decode()
    assert "- [ ] " in shop                       # checkbox rows
    assert "at estimated prices" in shop          # cost provenance label
    cook = a["cook_plan.md"].decode()
    assert "## Session 0" in cook
    assert "Shortest keeps this session" in cook
    eat = a["eat_sol.md"].decode()
    assert "## Day 1" in eat and "## Day 7" in eat
    assert "Mode: **precision**" in eat

    # traceability footer on every file
    for text in (shop, cook, eat):
        assert f"plan seed 1 · library {FIXTURE}" in text

    # byte-identical across two runs — same inputs + seed => same artifacts
    assert a == b
