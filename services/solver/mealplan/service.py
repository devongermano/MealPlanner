"""SPIKE — M2 solver-service skeleton (Track B contract-codegen spike).

THIS IS NOT THE PRODUCTION SERVICE. It exists to prove the type flow end to
end (ARCHITECTURE.md "the one non-negotiable wiring rule"): pydantic result
mirrors (schemas.py) → this FastAPI app's OpenAPI document → codegen'd TS in
packages/contracts. Deliberately absent, to be built at real M2:
- no auth, no accounts, no per-household isolation
- no config surface, no deployment story
- no solve lock / queueing (PRD §8.4: solves serialize behind a lock —
  the API's problem; this spike solves synchronously in-request)
- no pantry/plan-date support in /solve (structurally covered by the
  session_plan.leftover mirror; wiring is an M2 task). The plan date is
  ALSO the M1.11 day-type anchor (M111_SPEC §3: anchor =
  plan_date.weekday()), so until it is wired a library whose people
  author `target_profiles` is rejected with a structured 422
  `date_required` — the same reason the CLI raises, never a 500.

What it DOES honor already:
- thin orchestration only (P10): every computation below is a call into the
  engine/costing package; this file names results, it never derives them.
- inline library documents in, engine result object out, all-errors
  validation (io_yaml) reported as a structured 422.

Run: uvicorn mealplan.service:app  (requires the ``service`` extra).
"""

import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import io_yaml
from .costing import (attribute, budget_ceiling, menu_cost, purchase,
                      session_plan)
from .engine import build_week, choose_menu, plate, score_menu
from .schemas import (API_VERSION, ValidationErrorResponse, WeekPlanResult,
                      build_week_plan_result, library_identity)


class SolveOptions(BaseModel):
    """Options mirroring the CLI's knobs (subset — the spike's scope)."""

    model_config = ConfigDict(extra="forbid")

    seed: int = 0
    n: int = 12                          # menu size for choose_menu
    menu: Optional[list[str]] = None     # explicit menu instead of search
    must: list[str] = Field(default_factory=list)   # forced components


class SolveRequest(BaseModel):
    """Inline library documents (the YAML interchange documents, as JSON) +
    options. Validation happens through the engine's own io_yaml validators —
    the service adds none of its own rules."""

    model_config = ConfigDict(extra="forbid")

    ingredients: dict[str, Any]          # ingredients.yaml document
    components: dict[str, Any]           # components.yaml document
    people: dict[str, Any]               # people.yaml document (+settings/budget)
    library_name: Optional[str] = None
    options: SolveOptions = Field(default_factory=SolveOptions)


class Healthz(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    api_version: str


app = FastAPI(
    title="mealplan solver service",
    version="0.1.0-spike",
    description=("SPIKE of the M2 solver service — contract-codegen source "
                 "of truth only. Stateless, dumb, no auth (see module "
                 "docstring). Engine result objects in/out, verbatim (P10)."))


@app.get("/healthz", response_model=Healthz)
def healthz() -> Healthz:
    return Healthz(ok=True, api_version=API_VERSION)


def _load_inline(req: SolveRequest):
    """Feed the inline documents through the ONE load path (io_yaml.load) by
    materializing them as a temp library directory — zero duplicated
    validation/derivation logic, identical semantics to the CLI."""
    with tempfile.TemporaryDirectory() as td:
        for fname, doc in (("ingredients.yaml", req.ingredients),
                           ("components.yaml", req.components),
                           ("people.yaml", req.people)):
            Path(td, fname).write_text(yaml.safe_dump(doc))
        return io_yaml.load(td)


@app.post("/solve", response_model=WeekPlanResult,
          responses={422: {"model": ValidationErrorResponse}})
def solve(req: SolveRequest) -> WeekPlanResult:
    """Validated library in → WeekPlanResult out. Thin orchestration of the
    exact pipeline the CLI runs: load → choose_menu (or explicit menu) →
    build_week → session_plan → purchase/cost. No solver logic lives here."""
    try:
        ing, comps, people, settings = _load_inline(req)
    except io_yaml.ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=ValidationErrorResponse(
                issues=[dict(code=i.code, where=i.where, message=i.message,
                             severity=i.severity) for i in e.issues]
            ).model_dump())

    # M1.11: /solve carries no plan date, so there is no anchor to resolve
    # day-types against (M111_SPEC §3) — and `build_week` would raise the
    # engine-side ValueError guard, which FastAPI turns into a 500 on
    # input that is perfectly VALID YAML. Mirror the CLI's `date_required`
    # instead: a structured 422, all-errors (one issue per profiled
    # person), same as every other bad input on this endpoint. The real
    # wiring (a plan_date on SolveOptions, threaded to the anchor) is the
    # M2 task named in the module docstring.
    profiled = sorted(pn for pn, p in people.items()
                      if p.get("target_profiles"))
    if profiled:
        raise HTTPException(
            status_code=422,
            detail=ValidationErrorResponse(
                detail="target_profiles need a plan date this spike cannot "
                       "take",
                issues=[dict(
                    code="date_required",
                    # io_yaml's `where` shape verbatim (_load_inline
                    # materializes the inline doc as people.yaml), so an
                    # API consumer parses one convention, not two
                    where=(f"people.yaml: person '{pn}', "
                           "field 'target_profiles'"),
                    message="day-type cycling is anchored to the plan "
                            "start date's weekday, and /solve takes no "
                            "plan date yet (M2 wiring). Use the CLI "
                            "(`mealplan week --date YYYY-MM-DD`) or drop "
                            "target_profiles/week from this person",
                    severity="error") for pn in profiled]
            ).model_dump())

    opts = req.options
    if opts.menu is not None:
        menu = list(opts.menu)
        unknown = [x for x in menu if x not in comps]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=ValidationErrorResponse(
                    detail="unknown components in explicit menu",
                    issues=[dict(code="unknown_component",
                                 where=f"options.menu: '{x}'",
                                 message=f"unknown component '{x}'",
                                 severity="error") for x in unknown]
                ).model_dump())
        _, menu_info = score_menu(comps, ing, menu, settings)
        # feasibility of an explicit menu: the same per-person plate check
        # choose_menu's verify phase runs (engine calls only)
        broke = {}
        for pname, p in people.items():
            ok, _, miss = plate(p, comps, menu)
            if not ok:
                broke[pname] = miss
        feasible = not broke
    else:
        menu, menu_info, feasible, broke = choose_menu(
            comps, ing, people, settings, n=opts.n, seed=opts.seed,
            must=[m for m in opts.must if m in comps])

    diag: dict = {}
    weeks, demand = build_week(comps, people, settings, menu,
                               seed=opts.seed, ing=ing, diag=diag)
    sp = session_plan(comps, ing, settings, weeks)
    cooked = [i for i in menu if sp["batches"].get(i)]
    rows, waste_perishable, waste_total = purchase(comps, ing, cooked,
                                                   sp["batches"])
    groceries = menu_cost(comps, ing, cooked, sp["batches"])
    shares, eaten = attribute(comps, ing, weeks, groceries)

    return build_week_plan_result(
        seed=opts.seed,
        library=library_identity(req.ingredients, req.components, req.people,
                                 name=req.library_name),
        menu=menu, feasible=feasible, broke=broke, menu_info=menu_info,
        weeks=weeks, demand=demand, diag=diag, session_plan=sp,
        purchase_rows=rows, waste_perishable=waste_perishable,
        waste_total=waste_total, groceries=groceries,
        ceiling=budget_ceiling(settings, people), eaten=eaten, shares=shares,
        people=people)
