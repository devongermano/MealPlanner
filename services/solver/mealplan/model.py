"""Typed data model + derivation.

Two jobs:

1. Typed dataclasses (Ingredient, Component, Person, Settings, Budget) so the
   engine's shapes are named and documented instead of living as ad-hoc dicts.
2. Derivation: component per-100g macros are DERIVED from the ingredient list
   (never hand-entered) and component tags are the union of ingredient tags.
   The math is copied verbatim from plan.py's load().

Behavior preservation: the v1 engine code indexes everything dict-style
(``comps[i]["per100"]``, ``settings["cook_days"]``, …). Each dataclass
therefore keeps the raw mapping it was built from and exposes dict-style
access that delegates to it, so the extracted engine code reads — and
behaves — identically to the prototype.

Settings defaults (M0.17): schema defaults live HERE, in the model layer
(``SETTINGS_DEFAULTS``, applied once in ``Settings.from_raw``) — the engine
and costing read validated settings by plain indexing and carry no inline
``.get(..., default)`` fallbacks. ``cook_days`` has NO default: it is
required (validated in io_yaml) — the prototype's silent ``[0, 3]`` code
fallback is gone.
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
# - period (Budget): budget period label, validated to the one known value
#   ('week') — M0/M1 plans are weekly by construction, so nothing consumes
#   it; reserved for a future non-weekly budget period.
# (cooked (Pantry) was reserved in M0; it went LIVE in M1.8 —
#  costing.cooked_leftovers joins it into availability and session_plan.
#  meals_per_day (Person) was reserved in M0; it went LIVE in M1.9 — the
#  post-solve meal dealer (meals.deal_day, PRD §4.0) deals each solved day
#  plate into n composed meals.)
RESERVED_FIELDS = frozenset({"period"})

# M1.2 (PRD §4.1): a relaxed person with NO explicit tolerance gets this
# effective tolerance. PROVISIONAL — ±12% is the PRD §4.1 default, to be
# ratified against the M1.6 real week. An explicit tolerance always wins and
# is still capped at 0.5 by validation (io_yaml).
RELAXED_TOLERANCE = 0.12

PERSON_MODES = ("precision", "relaxed")

# M1.9 (PRD §4.0 + amendments): per-person / per-slot serving models.
# "portioned" is the meal-prep model (per-meal containers packed on cook
# day) — the product's stated model, so it is the default. PROVISIONAL
# (P9, M19_SPEC §11.3): family_style is the named v1-heritage mode; the
# default is an owner call to ratify. Both models share the entire solve —
# they change rendering only (PRD §4.0: grams are canonical, P7).
SERVING_MODELS = ("portioned", "family_style")

# M1.10 (PRD §4.0 amendment): cook-plan style is a preference. "recipe"
# renders classic per-dish blocks from the compiled session; "timeline"
# (M1.12) is the interleaved optimized stream — the greedy scheduler
# (schedule.py) runs ONLY for timeline households. Both are views of the
# same compiled session.
COOK_PLAN_STYLES = ("recipe", "timeline")

# M1.13 (dish layer, M113_SPEC §9): orphan-side policy during the steward's
# reconstruction window. "permissive" (default until M1.6, P9): starch/veg
# referenced by no dish's compatible_sides may serve as sides of any dish,
# flagged orphan_side. "strict": unlisted sides are unservable. The
# documented migration ratchet is flipping the default to strict once
# compatible_sides coverage lands.
DISH_LAYER_MODES = ("permissive", "strict")

# M1.13 (M113_SPEC §2): dishes.yaml reconstruction provenance — the honesty
# field that tells the reviewer how hard to look (data steward's schema).
DISH_RECONSTRUCTIONS = ("from_source", "inferred", "invented")

# M1.11 (PRD §5.2): weekday keys for per-person target-profile week maps —
# ISO order, Monday first, matching datetime.date.weekday() == 0. The week
# map is keyed by CALENDAR weekday (start-day invariant), never by plan-day
# position; plan day d has weekday DAY_KEYS[(anchor + d) % 7] where
# ``anchor = plan_date.weekday()`` is derived ONCE at the CLI boundary —
# dates come IN as data, the engine reads no wall clock.
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# M1.12: the household station inventory the timeline scheduler respects.
# PROVISIONAL defaults (P9) — a typical apartment kitchen; override any
# subset in settings.stations. `prep` is simultaneous prep workspaces
# (cutting boards that can host a task at once), NOT people — the cook is
# a separate unary resource (one pair of hands, always).
STATIONS_DEFAULTS = {"burners": 4, "oven_slots": 2, "grill": False,
                     "prep": 1}


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
    household_unit: Optional[dict] = None   # M1.2: {"name": str, "grams": >0}
    anchor: Optional[str] = None
    freezes: Optional[bool] = None  # LIVE (M0.5): freezer-bridging availability
    pairs_with: Optional[list] = None  # M1.9: accent affinity (component ids)
    source: Optional[str] = None
    per100: dict = field(default_factory=dict)   # DERIVED — see derive_component
    tags: list = field(default_factory=list)     # DERIVED — union of ingredient tags
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Person(_RawView):
    """min_components_per_day / max_components_per_day were REMOVED in schema
    v1 (M0.5): no engine ever consumed them; validation now errors on them.

    meals_per_day is LIVE since M1.9 (PRD §4.0): each person's solved day
    plate is dealt into n composed meals by the post-solve dealer
    (meals.deal_day). Validated int >= 1; unset means no meal layer for
    this person (the layer is inert).

    serving_model / meal_slots (M1.9, PRD §4.0 amendments): the serving
    model is per person with per-slot overrides — slots inherit the
    person's serving_model unless they set their own. ``interchangeable``
    (per slot) is a named OPT-IN, never a default (Amendment 2). Slot
    resolution — the schema-defaults job — is ``resolve_meal_slots``
    below, the ONE resolution point every consumer calls.

    mode (M1.2, PRD §4.1): "precision" (default) or "relaxed". Presentation
    + tolerance-default ONLY — the engine still solves grams either way. A
    relaxed person with no explicit tolerance gets RELAXED_TOLERANCE; the
    default is applied HERE (the one schema-defaults layer) so the engine
    keeps reading person["tolerance"] by plain indexing.

    target_profiles / week (M1.11, PRD §5.2): named day-type macro maps +
    a partial CALENDAR-weekday map (``mon..sun`` → profile name). A weekday
    absent from ``week`` resolves to ``targets`` — the base day. The two
    come together or not at all (validated in io_yaml); ``targets`` stays
    REQUIRED for everyone (flat daily grams remain valid shorthand). THE
    canonical per-day resolution is ``resolve_targets`` below (P10) — no
    other code may do weekday math on these fields.
    """

    name: str
    targets: dict                   # daily grams: {"protein","fat","carb"}
    tolerance: float
    mode: str = "precision"         # M1.2: precision | relaxed (PRD §4.1)
    exclude: list = field(default_factory=list)
    dislikes: list = field(default_factory=list)
    max_daily_mass_g: Optional[float] = None
    meals_per_day: Optional[int] = None    # LIVE (M1.9): meal-layer n
    serving_model: str = "portioned"       # LIVE (M1.9): rendering model
    meal_slots: Optional[list] = None      # LIVE (M1.9): per-slot config
    max_dishes_per_slot: Optional[int] = None  # LIVE (M1.13): big-eater
    #                       ladder rung 3 — explicit opt-in, default 1,
    #                       per person with per-slot overrides (M113_SPEC §7)
    target_profiles: Optional[dict] = None  # LIVE (M1.11): day-type macros
    week: Optional[dict] = None             # LIVE (M1.11): weekday → profile
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, pname: str, d: dict) -> "Person":
        raw = dict(d)
        mode = raw.get("mode") or "precision"
        raw["mode"] = mode
        if raw.get("tolerance") is None and mode == "relaxed":
            raw["tolerance"] = RELAXED_TOLERANCE
        sm = raw.get("serving_model") or "portioned"
        raw["serving_model"] = sm
        return cls(name=pname, targets=raw.get("targets"),
                   tolerance=raw.get("tolerance"), mode=mode,
                   exclude=raw.get("exclude") or [],
                   dislikes=raw.get("dislikes") or [],
                   max_daily_mass_g=raw.get("max_daily_mass_g"),
                   meals_per_day=raw.get("meals_per_day"),
                   serving_model=sm,
                   meal_slots=raw.get("meal_slots"),
                   max_dishes_per_slot=raw.get("max_dishes_per_slot"),
                   target_profiles=raw.get("target_profiles"),
                   week=raw.get("week"),
                   raw=raw)


def resolve_meal_slots(person) -> Optional[list]:
    """THE one slot-resolution point (M1.9, M19_SPEC §2.3) — schema defaults
    live in the model layer, so every consumer (dealer, picker nudge, eat
    sheets, doctor) resolves a person's meal slots through exactly this
    function. Accepts a ``Person`` or any dict-style person mapping.

    Rules (validated in io_yaml — this function assumes a valid document):
    - ``meals_per_day`` set, ``meal_slots`` absent → generated slots named
      ``meal_1..meal_n`` (breakfast/lunch/dinner semantics are NOT guessed —
      open question, M19_SPEC §11.2).
    - ``meal_slots`` set (alone or with a matching ``meals_per_day``) →
      those slots; ``n = len(meal_slots)``.
    - Neither → ``None``: no meal layer for this person (the layer is
      inert, byte-identical pipeline).

    Each resolved slot is ``{"name", "serving_model", "interchangeable"}``:
    slots inherit the person's ``serving_model`` (default "portioned")
    unless they override it; ``interchangeable`` defaults False (opt-in,
    never default — PRD §4.0 Amendment 2).
    """
    sm = person.get("serving_model") or "portioned"
    slots = person.get("meal_slots")
    if slots:
        return [dict(name=s["name"],
                     serving_model=s.get("serving_model") or sm,
                     interchangeable=bool(s.get("interchangeable", False)))
                for s in slots]
    n = person.get("meals_per_day")
    if n:
        return [dict(name=f"meal_{k + 1}", serving_model=sm,
                     interchangeable=False) for k in range(n)]
    return None


# --------------------------------------------------------------------------- #
#  M1.11 target-profile resolution (M111_SPEC §5) — THE one resolution point
#  (P10), beside resolve_meal_slots on purpose: schema semantics live in the
#  model layer; engine/dishes/meals/doctor/renderers all consume THESE
#  functions and never do weekday math of their own.
# --------------------------------------------------------------------------- #
def week_day_key(day_index, anchor) -> str:
    """Plan day ``day_index`` (0-indexed) has this calendar weekday key.
    THE one weekday-math site — ``(anchor + day_index) % 7`` appears
    exactly here; ``anchor = plan_date.weekday()`` (int 0-6, Monday=0) is
    derived once at the CLI boundary. Any day count works: resolution is
    mod-7 for ``days != 7`` too (M111_SPEC §13)."""
    return DAY_KEYS[(anchor + day_index) % 7]


def week_day_label(person, day_index, anchor) -> Optional[str]:
    """The day-type NAME plan day ``day_index`` resolves to, or None for
    the base day (drives the §9 rendering branch: base days show the
    weekday only). A person without a week map is always base — no anchor
    needed, so the inert path never demands one."""
    week = person.get("week")
    if not week:
        return None
    if day_index is None or anchor is None:
        raise ValueError(
            "person authors a target-profile week map but no plan-day/"
            "anchor was supplied — the CLI derives anchor = "
            "plan_date.weekday() and its date_required check fires first "
            "for real users (M1.11)")
    return week.get(week_day_key(day_index, anchor))


def resolve_targets(person, day_index=None, anchor=None) -> dict:
    """THE canonical per-day target resolution (M1.11, P10).

    No profiles → returns ``person["targets"]`` — the SAME object
    (identity), which is the inertness proof (M111_SPEC §8 link 1).
    Profiles present and no day/anchor → ValueError: an engine-side bug
    guard, never a user-facing path (cli's structured ``date_required``
    fires pre-solve). A weekday absent from ``week`` resolves to the base
    ``targets`` — explicit documented absence semantics, not inference.
    """
    profs = person.get("target_profiles")
    if not profs:
        return person["targets"]
    if day_index is None or anchor is None:
        raise ValueError(
            "person authors target_profiles but resolve_targets was "
            "called without day_index/anchor — thread anchor from the "
            "plan date (M1.11)")
    label = week_day_label(person, day_index, anchor)
    if label is None:
        return person["targets"]
    return profs[label]


def person_for_day(person, day_index=None, anchor=None):
    """Day-view person (M111_SPEC §5 injection strategy): the IDENTICAL
    person object when resolution returns the base map (object identity —
    zero churn, byte-identical downstream behavior); otherwise a copy
    whose ``targets`` is the resolved day map (``Person.from_raw`` for
    Person inputs, dict copy for plain mappings). Never mutates the
    input. The week-loop layer (build_week / build_week_dishes /
    deal_week / replate*) injects this view so every inner
    ``person["targets"]`` read is day-correct with zero diff."""
    resolved = resolve_targets(person, day_index, anchor)
    if resolved is person["targets"]:
        return person
    if isinstance(person, Person):
        raw = dict(person.raw)
        raw["targets"] = resolved
        return Person.from_raw(person.name, raw)
    view = {k: v for k, v in person.items()}
    view["targets"] = resolved
    return view


def weekly_targets(person, days, anchor=None) -> dict:
    """The emergent weekly sum (M111_SPEC §4): Σ over plan days of
    ``resolve_targets``. REPORTING ONLY (plan.md person header, eat-sheet
    header, calorie-share table) — no LP constraint spans days; free
    weekly allocation is the declared PRD §5.2 successor. A no-profile
    person needs no anchor and sums to ``days × targets``."""
    out = {m: 0.0 for m in MACROS}
    for d in range(days):
        t = resolve_targets(person, d, anchor)
        for m in MACROS:
            out[m] += t[m]
    return out


def distinct_day_types(person) -> list:
    """Ordered ``(label, targets, weekdays)`` triples — one per DISTINCT
    day-type (M111_SPEC §7): ``("base", targets, <weekdays not in week>)``
    first, included iff any weekday resolves to base (always, for a
    no-profile person: one entry covering all seven), then one entry per
    profile in authoring order with the weekday keys mapping to it. The
    doctor loops THESE — feasibility is per day-type, a set of macro maps,
    anchor-free by construction — so it stays O(distinct types), never
    O(7), and needs no date."""
    profs = person.get("target_profiles")
    if not profs:
        return [("base", person["targets"], DAY_KEYS)]
    week = person.get("week") or {}
    out = []
    base_days = tuple(k for k in DAY_KEYS if k not in week)
    if base_days:
        out.append(("base", person["targets"], base_days))
    for name in profs:
        days = tuple(k for k in DAY_KEYS if week.get(k) == name)
        out.append((name, profs[name], days))
    return out


@dataclass
class Dish(_RawView):
    """One named plate (M1.13, M113_SPEC §2 — the data steward's draft
    schema consumed as-is). A dish is a named combination of components
    with per-SERVING ratio bands: ``components`` maps component id →
    ``{base_g, min_g, max_g}`` in grams of the finished component per one
    serving. ``accents`` is the subset of ``components`` that is optional
    per-person finishing (min_g 0 — droppable, dish intact); a CORE member
    is a components entry with min_g > 0. ``compatible_sides`` may be
    served ALONGSIDE (no bands here — the side's own serve_g governs).
    ``meal_affinity`` labels match person slot NAMES only (breakfast
    semantics are never guessed — M19 §11.2 precedent). ``cuisine`` is
    optional authored data; absent, it derives from the main-role member.
    ``reconstruction`` (from_source | inferred | invented) and ``source``
    are the provenance the doctor reports; ``notes`` ride to the reviewer.
    """

    id: str
    name: str
    components: dict                 # {cid: {"base_g","min_g","max_g"}}
    accents: list = field(default_factory=list)
    compatible_sides: list = field(default_factory=list)
    meal_affinity: list = field(default_factory=list)
    cuisine: Optional[str] = None    # optional authored; else derived
    source: Optional[str] = None
    reconstruction: Optional[str] = None
    notes: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, d: dict) -> "Dish":
        return cls(id=d.get("id"), name=d.get("name"),
                   components={cid: dict(b) for cid, b in
                               (d.get("components") or {}).items()},
                   accents=list(d.get("accents") or []),
                   compatible_sides=list(d.get("compatible_sides") or []),
                   meal_affinity=list(d.get("meal_affinity") or []),
                   cuisine=d.get("cuisine"), source=d.get("source"),
                   reconstruction=d.get("reconstruction"),
                   notes=d.get("notes"), raw=dict(d))


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
    """On-hand stock (M0.12/M1.8, PRD §8.1).

    - stock: raw ingredient grams on hand — deducted from purchase need
      BEFORE pack rounding (costing.purchase), after the acquired-age rule
      (costing.age_pantry, M1.8: stock too old to survive to any cook
      session is not deducted and is reported as expiring unused).
    - cooked: cooked-component leftovers — LIVE since M1.8:
      costing.cooked_leftovers joins them into build_week availability with
      residual life, and session_plan consumes them before fresh batches.

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


# The ONE place schema defaults are declared (M0.17). Field-by-field:
#   days                       7      plan length
#   batch_time_factor          0.45   marginal-batch time share (provisional, P9)
#   min_lean_anchors           2      M0.5: effective prototype behavior kept
#   shop_days                  [0]    M0.6: one shopping trip, day 0
#   max_batches_per_component  3      variety/volume guard
#   use_freezer                True   M0.5: freezer bridging on by default
#   cook_plan_style            recipe M1.10: per-dish blocks; "timeline" is
#                                     the M1.12 interleaved stream (greedy
#                                     scheduler — runs ONLY for timeline
#                                     households)
#   stations                   see STATIONS_DEFAULTS (M1.12, provisional):
#                                     household station inventory; any
#                                     subset may be overridden
# No default — REQUIRED:
#   cook_days                  validated required (io_yaml, M0.17): the
#                              prototype's silent [0, 3] fallback is gone
#   active_min_budget          engine indexes it; every corpus declares it
#   max_days_same_component    engine indexes it; every corpus declares it
#   budget                     defaulted to {"mode": "off"} at load
#                              (io_yaml.load), not here
SETTINGS_DEFAULTS = {
    "days": 7,
    "batch_time_factor": 0.45,
    "min_lean_anchors": 2,
    "shop_days": [0],
    "max_batches_per_component": 3,
    "use_freezer": True,
    "cook_plan_style": "recipe",
    "dish_layer": "permissive",    # M1.13: orphan-side policy (P9 — the
                                   # documented ratchet flips this to
                                   # "strict" after steward coverage)
    "stations": STATIONS_DEFAULTS,
}


@dataclass
class Settings(_RawView):
    days: Optional[int] = None
    active_min_budget: Optional[float] = None
    batch_time_factor: Optional[float] = None
    max_days_same_component: Optional[int] = None
    min_lean_anchors: Optional[int] = None
    cook_days: Optional[list] = None      # REQUIRED — no default (M0.17)
    shop_days: Optional[list] = None      # M0.6: shopping trips are data
    max_batches_per_component: Optional[int] = None
    use_freezer: Optional[bool] = None    # M0.5: freezer bridging, default true
    cook_plan_style: Optional[str] = None  # M1.10: recipe | timeline
    dish_layer: Optional[str] = None      # M1.13: permissive | strict
    stations: Optional[dict] = None       # M1.12: station inventory
    budget: Any = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, d: dict, budget: Any) -> "Settings":
        raw = dict(d)
        raw["budget"] = budget
        for k, v in SETTINGS_DEFAULTS.items():
            if raw.get(k) is None:
                raw[k] = list(v) if isinstance(v, list) else (
                    dict(v) if isinstance(v, dict) else v)
        # M1.12: a partial stations mapping overrides field-by-field — the
        # unstated fields keep the PROVISIONAL defaults (one place, here).
        raw["stations"] = {**STATIONS_DEFAULTS, **(raw["stations"] or {})}
        # canonical form everywhere downstream: sorted, deduped. cook_days
        # gets the same treatment as shop_days — session attribution's
        # "earliest session wins" (PRD §8.2) must mean earliest DAY, never
        # first-in-YAML-list-order: [4, 0] and [0, 4] are the same schedule.
        raw["shop_days"] = sorted(set(raw["shop_days"]))
        if isinstance(raw.get("cook_days"), list):
            raw["cook_days"] = sorted(set(raw["cook_days"]))
        return cls(days=raw["days"],
                   active_min_budget=raw.get("active_min_budget"),
                   batch_time_factor=raw["batch_time_factor"],
                   max_days_same_component=raw.get("max_days_same_component"),
                   min_lean_anchors=raw["min_lean_anchors"],
                   cook_days=raw.get("cook_days"),
                   shop_days=raw["shop_days"],
                   max_batches_per_component=raw["max_batches_per_component"],
                   use_freezer=raw["use_freezer"],
                   cook_plan_style=raw["cook_plan_style"],
                   dish_layer=raw["dish_layer"],
                   stations=raw["stations"],
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
        household_unit=c.get("household_unit"), anchor=c.get("anchor"),
        freezes=c.get("freezes"), pairs_with=c.get("pairs_with"),
        source=c.get("source"),
        per100=raw["per100"], tags=raw["tags"], raw=raw)
