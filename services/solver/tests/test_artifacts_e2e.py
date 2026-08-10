"""M1.1/M1.10 end-to-end artifact tests — the artifacts tests that run a
real solve (SLOW_MODULES; every other artifacts/cook-script test renders
hand-built structures in the fast tier).

1. solo_lifter --artifacts: all three deliverables exist, carry their
   required skeleton, and are byte-identical across two runs with the same
   inputs + seed (determinism is the M0 gate property, extended to the M1
   deliverables).
2. examples --methods (M1.10): the compiled cook script — method-step
   annotations, station summary, portioning matrix, technique glossary,
   meal-structured eat sheets — across all four artifacts, byte-stable x2.
"""

from pathlib import Path

from mealplan import cli

FIXTURE = Path(__file__).parent / "fixtures" / "solo_lifter"
REPO = Path(__file__).resolve().parents[3]


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


# --------------------------------------------------------------------------- #
#  M1.10 — the ONE slow e2e for the sheet rework: examples + --methods
# --------------------------------------------------------------------------- #
def _run_methods(tmp_path, tag):
    d = tmp_path / tag
    cli.main(["week", "--library", str(REPO / "examples"),
              "--methods", str(REPO / "data" / "methods-draft"),
              "--seed", "1", "--n", "8",
              "--out", str(tmp_path / f"plan_{tag}.md"),
              "--artifacts", str(d)])
    files = {p.name: p.read_bytes() for p in d.iterdir()}
    files["plan.md"] = (tmp_path / f"plan_{tag}.md").read_bytes()
    return files


def test_examples_methods_cook_script_all_artifacts_byte_stable(tmp_path,
                                                                capsys):
    a = _run_methods(tmp_path, "a")
    b = _run_methods(tmp_path, "b")
    capsys.readouterr()

    # all four artifacts: the three deliverables (two eat sheets — examples
    # has jimbo + devon) plus the plan.md report
    assert set(a) == {"shopping_list.md", "cook_plan.md", "eat_jimbo.md",
                      "eat_devon.md", "plan.md"}

    cook = a["cook_plan.md"].decode()
    # compiled session script: annotated checkbox steps with station/mode/
    # duration, station summary, technique glossary
    assert "Stations (single-batch step estimates, provisional" in cook
    assert " · active ~" in cook or " · passive ~" in cook
    assert "## Techniques" in cook
    assert "- **[" in cook
    # portioning matrix: examples has meal structure (jimbo 3 slots
    # portioned, devon breakfast family_style) — per-person container rows
    # AND shared-container storage totals must both appear
    assert "Portion & pack — session" in cook
    assert "· eat day" in cook
    assert "in a shared container" in cook

    # eat sheets: per-slot serving-model phrasing
    jimbo = a["eat_jimbo.md"].decode()
    assert "— portioned" in jimbo
    assert "- packed container: " in jimbo
    devon = a["eat_devon.md"].decode()
    # M1.13: examples is a DISH library — the meal heading SAYS the dish
    # ("breakfast — <dish name> — family style") and the servings scalar
    # is rendered; family-style take-amount voice survives underneath
    assert "### breakfast — " in devon
    assert "— family style" in devon
    assert "portioned within its ratio bands" in devon
    assert "— take " in devon

    # one renderer, two outputs: the plan.md report carries the SAME
    # compiled script (headings one level down)
    plan = a["plan.md"].decode()
    assert "### Session 0 — cook day 0" in plan
    assert "Portion & pack — session" in plan
    assert "### Techniques" in plan

    # byte-identical across two runs (method-fragment rendering included)
    assert a == b
