"""Typed data model + derivation.

Two jobs:

1. Typed dataclasses (Ingredient, Component, Person, Settings, Budget) so the
   engine's shapes are named and documented instead of living as ad-hoc dicts.
2. Derivation: component per-100g macros are DERIVED from the ingredient list
   (never hand-entered) and component tags are the union of ingredient tags.
   The math is copied verbatim from plan.py's load().

Behavior preservation: the v1 engine code indexes everything dict-style
(``comps[i]["per100"]``, ``settings.get("cook_days", [0, 3])``, …). Each
dataclass therefore keeps the raw mapping it was built from and exposes
dict-style access that delegates to it, so the extracted engine code reads —
and behaves — identically to the prototype, including which keys are absent
(``.get`` defaults fire exactly when the YAML omitted the key, as before).
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from .units import KCAL, MACROS

# --------------------------------------------------------------------------- #
# M0.5 dead-config gate: every schema field must be consumed by engine/costing/
# cli code — or listed here as EXPLICITLY RESERVED. A reserved field is
# validated on load but ignored by the engine BY DESIGN; the registry test
# (tests/test_dead_config.py) fails CI on any field that is neither.
#
# - meals_per_day (Person): presentation-level meal structure for the M1 eat
#   sheets (PRD §8.1). The M0 engine plans whole days; splitting a day's
#   plate into meals is rendering, not solving.
RESERVED_FIELDS = frozenset({"meals_per_day"})


class _RawView:
    """Dict-style access shim delegating to the raw mapping the object was
    parsed from. Keeps the extracted engine code byte-identical in behavior to
    the prototype's plain-dict world."""

    raw: dict

    def __getitem__(self, key):
        return self.raw[key]

    def __setitem__(self, key, value):
        self.raw[key] = value
        if hasattr(type(self), "__dataclass_fields__") and key in type(self).__dataclass_fields__:
            object.__setattr__(self, key, value)

    def __contains__(self, key):
        return key in self.raw

    def __iter__(self):
        return iter(self.raw)

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def keys(self):
        return self.raw.keys()

    def items(self):
        return self.raw.items()

    def values(self):
        return self.raw.values()


@dataclass
class Ingredient(_RawView):
    """No stored kcal (M0.9): kcal derives from macros (Atwater 4/9/4), always.

    edible_fraction (M0.7): share of the gross weight that is edible (bone-in
    items). Portions and yield_g stay GROSS; macros apply to the edible share.
    negligible (M0.9): intentionally-zero macros (seasonings) — silences the
    all-zero-macros validation warning.
    freezable (M0.6): bought past-raw-keeps_days ingredients may be frozen on
    arrival (thaw note in the cook plan) instead of blocking the session.
    """

    id: str
    p: float
    f: float
    c: float
    perishable: bool
    pack_g: float
    keeps_days: int
    cost: float
    tags: list = field(default_factory=list)
    edible_fraction: float = 1.0
    negligible: bool = False
    freezable: bool = False
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, iid: str, d: dict) -> "Ingredient":
        return cls(id=iid, p=d.get("p"), f=d.get("f"),
                   c=d.get("c"), perishable=d.get("perishable"),
                   pack_g=d.get("pack_g"), keeps_days=d.get("keeps_days"),
                   cost=d.get("cost"), tags=d.get("tags") or [],
                   edible_fraction=d.get("edible_fraction", 1.0),
                   negligible=bool(d.get("negligible", False)),
                   freezable=bool(d.get("freezable", False)), raw=dict(d))


@dataclass
class Component(_RawView):
    id: str
    name: str
    cuisine: str
    role: str                       # main | starch | veg | accent | drink
    yield_g: float
    serve_g: dict                   # {"min": g, "max": g}
    keeps_days: int
    active_min: float
    ingredients: dict               # {ingredient_id: grams}
    unit_g: Optional[float] = None
    anchor: Optional[str] = None
    freezes: Optional[bool] = None  # LIVE (M0.5): freezer-bridging availability
    source: Optional[str] = None
    per100: dict = field(default_factory=dict)   # DERIVED — see derive_component
    tags: list = field(default_factory=list)     # DERIVED — union of ingredient tags
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Person(_RawView):
    """min_components_per_day / max_components_per_day were REMOVED in schema
    v1 (M0.5): no engine ever consumed them; validation now errors on them.

    meals_per_day is RESERVED (see RESERVED_FIELDS): validated int >= 1, but
    the M0 engine ignores it by design — it is the meal structure for the M1
    eat sheets (PRD §8.1), a presentation concern, not a solving one.
    """

    name: str
    targets: dict                   # daily grams: {"protein","fat","carb"}
    tolerance: float
    exclude: list = field(default_factory=list)
    dislikes: list = field(default_factory=list)
    max_daily_mass_g: Optional[float] = None
    meals_per_day: Optional[int] = None    # RESERVED — M1 eat sheets
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, pname: str, d: dict) -> "Person":
        return cls(name=pname, targets=d.get("targets"),
                   tolerance=d.get("tolerance"), exclude=d.get("exclude") or [],
                   dislikes=d.get("dislikes") or [],
                   max_daily_mass_g=d.get("max_daily_mass_g"),
                   meals_per_day=d.get("meals_per_day"),
                   raw=dict(d))


@dataclass
class Budget(_RawView):
    mode: str = "off"               # shared | per_person | by_consumption | off
    period: Optional[str] = None
    total: Optional[float] = None
    per_person: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, d: dict) -> "Budget":
        return cls(mode=d.get("mode", "off"), period=d.get("period"),
                   total=d.get("total"), per_person=d.get("per_person") or {},
                   raw=dict(d))


@dataclass
class Pantry(_RawView):
    """On-hand stock (M0.12, PRD §8.1).

    - stock: raw ingredient grams on hand — deducted from purchase need
      BEFORE pack rounding (costing.purchase).
    - cooked: cooked-component leftovers — schema and validation only in M0;
      planning integration (availability join) is M1+.

    An empty pantry (empty/absent lists) behaves identically to no pantry.
    """

    stock: list = field(default_factory=list)    # [{ingredient, grams, acquired}]
    cooked: list = field(default_factory=list)   # [{component, grams, cooked}]
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, d: dict) -> "Pantry":
        return cls(stock=list(d.get("stock") or []),
                   cooked=list(d.get("cooked") or []), raw=dict(d))

    def stock_g(self) -> dict:
        """Total on-hand grams per ingredient, summed across entries."""
        out = {}
        for r in self.stock:
            out[r["ingredient"]] = out.get(r["ingredient"], 0) + r["grams"]
        return out


@dataclass
class Settings(_RawView):
    days: Optional[int] = None
    active_min_budget: Optional[float] = None
    batch_time_factor: Optional[float] = None
    max_days_same_component: Optional[int] = None
    min_lean_anchors: Optional[int] = None
    cook_days: Optional[list] = None
    shop_days: Optional[list] = None      # M0.6: shopping trips are data
    max_batches_per_component: Optional[int] = None
    use_freezer: Optional[bool] = None    # M0.5: freezer bridging, default true
    budget: Any = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, d: dict, budget: Any) -> "Settings":
        raw = dict(d)
        raw["budget"] = budget
        if d.get("shop_days"):
            # canonical form everywhere downstream: sorted, deduped
            raw["shop_days"] = sorted(set(d["shop_days"]))
        return cls(days=d.get("days"), active_min_budget=d.get("active_min_budget"),
                   batch_time_factor=d.get("batch_time_factor"),
                   max_days_same_component=d.get("max_days_same_component"),
                   min_lean_anchors=d.get("min_lean_anchors"),
                   cook_days=d.get("cook_days"),
                   shop_days=raw.get("shop_days"),
                   max_batches_per_component=d.get("max_batches_per_component"),
                   use_freezer=d.get("use_freezer"),
                   budget=budget, raw=raw)


def derive_component(c: dict, ing: dict) -> Component:
    """Build a Component from its raw YAML entry, deriving per100 macros and
    tags from the ingredient list: macros are DERIVED from the ingredient list,
    never hand-entered.

    M0.7: each ingredient's macro contribution is scaled by its edible_fraction
    (bone-in items); yield_g — and therefore portion weights — remain GROSS.
    M0.9: kcal is never read from an ingredient; per100 kcal is Atwater 4/9/4
    over the derived per100 macros.
    """
    tot = {"protein": 0.0, "fat": 0.0, "carb": 0.0}
    tags = set()
    for name, grams in c["ingredients"].items():
        i = ing[name]
        ef = i.get("edible_fraction", 1.0)
        tot["protein"] += i["p"] * ef * grams / 100
        tot["fat"] += i["f"] * ef * grams / 100
        tot["carb"] += i["c"] * ef * grams / 100
        tags |= set(i.get("tags") or [])
    y = c["yield_g"]
    raw = dict(c)
    per100 = {k: round(v * 100 / y, 3) for k, v in tot.items()}
    per100 = {"kcal": round(sum(per100[m] * KCAL[m] for m in MACROS), 3),
              **per100}
    raw["per100"] = per100
    raw["tags"] = sorted(tags)
    return Component(
        id=c["id"], name=c.get("name"), cuisine=c.get("cuisine"),
        role=c.get("role"), yield_g=c.get("yield_g"), serve_g=c.get("serve_g"),
        keeps_days=c.get("keeps_days"), active_min=c.get("active_min"),
        ingredients=c.get("ingredients"), unit_g=c.get("unit_g"),
        anchor=c.get("anchor"),
        freezes=c.get("freezes"), source=c.get("source"),
        per100=raw["per100"], tags=raw["tags"], raw=raw)
