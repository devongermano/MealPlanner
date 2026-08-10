"""lockplan.py — locked plan artifacts (M1.3, PRD §4.3 / §8.1 / §8.2).

The plan artifact is the record of a lock: an immutable YAML document
holding a verbatim snapshot of every input (the three library documents,
the pantry document, the CLI overrides, the seed, the plan date), a sha256
over the canonical JSON of exactly that snapshot, and the solved outputs
(menu, per-person day portions, session plan, warnings/relax tiers). A
``veto_history`` list is present and empty — reserved for the M2
propose → veto → lock loop.

Artifact key (PRD §8.2): plans are keyed by the PRIMARY TRIP date — the
plan's first shopping trip. Computed here, precisely: the plan start date
(``--date``) advanced by ``sorted(settings["shop_days"])[0]`` days. With
the default ``shop_days: [0]`` the key equals the plan date.

Immutability (PRD §8.1): ``write_plan`` REFUSES to overwrite an existing
``plan.yaml`` (structured error, CLI exit 3). With supersede, the old file
is RENAMED to ``plan.superseded-<n>.yaml`` — its content is never
modified (byte-identical before and after) — and the new plan's
``supersedes`` header lists the file it displaced.

Reproducibility: ``mealplan verify-plan`` recomputes the inputs hash from
the embedded snapshot and re-solves from it (cli._solve_from_snapshot),
comparing menu + portions. A fresh lock verifies clean on the reference
environment; a tampered file or altered snapshot fails loudly.

No wall clock anywhere (determinism scan): the plan date is caller
supplied, and the artifact deliberately carries no "created at" timestamp
— its identity is its key + inputs hash.
"""

import datetime
import re
from pathlib import Path

import yaml

from . import __version__
from .serialize import jsonable, sha256_of

PLAN_SCHEMA_VERSION = 1
PLAN_FILENAME = "plan.yaml"
_SUPERSEDED_RE = re.compile(r"^plan\.superseded-(\d+)\.yaml$")


class LockExists(Exception):
    """plan.yaml already exists at the key and supersede was not given."""

    def __init__(self, path):
        self.path = Path(path)
        super().__init__(
            f"a locked plan already exists at {path} — plans are immutable "
            "(PRD §8.1). Re-run with --supersede to rename it to "
            "plan.superseded-<n>.yaml (content untouched) and lock a new "
            "plan in its place.")


def primary_trip_date(plan_date: datetime.date, settings) -> datetime.date:
    """The artifact key (PRD §8.2): plan start date advanced by the first
    (sorted) shop day. ``settings['shop_days']`` is already sorted+deduped
    by the model layer; sorted() again keeps this correct for raw dicts."""
    first_trip = sorted(settings["shop_days"])[0]
    return plan_date + datetime.timedelta(days=first_trip)


def build_snapshot(library_docs: dict, pantry_doc, overrides: dict,
                   seed: int, plan_date: datetime.date) -> dict:
    """The verbatim inputs snapshot the hash covers — EXACTLY these fields
    (M1.3): the three library documents, the pantry document (or None),
    the CLI overrides, the seed, and the plan date."""
    return {
        "library": {
            "ingredients": library_docs["ingredients"],
            "components": library_docs["components"],
            "people": library_docs["people"],
        },
        "pantry": pantry_doc,
        "overrides": overrides,
        "seed": seed,
        "plan_date": plan_date.isoformat(),
    }


def inputs_sha256(snapshot: dict) -> str:
    """sha256 over the canonical JSON of exactly the snapshot."""
    return sha256_of(snapshot)


def build_plan_doc(snapshot, key, plan_date, menu, weeks, sp, feasible,
                   misses, relax_tiers, warnings, supersedes=(), meals=None):
    """Assemble the plan.yaml document. Everything passes through
    ``jsonable`` so the YAML written is plain scalars/lists/maps.

    ``meals`` (M1.9, PRD §4.0): the dealt MealDay structure per configured
    person — meals are PLAN CONTENT, not rendering, so they lock with the
    plan. The key is present only when somebody configures meals: a
    meal-less plan document stays byte-identical to pre-M1.9 (the layer is
    inert). The inputs hash covers meal_slots / serving_model /
    meals_per_day automatically — the snapshot embeds people.yaml verbatim.
    """
    doc = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "schema": "mealplan/v2",
        "engine_version": __version__,
        "key": key.isoformat(),
        "primary_trip_date": key.isoformat(),
        "plan_date": plan_date.isoformat(),
        "supersedes": list(supersedes),
        "inputs_sha256": inputs_sha256(snapshot),
        "inputs": snapshot,
        "menu": menu,
        "feasible": feasible,
        "misses": misses,
        "portions": weeks,          # per-person, per-day solved grams
        "session_plan": sp,
        "relax_tiers": relax_tiers,
        "warnings": warnings,
        "veto_history": [],         # reserved for M2 (propose → veto → lock)
    }
    if meals:
        doc["meals"] = meals        # per-person, per-day dealt MealDay
    return jsonable(doc)


def _next_superseded_name(plan_dir: Path) -> str:
    ns = [int(m.group(1)) for p in plan_dir.iterdir()
          if (m := _SUPERSEDED_RE.match(p.name))]
    return f"plan.superseded-{max(ns, default=0) + 1}.yaml"


def write_plan(plan_dir, doc, supersede=False):
    """Write ``doc`` as plan.yaml under ``plan_dir``.

    Returns ``(path, superseded_names)``. If plan.yaml exists: raises
    LockExists unless ``supersede`` — then the old file is RENAMED (never
    rewritten) to plan.superseded-<n>.yaml and the new document's
    ``supersedes`` field is set to name it.
    """
    plan_dir = Path(plan_dir)
    plan_dir.mkdir(parents=True, exist_ok=True)
    path = plan_dir / PLAN_FILENAME
    superseded = []
    if path.exists():
        if not supersede:
            raise LockExists(path)
        newname = _next_superseded_name(plan_dir)
        path.rename(plan_dir / newname)      # rename only: bytes untouched
        superseded = [newname]
    doc = dict(doc)
    doc["supersedes"] = superseded
    path.write_text(yaml.safe_dump(doc, sort_keys=False,
                                   default_flow_style=False))
    return path, superseded


def load_plan(path):
    """Read a plan.yaml back. Raises ValueError on a document that is not a
    locked plan (missing the fields verify-plan needs)."""
    doc = yaml.safe_load(Path(path).read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: not a mapping — not a locked plan")
    missing = [k for k in ("schema_version", "inputs", "inputs_sha256",
                           "menu", "portions") if k not in doc]
    if missing:
        raise ValueError(f"{path}: missing plan field(s): "
                         f"{', '.join(missing)} — not a locked plan")
    if doc["schema_version"] != PLAN_SCHEMA_VERSION:
        raise ValueError(f"{path}: unknown plan schema_version "
                         f"{doc['schema_version']!r}; "
                         f"known: {PLAN_SCHEMA_VERSION}")
    return doc


def check_hash(doc) -> bool:
    """Does the stored inputs_sha256 match the embedded snapshot?"""
    return inputs_sha256(doc["inputs"]) == doc["inputs_sha256"]
