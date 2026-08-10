"""M1.9 meal-layer pipeline tests (M19_SPEC §9, slow tier).

Rule (promoted from the proposals): fixtures set meals_per_day ONLY where
the test targets the meal layer — the solve budget never leaks into every
fixture. The meal-configured library is built in tmp_path from family_four;
the checked-in fixtures stay meal-free, so the determinism golden and the
solve-count baselines are untouched.

Covers: ONE full end-to-end (build → deal → lock → verify-plan round trip
with meal structure embedded), the picker-nudge structural effect, and the
inertness gate (no meals configured ⇒ no meal keys anywhere — the
byte-identity of the no-meals pipeline is guarded by the UNREGENERATED
determinism golden in test_capabilities).
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

import pytest
import yaml

from mealplan import cli, engine, io_yaml, meals
from mealplan.model import resolve_meal_slots

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_cli(argv):
    """(exit_code, stdout) for one CLI invocation — success returns
    normally (code 0); infeasible/errors raise SystemExit."""
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            cli.main(argv)
    except SystemExit as e:
        code = 0 if e.code is None else e.code
    return code, buf.getvalue()


def _meal_library(tmp_path):
    """family_four with a meal structure for TWO people (the blended
    example: plain meals_per_day for one, named slots with a family_style
    breakfast + an interchangeable lunch for another); carla and dave stay
    meal-free — the layer must be per-person."""
    lib = tmp_path / "family_four_meals"
    shutil.copytree(FIXTURES / "family_four", lib)
    doc = yaml.safe_load((lib / "people.yaml").read_text())
    doc["people"]["bruno"]["meals_per_day"] = 2
    doc["people"]["petit"]["serving_model"] = "portioned"
    doc["people"]["petit"]["meal_slots"] = [
        {"name": "breakfast", "serving_model": "family_style"},
        {"name": "lunch", "interchangeable": True},
        {"name": "dinner"},
    ]
    (lib / "people.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return lib


@pytest.fixture(scope="module")
def locked(tmp_path_factory):
    """ONE lock run shared by every assertion below (LP time is the
    budget)."""
    tmp = tmp_path_factory.mktemp("m19")
    lib = _meal_library(tmp)
    plans = tmp / "plans"
    code, out = run_cli(["lock", "--library", str(lib), "--seed", "1",
                         "--n", "8", "--date", "2026-08-10",
                         "--plans", str(plans), "--json"])
    assert code == 0, out[-2000:]
    doc = json.loads(out)
    assert doc["ok"] is True
    plan_path = Path(doc["result"]["plan_path"])
    plan = yaml.safe_load(plan_path.read_text())
    return dict(lib=lib, plans=plans, result=doc["result"],
                plan_path=plan_path, plan=plan)


def test_lock_artifact_carries_meal_structure(locked):
    plan = locked["plan"]
    assert "meals" in plan
    assert set(plan["meals"]) == {"bruno", "petit"}     # only the configured
    for pname, days in plan["meals"].items():
        assert len(days) == 7
        for d, md in enumerate(days):
            assert {"meals", "notes"} <= set(md)
            # day plate authoritative and conserved gram-for-gram
            dealt = {}
            for meal in md["meals"]:
                for cid, g in meal["items"].items():
                    dealt[cid] = dealt.get(cid, 0) + g
            plate = plan["portions"][pname][d]
            assert dealt == {c: g for c, g in plate.items() if g}, \
                (pname, d)
    assert len(plan["meals"]["bruno"][0]["meals"]) == 2
    petit_meals = plan["meals"]["petit"][0]["meals"]
    assert [m["slot"] for m in petit_meals] == ["breakfast", "lunch",
                                                "dinner"]
    assert petit_meals[0]["serving_model"] == "family_style"
    assert petit_meals[1]["serving_model"] == "portioned"


def test_eat_sheets_render_meal_sections(locked):
    plan_dir = locked["plan_path"].parent
    bruno = (plan_dir / "eat_bruno.md").read_text()
    assert "### meal_1 — portioned" in bruno
    assert "### meal_2 — portioned" in bruno
    assert "- meal subtotal:" in bruno
    petit = (plan_dir / "eat_petit.md").read_text()
    assert "### breakfast — family style" in petit
    assert "### lunch — portioned" in petit
    # meal-free people keep the flat day sheet
    carla = (plan_dir / "eat_carla.md").read_text()
    assert "meal subtotal" not in carla


def test_verify_plan_round_trip_re_deals_and_verifies(locked):
    code, out = run_cli(["verify-plan", str(locked["plan_path"]), "--json"])
    assert code == 0, out[-2000:]
    doc = json.loads(out)
    assert doc["ok"] is True
    rep = doc["result"]
    assert rep["verified"] is True
    assert rep["meals_ok"] is True


def test_verify_plan_catches_tampered_meals(locked, tmp_path):
    tampered = yaml.safe_load(yaml.safe_dump(locked["plan"]))
    victim = tampered["meals"]["bruno"][0]["meals"][0]["items"]
    cid = sorted(victim)[0]
    victim[cid] = victim[cid] + 25     # altered allocation, hash untouched
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(tampered, sort_keys=False))
    code, out = run_cli(["verify-plan", str(p), "--json"])
    assert code == 3
    doc = json.loads(out)
    assert doc["error"]["details"]["meals_ok"] is False


def test_json_week_carries_meals_and_dealability(locked):
    code, out = run_cli(["week", "--library", str(locked["lib"]),
                         "--seed", "1", "--n", "8", "--json"])
    assert code == 0
    doc = json.loads(out)
    result = doc["result"]
    assert set(result["meals"]) == {"bruno", "petit"}
    # the dealt structure conserves the solved week it rode in with
    for pname, days in result["meals"].items():
        for d, md in enumerate(days):
            dealt = {}
            for meal in md["meals"]:
                for cid, g in meal["items"].items():
                    dealt[cid] = dealt.get(cid, 0) + g
            plate = result["weeks"][pname][d]
            assert dealt == {c: g for c, g in plate.items() if g}
    # picker-nudge structural effect (§3.5): chosen plates carry >=
    # min(mains_available, n) distinct mains on a clear majority of days.
    # Asserted for bruno (high-kcal, n=2) only: the nudge selects among
    # LP-FEASIBLE candidates, and petit's low targets cannot fit 3 mains
    # at authored serve minimums — plate composition caps the dealer and
    # the nudge alike (M19_SPEC §0d); the dealer declares it per-day via
    # variety_unmet and doctor's meal section reports it up front.
    ing, comps, people, settings = io_yaml.load(locked["lib"])
    menu = result["menu"]
    for pname, n in (("bruno", 2),):
        ok_days = 0
        days_counted = 0
        for d, plate in enumerate(result["weeks"][pname]):
            if not plate:
                continue
            days_counted += 1
            avail_mains = len({
                i for i in menu
                if comps[i]["role"] == "main"
                and engine.eligible(comps[i], people[pname])
                and engine.available_on(comps[i], d, settings, ing)})
            plate_mains = len({c for c in plate
                               if comps[c]["role"] == "main"})
            if plate_mains >= min(avail_mains, n):
                ok_days += 1
        assert days_counted and ok_days >= days_counted - 2, \
            (pname, ok_days, days_counted)


def test_interchangeable_slot_notes_present(locked):
    petit_days = locked["plan"]["meals"]["petit"]
    codes = {x["code"] for md in petit_days for x in md["notes"]}
    assert "interchange_applied" in codes


def test_doctor_reports_meal_layer_section(locked):
    code, out = run_cli(["doctor", "--library", str(locked["lib"]),
                         "--json"])
    assert code == 0
    doc = json.loads(out)
    data = doc["result"]["data"]
    assert set(data["meal_layer"]) == {"bruno", "petit"}
    entry = data["meal_layer"]["petit"]
    assert entry["slots"] == 3
    for k in ("worst_day_mains", "worst_day_side_mass_g",
              "side_mass_needed_g", "expected_variety_unmet_days"):
        assert k in entry


# --------------------------------------------------------------------------- #
#  inertness — the hard gate: nobody configures meals ⇒ no meal keys, and
#  the pipeline output is byte-identical (goldens pass UNREGENERATED —
#  guarded by test_capabilities' golden byte compare running in this suite)
# --------------------------------------------------------------------------- #
def test_no_meals_pipeline_emits_no_meal_keys(tmp_path):
    lib = FIXTURES / "solo_lifter"
    plans = tmp_path / "plans"
    code, out = run_cli(["lock", "--library", str(lib), "--seed", "0",
                         "--n", "6", "--date", "2026-08-10",
                         "--plans", str(plans), "--json"])
    assert code == 0, out[-2000:]
    doc = json.loads(out)
    plan = yaml.safe_load(Path(doc["result"]["plan_path"]).read_text())
    assert "meals" not in plan
    code, out = run_cli(["verify-plan", doc["result"]["plan_path"],
                         "--json"])
    assert code == 0
    rep = json.loads(out)
    assert rep["result"]["verified"] is True
    assert "meals_ok" not in rep["result"]     # historical report shape
    # the --json week document carries no meals key either
    code, out = run_cli(["week", "--library", str(lib), "--seed", "0",
                         "--n", "6", "--json"])
    assert code == 0
    assert "meals" not in json.loads(out)["result"]
    # and doctor has no meal-layer section
    code, out = run_cli(["doctor", "--library", str(lib), "--json"])
    assert code == 0
    assert "meal_layer" not in json.loads(out)["result"]["data"]


def test_deal_week_inert_on_meal_free_fixture():
    ing, comps, people, settings = io_yaml.load(FIXTURES / "solo_lifter")
    assert all(resolve_meal_slots(p) is None for p in people.values())
    weeks = {pn: [{} for _ in range(7)] for pn in people}
    assert meals.deal_week(people, comps, weeks) == {}
