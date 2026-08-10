"""The refusal taxonomy — every way the simulated world can tell the cook "no".

This module is the heart of the gate's epistemics. An LLM asked to *imagine*
cooking produces critique, and critique is unfalsifiable: you can neither
trust it when it complains nor believe it when it stays silent. So the cook
here does not get to judge. It proposes an action; the world accepts or
refuses; and a refusal is a typed exception carrying the numbers that caused
it.

Every exception below therefore answers three questions the scorer needs:

    defect_class  what KIND of problem this is (the closed taxonomy the
                  findings schema validates against — adding a member here
                  is a deliberate act, never an ad-hoc string)
    detail        the human sentence, with the actual numbers in it
    state         the world's relevant state at the moment of refusal, so a
                  reader can reconstruct the failure without the transcript

`AssumptionRequired` is the odd one out and the most important. It is not a
failure of the cook — it is the world refusing to proceed until the cook
*declares* what it is about to make up. It exists because the real leak in
this design is not filesystem access, it is the model's own world knowledge:
an LLM that knows kitchens will silently patch exactly the omissions we are
trying to surface ("obviously you refrigerate raw chicken"). Routing every
such patch through one chokepoint is what converts invisible competence into
a countable finding.
"""

from __future__ import annotations

from typing import Any, Optional

# --------------------------------------------------------------------------- #
#  the closed defect taxonomy
#
#  Slice 1 (store + shopping) raises the first group. The later groups are
#  declared NOW, unraised, so the findings schema and the scorer are stable
#  across slices and a reader can see the whole intended shape of the gate.
# --------------------------------------------------------------------------- #
DEFECT_CLASSES = (
    # ---- slice 1: the store ----
    "product_not_found",          # no real product matches a list row
    "product_ambiguous",          # several plausible, sheet gives no way to choose
    "unit_untranslatable",        # grams demanded of something sold by bunch/count
    "pack_size_mismatch",         # the stated pack does not exist in the store
    "quantity_deviation",         # purchasable amount != the amount asked for
    "cost_deviation",             # receipt materially differs from the stated total
    "ingredient_absent_from_list",  # a cook step needs it; no list row buys it
    "identifier_not_human",       # raw snake_case id where a product name belongs
    "orientation_missing",        # no shop day / no start / no prerequisites
    "storage_unspecified",        # nothing says where this goes or that it must be frozen
    # ---- slice 2: the kitchen (declared, not yet raised) ----
    "vessel_capacity_exceeded",
    "no_free_burner",
    "oven_temperature_conflict",
    "equipment_absent",
    "time_model_false",
    "hands_busy",
    # ---- slice 3: storage, containers, expiry (declared, not yet raised) ----
    "container_shortage",
    "cold_storage_capacity",
    "food_expired",
    "food_never_cooked",
    "container_not_found",
    "surplus_unconsumed",
    "reheat_absent",
    # ---- cross-cutting ----
    "arithmetic_mismatch",        # the sheets disagree with each other
    "assumption_required",        # the cook had to supply what the sheets omitted
    "implausible_meal",           # cook judgment; weighted lowest
)


class SimError(Exception):
    """Base: a refusal by the world, carrying everything the scorer needs.

    Subclasses set ``defect_class``. Instances carry ``state`` — a plain dict
    of the numbers involved — so a finding can quote physics rather than
    vibes.
    """

    defect_class: str = "arithmetic_mismatch"

    def __init__(self, detail: str, **state: Any):
        super().__init__(detail)
        self.detail = detail
        self.state = state

    def as_dict(self) -> dict:
        return {"defect_class": self.defect_class,
                "detail": self.detail,
                "state": self.state}


# --------------------------------------------------------------------------- #
#  the store
# --------------------------------------------------------------------------- #
class NoSuchProduct(SimError):
    """Nothing on any shelf matches. The classic instance is a shopping row
    naming a *category* rather than a product — you cannot buy "spices"."""

    defect_class = "product_not_found"


class AmbiguousProduct(SimError):
    """Several real products match and the sheet gives no basis to choose
    (fresh vs frozen shrimp; bone-in vs boneless). Raised only when the cook
    tries to skip the choice — declaring an assumption is the way through."""

    defect_class = "product_ambiguous"


class UnitUntranslatable(SimError):
    """The sheet asks for grams of something the world sells by bunch or by
    count. Nobody weighs cilantro at the shelf; you pick up bunches."""

    defect_class = "unit_untranslatable"


class PackSizeMismatch(SimError):
    """The sheet states a pack size ("5 × 32 oz") that no shelf carries."""

    defect_class = "pack_size_mismatch"


class NotSoldHere(SimError):
    """A real thing, but this store does not carry it — distinct from
    `NoSuchProduct`, which is "this is not a purchasable object at all"."""

    defect_class = "product_not_found"


# --------------------------------------------------------------------------- #
#  the chokepoint
# --------------------------------------------------------------------------- #
class AssumptionRequired(SimError):
    """The world will not proceed until the cook declares, on the record,
    the thing the sheets failed to say.

    Raised with the FIELD that is missing so the scorer can group by it:
    every cook who had to invent a pot size produces the same
    ``field="vessel"`` assumption, and that repetition across personas is
    what promotes a finding from anecdote to structural.
    """

    defect_class = "assumption_required"

    def __init__(self, field: str, why: str, **state: Any):
        super().__init__(
            f"the sheets do not specify {field}: {why} — call "
            f"assume({field!r}, value, why) to proceed on the record",
            field=field, why=why, **state)
        self.field = field


class GaveUp(SimError):
    """The cook stopped. This is the strongest signal the gate can produce:
    a human holding these sheets could not continue. Always BLOCKER-eligible
    (see score.py — BLOCKER is machine-checked, never self-declared)."""

    defect_class = "orientation_missing"

    def __init__(self, what: str, why: str, **state: Any):
        super().__init__(f"gave up on {what}: {why}", what=what, why=why,
                         **state)


# --------------------------------------------------------------------------- #
#  procedure errors — the cook misusing the world, not the world failing
#
#  These are NOT findings. They are the simulator telling the cook it did
#  something incoherent (checked out twice, referenced a SKU it never saw).
#  score.py drops them; a high rate of them means the tool surface is
#  confusing and belongs in the gate's own calibration, not in the report
#  about the meal planner.
# --------------------------------------------------------------------------- #
class ProcedureError(SimError):
    """Cook error, not a product defect. Excluded from findings."""

    defect_class = "arithmetic_mismatch"


class UnknownSku(ProcedureError):
    pass


class AlreadyCheckedOut(ProcedureError):
    pass


class NothingInCart(ProcedureError):
    pass


def is_finding(exc: SimError) -> bool:
    """Does this refusal say something about the MEAL PLAN, or about the
    cook's handling of our own tools? Only the former is a finding."""
    return not isinstance(exc, ProcedureError)
