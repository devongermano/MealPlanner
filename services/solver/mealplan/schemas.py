"""Pydantic v2 mirrors of the engine's REAL result objects (Track B spike).

These models are STRUCTURAL MIRRORS of what engine.py / costing.py / io_yaml.py
actually return — never an aspiration of what they should return. The law of
this module (ARCHITECTURE.md "the one non-negotiable wiring rule"): when a
mirror and the engine disagree, THE SCHEMA IS WRONG. Fix the schema; never
bend the engine to a schema. tests/test_contracts_roundtrip.py enforces this
by running the full pipeline on the solo_lifter fixture and model_validating
the real outputs.

Why they exist: the solver service (service.py) emits these through FastAPI's
OpenAPI document, and packages/contracts codegens the TypeScript mirror from
that document. Nobody hand-writes the TS mirror of engine types. Ever.

Modeling rules:
- ``extra="forbid"`` everywhere (except StructuredWarning, whose payload keys
  vary by code on purpose): a new field the engine starts emitting FAILS the
  round-trip test, forcing a deliberate schema + codegen + consumers PR —
  drift can never be silent.
- Engine-native shapes that are not JSON-object-shaped (purchase's 7-tuples,
  PlateResult's tuple subclass) get a ``mode="before"`` validator so the raw
  engine object validates directly; the positional mapping lives HERE, in the
  contract layer, in exactly one place.
- ``build_week_plan_result`` is the ONE composer of the /solve response from
  raw pipeline outputs (name-and-map only, no solver math); the service and
  the round-trip test both use it, so they cannot diverge.

This module imports nothing from engine/costing when imported and is only
importable when the ``service`` extra (pydantic) is installed; the core engine
never depends on it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

API_VERSION = "mealplan/v2"


class _Strict(BaseModel):
    """Base: unknown fields are a contract violation, not noise."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
#  structured warnings (M0.8/M0.13/M1.8 — engine emits dicts w/ code+message)
# --------------------------------------------------------------------------- #
class StructuredWarning(BaseModel):
    """One structured engine warning. ``code`` + ``message`` are universal;
    every other key is code-specific payload (pin_adjusted carries bounds,
    stock_expiring_unused carries grams/ages, …) — mirrored open, on purpose.
    """

    model_config = ConfigDict(extra="allow")

    code: str
    message: str


# --------------------------------------------------------------------------- #
#  io_yaml.ValidationIssue (the all-errors load/save report)
# --------------------------------------------------------------------------- #
class ValidationIssue(_Strict):
    """Mirror of io_yaml.ValidationIssue: one problem, precisely located."""

    code: str
    where: str
    message: str
    severity: Literal["error", "warning"] = "error"


class ValidationErrorResponse(_Strict):
    """422 body: EVERY issue found (io_yaml.ValidationError), never the first."""

    detail: str = "library failed validation"
    issues: list[ValidationIssue]


# --------------------------------------------------------------------------- #
#  engine.PlateResult
# --------------------------------------------------------------------------- #
class PlateResult(_Strict):
    """Mirror of engine.PlateResult — (ok, {id: grams}, {macro: signed_miss})
    plus ``.warnings``. Misses are signed: positive = forced OVER target,
    negative = cannot REACH target (M1.0)."""

    ok: bool
    portions: dict[str, int]
    misses: dict[str, float]
    warnings: list[StructuredWarning] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_engine_tuple(cls, v: Any) -> Any:
        # engine.PlateResult is a 3-tuple subclass carrying .warnings
        if isinstance(v, tuple) and len(v) == 3:
            ok, portions, miss = v
            return dict(ok=bool(ok), portions=dict(portions),
                        misses=dict(miss),
                        warnings=list(getattr(v, "warnings", []) or []))
        return v


# --------------------------------------------------------------------------- #
#  engine.doctor structured data (M0.11, PRD §8.3)
# --------------------------------------------------------------------------- #
class RawBlocker(_Strict):
    """One perishable, non-freezable ingredient past raw keeps_days at cook
    time (costing.raw_freshness blocker entry)."""

    ingredient: str
    shop_day: Optional[int]     # None: no shopping trip before the session
    keeps_days: int


class RawFreshnessIssue(_Strict):
    """doctor: (component, session) pair the raw-freshness rule blocks."""

    component: str
    session: int
    day: int
    blockers: list[RawBlocker]


class FeasibilityEntry(_Strict):
    """doctor feasibility, per person."""

    ok: bool
    miss: dict[str, float]
    eligible: int
    total: int
    blocked: list[str]
    clears_at_tolerance: Optional[float]


class BindingMacro(_Strict):
    """engine.binding_macro: WHICH macro binds for a person on this library."""

    macro: str
    signed_miss_g: float
    direction: Literal["over", "short"]
    at_tolerance: float
    misses: dict[str, float]


class VolumeFloorBinding(_Strict):
    macro: str
    signed_miss_g: float
    direction: Literal["over", "short"]


class VolumeFloor(_Strict):
    """engine.volume_floor: minimum daily food mass the targets require."""

    floor_g: Optional[int]      # None: infeasible even at the search top
    binding: Optional[VolumeFloorBinding]
    searched: list[int]         # [lo, hi] search range
    note: Optional[str]


class AblationEntry(_Strict):
    """doctor structural ablation, per person: how many lean mains the week
    actually needs (None = stays feasible no matter which mains are cut)."""

    min_lean_mains: Optional[int]
    miss: dict[str, float]


class StructuralCheck(_Strict):
    lean_anchors: list[str]
    ablation: dict[str, AblationEntry]
    dense_carriers: int


class LeanCoverage(_Strict):
    """engine.lean_coverage: the REAL shelf-life stagger check."""

    lean_anchors: list[str]
    per_day: dict[int, list[str]]
    uncovered_days: list[int]


class CarbHeadroomDay(_Strict):
    day: int
    headroom_g: float


class CarbHeadroom(_Strict):
    """engine.carb_headroom: per-day carb availability vs target, per person."""

    target_g: float
    per_day: list[CarbHeadroomDay]
    worst_day: Optional[int]
    worst_headroom_g: float
    ok: bool


class DoctorReport(_Strict):
    """Mirror of engine.doctor's structured ``data`` (the second element of
    its (text, data) return). ``raw_freshness`` is present only when doctor
    ran with ingredient data (``ing`` passed)."""

    raw_freshness: Optional[list[RawFreshnessIssue]] = None
    feasibility: dict[str, FeasibilityEntry]
    binding_macro: dict[str, Optional[BindingMacro]]
    volume_floor: dict[str, VolumeFloor]
    structural: StructuralCheck
    lean_coverage: LeanCoverage
    carb_headroom: dict[str, CarbHeadroom]


# --------------------------------------------------------------------------- #
#  costing.session_plan (M0.4 — THE canonical session attribution, P10)
# --------------------------------------------------------------------------- #
class ThawNote(_Strict):
    """Frozen-on-arrival ingredient to thaw before a session (M0.6)."""

    component: str
    ingredient: str
    shop_day: int
    session: int
    note: str


class FreezerServe(_Strict):
    """Demand fed from the freezer bridge — thaw ahead (M0.5)."""

    component: str
    day: int
    grams: float
    session: int
    note: str


class LeftoverServe(_Strict):
    """Demand fed from cooked pantry leftovers — already paid for (M1.8)."""

    component: str
    day: int
    grams: float
    note: str


class UnattributedDemand(_Strict):
    """Demand no cook session can feed — reported, never invented."""

    component: str
    day: int
    grams: float


class CookSession(_Strict):
    index: int
    start: int                      # cook day (0-indexed)
    demand_g: dict[str, float]
    batches: dict[str, int]
    made_g: dict[str, float]
    minutes: int
    thaw_notes: list[ThawNote]
    freezer_notes: list[FreezerServe]


class SessionPlan(_Strict):
    """Mirror of costing.session_plan's return dict."""

    sessions: list[CookSession]
    batches: dict[str, int]         # summed per-session batches (feeds purchase)
    minutes: int
    unattributed: list[UnattributedDemand]
    freezer: list[FreezerServe]
    leftover: list[LeftoverServe]


# --------------------------------------------------------------------------- #
#  costing.purchase rows (shopping list)
# --------------------------------------------------------------------------- #
class ShoppingRow(_Strict):
    """Mirror of one costing.purchase row — engine-side it is the 7-tuple
    ``(name, need_g, units, pack_g, leftover_g, perishable, keeps_days)``;
    the positional mapping lives here and nowhere else."""

    ingredient: str
    need_g: int
    units: int
    pack_g: float
    leftover_g: int
    perishable: bool
    keeps_days: int

    @model_validator(mode="before")
    @classmethod
    def _from_purchase_tuple(cls, v: Any) -> Any:
        if isinstance(v, (tuple, list)) and len(v) == 7:
            name, need, units, pack, left, per, keeps = v
            return dict(ingredient=name, need_g=need, units=units,
                        pack_g=pack, leftover_g=left, perishable=bool(per),
                        keeps_days=keeps)
        return v


# --------------------------------------------------------------------------- #
#  week-plan blocks
# --------------------------------------------------------------------------- #
class MenuInfo(_Strict):
    """Mirror of score_menu's info dict (returned through choose_menu)."""

    waste_perishable: float
    active_min: float
    cuisines: int
    roles: dict[str, int]


class CostBlock(_Strict):
    """costing.menu_cost + attribute + budget_ceiling, composed."""

    groceries: float                    # what the shop costs (pack-rounded)
    ceiling: Optional[float]            # budget ceiling, None = no ceiling
    eaten_value: dict[str, float]       # per person: cost of food actually eaten
    shares: dict[str, float]            # per person: share of the grocery bill


class VolumeBlock(_Strict):
    """Per-person daily food mass over the week's non-empty days."""

    avg_g: float
    min_g: float
    max_g: float
    cap_g: Optional[float]              # person's max_daily_mass_g, if any


class LibraryIdentity(_Strict):
    """Which library produced this plan: a canonical-JSON sha256 over the
    three inline documents plus human-oriented counts. Reproducibility is
    seed + library identity (PRD §8.3 determinism)."""

    name: Optional[str] = None
    digest_sha256: str
    n_ingredients: int
    n_components: int
    people: list[str]


class WeekPlanResult(_Strict):
    """The /solve response: the full pipeline's outputs, verbatim (P10 — the
    engine is the sole producer; this model only names what it returned)."""

    api_version: Literal["mealplan/v2"] = API_VERSION
    seed: int
    library: LibraryIdentity
    menu: list[str]
    feasible: bool
    broke: dict[str, dict[str, float]]          # per person: macro misses
    menu_info: MenuInfo
    weeks: dict[str, list[dict[str, int]]]      # person -> per-day portions (g)
    demand: dict[str, float]                    # component -> total grams
    relax_tiers: dict[str, list[Optional[int]]]  # P8: never-silent relaxations
    session_plan: SessionPlan
    shopping: list[ShoppingRow]
    waste_perishable_g: float
    waste_total_g: float
    cost: CostBlock
    volume: dict[str, VolumeBlock]


# --------------------------------------------------------------------------- #
#  composers — name-and-map only, ZERO solver logic
# --------------------------------------------------------------------------- #
def library_identity(ingredients_doc: dict, components_doc: dict,
                     people_doc: dict, name: Optional[str] = None
                     ) -> LibraryIdentity:
    """Identity of an inline library: sha256 over the canonical JSON of the
    three documents (sorted keys, dates stringified) + counts."""
    canon = json.dumps(
        {"ingredients": ingredients_doc, "components": components_doc,
         "people": people_doc},
        sort_keys=True, default=str, ensure_ascii=False)
    return LibraryIdentity(
        name=name,
        digest_sha256=hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        n_ingredients=len(ingredients_doc.get("ingredients") or {}),
        n_components=len(components_doc.get("components") or []),
        people=sorted((people_doc.get("people") or {}).keys()))


def build_week_plan_result(*, seed: int, library: LibraryIdentity,
                           menu: list, feasible: bool, broke: dict,
                           menu_info: dict, weeks: dict, demand: dict,
                           diag: dict, session_plan: dict,
                           purchase_rows: list, waste_perishable: float,
                           waste_total: float, groceries: float,
                           ceiling: Optional[float], eaten: dict,
                           shares: dict, people: dict) -> WeekPlanResult:
    """Compose the /solve response from RAW pipeline outputs. Every argument
    is exactly what the corresponding engine/costing call returned; the only
    derivation here is the per-person volume summary (sum/min/max over the
    week's non-empty plates — arithmetic on served grams, mirroring the CLI
    report's volume table, no solver math)."""
    volume = {}
    for pname, wk in weeks.items():
        masses = [sum(pl.values()) for pl in wk if pl]
        if not masses:
            continue                     # all-empty week: no volume row (CLI parity)
        volume[pname] = VolumeBlock(
            avg_g=round(sum(masses) / len(masses), 1),
            min_g=min(masses), max_g=max(masses),
            cap_g=people[pname].get("max_daily_mass_g"))
    return WeekPlanResult(
        seed=seed, library=library, menu=list(menu), feasible=bool(feasible),
        broke={p: dict(m) for p, m in (broke or {}).items()},
        menu_info=menu_info, weeks=weeks, demand=demand,
        relax_tiers=dict((diag or {}).get("relax_tiers") or {}),
        session_plan=session_plan, shopping=purchase_rows,
        waste_perishable_g=waste_perishable, waste_total_g=waste_total,
        cost=CostBlock(groceries=groceries, ceiling=ceiling,
                       eaten_value=eaten, shares=shares),
        volume=volume)
