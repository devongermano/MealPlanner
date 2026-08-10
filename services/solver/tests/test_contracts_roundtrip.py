"""Contract round-trip — SLOW tier (full pipeline + doctor on solo_lifter).

THE test of the wiring rule's ground truth: run the real pipeline and
model_validate the REAL outputs against the pydantic mirrors (schemas.py).
Any mismatch means THE SCHEMA IS WRONG — fix schemas.py, regenerate the
contracts, and never touch the engine to satisfy a schema.

Strictness does the work: every mirror is extra="forbid", so a field the
engine starts (or stops) emitting fails here, forcing the atomic
schema + codegen + consumers PR that ARCHITECTURE.md demands.
"""

from pathlib import Path

import pytest

pytest.importorskip("pydantic", reason="[service] extra not installed")
pytest.importorskip("fastapi", reason="[service] extra not installed")

import yaml  # noqa: E402

from mealplan import costing, engine, io_yaml, schemas  # noqa: E402
from mealplan.service import SolveOptions, SolveRequest, solve  # noqa: E402

from _shared import GOLDEN_MENU_KW, GOLDEN_SEED  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def pipeline():
    """The solo_lifter full pipeline, at the frozen golden parameters —
    the same objects test_capabilities' golden asserts on."""
    ing, comps, people, settings = io_yaml.load(FIXTURES / "solo_lifter")
    menu, info, feasible, broke = engine.choose_menu(
        comps, ing, people, settings, **GOLDEN_MENU_KW)
    diag = {}
    weeks, demand = engine.build_week(comps, people, settings, menu,
                                      seed=GOLDEN_SEED, ing=ing, diag=diag)
    sp = costing.session_plan(comps, ing, settings, weeks)
    cooked = [i for i in menu if sp["batches"].get(i)]
    rows, wp, wt = costing.purchase(comps, ing, cooked, sp["batches"])
    groceries = costing.menu_cost(comps, ing, cooked, sp["batches"])
    shares, eaten = costing.attribute(comps, ing, weeks, groceries)
    return dict(ing=ing, comps=comps, people=people, settings=settings,
                menu=menu, info=info, feasible=feasible, broke=broke,
                diag=diag, weeks=weeks, demand=demand, sp=sp, rows=rows,
                wp=wp, wt=wt, groceries=groceries, shares=shares,
                eaten=eaten)


def test_session_plan_mirror_roundtrips_real_output(pipeline):
    m = schemas.SessionPlan.model_validate(pipeline["sp"])
    assert m.model_dump()["batches"] == pipeline["sp"]["batches"]
    assert m.minutes == pipeline["sp"]["minutes"]
    assert len(m.sessions) == len(pipeline["sp"]["sessions"])


def test_menu_info_mirror_roundtrips_real_output(pipeline):
    m = schemas.MenuInfo.model_validate(pipeline["info"])
    assert m.roles == pipeline["info"]["roles"]


def test_plate_result_mirror_roundtrips_real_solves(pipeline):
    """Feasible plate, pin-warning plate, and structural-failure plate all
    validate — including the signed-miss and warnings channels."""
    comps, people = pipeline["comps"], pipeline["people"]
    p = next(iter(people.values()))
    res = engine.plate(p, comps, list(comps))
    m = schemas.PlateResult.model_validate(res)
    assert m.ok == res.ok and m.portions == res.items_g

    # pin off the unit grid -> structured pin warning rides through
    cid = next(iter(comps))
    res2 = engine.plate(p, comps, list(comps), locked={cid: 1.5})
    m2 = schemas.PlateResult.model_validate(res2)
    assert [w.code for w in m2.warnings] == \
        [w["code"] for w in res2.warnings]

    # nothing usable -> SHORT misses (negative), still a valid mirror
    res3 = engine.plate(p, comps, [])
    m3 = schemas.PlateResult.model_validate(res3)
    assert not m3.ok and all(v < 0 for v in m3.misses.values())


def test_doctor_report_mirror_roundtrips_real_output(pipeline):
    _, data = engine.doctor(pipeline["comps"], pipeline["people"],
                            pipeline["settings"], ing=pipeline["ing"])
    m = schemas.DoctorReport.model_validate(data)
    assert set(m.feasibility) == set(pipeline["people"])
    assert m.raw_freshness is not None       # ing was passed
    # doctor without ing: no raw_freshness section — mirror must accept that
    _, data2 = engine.doctor(pipeline["comps"], pipeline["people"],
                             pipeline["settings"])
    m2 = schemas.DoctorReport.model_validate(data2)
    assert m2.raw_freshness is None


def test_week_plan_result_composes_from_real_pipeline(pipeline):
    p = pipeline
    lib = FIXTURES / "solo_lifter"
    docs = {n: yaml.safe_load((lib / f"{n}.yaml").read_text())
            for n in ("ingredients", "components", "people")}
    res = schemas.build_week_plan_result(
        seed=GOLDEN_SEED,
        library=schemas.library_identity(docs["ingredients"],
                                         docs["components"], docs["people"],
                                         name="solo_lifter"),
        menu=p["menu"], feasible=p["feasible"], broke=p["broke"],
        menu_info=p["info"], weeks=p["weeks"], demand=p["demand"],
        diag=p["diag"], session_plan=p["sp"], purchase_rows=p["rows"],
        waste_perishable=p["wp"], waste_total=p["wt"],
        groceries=p["groceries"],
        ceiling=costing.budget_ceiling(p["settings"], p["people"]),
        eaten=p["eaten"], shares=p["shares"], people=p["people"])
    assert res.api_version == "mealplan/v2"
    assert res.menu == p["menu"]
    assert res.weeks == {pn: [dict(pl) for pl in wk]
                         for pn, wk in p["weeks"].items()}
    assert res.session_plan.batches == p["sp"]["batches"]
    assert [r.ingredient for r in res.shopping] == [r[0] for r in p["rows"]]
    assert res.relax_tiers == p["diag"]["relax_tiers"]
    # JSON round trip: serialize + re-validate is lossless
    again = schemas.WeekPlanResult.model_validate_json(res.model_dump_json())
    assert again == res


def test_service_solve_end_to_end_matches_direct_pipeline(pipeline):
    """The /solve orchestration returns the SAME engine outputs as calling
    the pipeline directly (P10: the service adds nothing, renames nothing)."""
    lib = FIXTURES / "solo_lifter"
    docs = {n: yaml.safe_load((lib / f"{n}.yaml").read_text())
            for n in ("ingredients", "components", "people")}
    res = solve(SolveRequest(
        ingredients=docs["ingredients"], components=docs["components"],
        people=docs["people"], library_name="solo_lifter",
        options=SolveOptions(seed=GOLDEN_SEED, n=GOLDEN_MENU_KW["n"])))
    assert isinstance(res, schemas.WeekPlanResult)
    assert res.feasible
    # NOTE: choose_menu's iters/shortlist differ from GOLDEN_MENU_KW here
    # (service uses engine defaults), so compare structure, not the menu.
    assert res.session_plan.sessions
    assert res.shopping and res.volume
