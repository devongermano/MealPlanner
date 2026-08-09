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
    id: str
    kcal: float
    p: float
    f: float
    c: float
    perishable: bool
    pack_g: float
    keeps_days: int
    cost: float
    tags: list = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, iid: str, d: dict) -> "Ingredient":
        return cls(id=iid, kcal=d.get("kcal"), p=d.get("p"), f=d.get("f"),
                   c=d.get("c"), perishable=d.get("perishable"),
                   pack_g=d.get("pack_g"), keeps_days=d.get("keeps_days"),
                   cost=d.get("cost"), tags=d.get("tags") or [], raw=dict(d))


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
    batch_g: Optional[float] = None
    anchor: Optional[str] = None
    freezes: Optional[bool] = None
    source: Optional[str] = None
    per100: dict = field(default_factory=dict)   # DERIVED — see derive_component
    tags: list = field(default_factory=list)     # DERIVED — union of ingredient tags
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Person(_RawView):
    name: str
    targets: dict                   # daily grams: {"protein","fat","carb"}
    tolerance: float
    exclude: list = field(default_factory=list)
    dislikes: list = field(default_factory=list)
    max_daily_mass_g: Optional[float] = None
    meals_per_day: Optional[int] = None
    min_components_per_day: Optional[int] = None
    max_components_per_day: Optional[int] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, pname: str, d: dict) -> "Person":
        return cls(name=pname, targets=d.get("targets"),
                   tolerance=d.get("tolerance"), exclude=d.get("exclude") or [],
                   dislikes=d.get("dislikes") or [],
                   max_daily_mass_g=d.get("max_daily_mass_g"),
                   meals_per_day=d.get("meals_per_day"),
                   min_components_per_day=d.get("min_components_per_day"),
                   max_components_per_day=d.get("max_components_per_day"),
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
class Settings(_RawView):
    days: Optional[int] = None
    active_min_budget: Optional[float] = None
    batch_time_factor: Optional[float] = None
    max_days_same_component: Optional[int] = None
    min_lean_anchors: Optional[int] = None
    cook_days: Optional[list] = None
    max_batches_per_component: Optional[int] = None
    budget: Any = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, d: dict, budget: Any) -> "Settings":
        raw = dict(d)
        raw["budget"] = budget
        return cls(days=d.get("days"), active_min_budget=d.get("active_min_budget"),
                   batch_time_factor=d.get("batch_time_factor"),
                   max_days_same_component=d.get("max_days_same_component"),
                   min_lean_anchors=d.get("min_lean_anchors"),
                   cook_days=d.get("cook_days"),
                   max_batches_per_component=d.get("max_batches_per_component"),
                   budget=budget, raw=raw)


def derive_component(c: dict, ing: dict) -> Component:
    """Build a Component from its raw YAML entry, deriving per100 macros and
    tags from the ingredient list. Math copied verbatim from plan.py load():
    macros are DERIVED from the ingredient list, never hand-entered."""
    tot = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carb": 0.0}
    tags = set()
    for name, grams in c["ingredients"].items():
        i = ing[name]
        tot["kcal"] += i["kcal"] * grams / 100
        tot["protein"] += i["p"] * grams / 100
        tot["fat"] += i["f"] * grams / 100
        tot["carb"] += i["c"] * grams / 100
        tags |= set(i.get("tags") or [])
    y = c["yield_g"]
    raw = dict(c)
    # macros are DERIVED from the ingredient list. never hand-entered.
    raw["per100"] = {k: round(v * 100 / y, 3) for k, v in tot.items()}
    raw["tags"] = sorted(tags)
    return Component(
        id=c["id"], name=c.get("name"), cuisine=c.get("cuisine"),
        role=c.get("role"), yield_g=c.get("yield_g"), serve_g=c.get("serve_g"),
        keeps_days=c.get("keeps_days"), active_min=c.get("active_min"),
        ingredients=c.get("ingredients"), unit_g=c.get("unit_g"),
        batch_g=c.get("batch_g"), anchor=c.get("anchor"),
        freezes=c.get("freezes"), source=c.get("source"),
        per100=raw["per100"], tags=raw["tags"], raw=raw)
