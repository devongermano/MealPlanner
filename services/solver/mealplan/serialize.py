"""serialize.py — JSON-safe conversion and canonical hashing (M1.3/M1.4).

Two jobs, both P10-disciplined (convert, never reshape):

1. ``jsonable(x)`` — recursively convert an engine/costing structure into
   plain JSON types. This is a CONVERSION layer only: dict keys become
   strings, tuples/sets become lists, dates become ISO strings, and the
   model dataclasses (Ingredient, Component, Person, Settings, Pantry —
   anything carrying a ``.raw`` mapping) collapse to their raw mapping.
   No renaming, no restructuring: the CLI ``--json`` result is the SAME
   object the renderer consumes.

2. ``canonical_json(x)`` / ``sha256_of(x)`` — the canonical serialization
   the locked-plan inputs hash is computed over (M1.3, PRD §8.1): sorted
   keys, compact separators, ASCII-only, NaN refused. Byte-stable for a
   given structure across processes and platforms, so the same snapshot
   always hashes the same.

Determinism: no wall clock and no builtin hashing — ``hashlib.sha256`` is
content hashing, fully deterministic (and under the static scan).
"""

import datetime
import hashlib
import json


def jsonable(x):
    """Recursively convert ``x`` to plain JSON-safe types (see module
    docstring). Unknown objects fall back to ``str(x)`` — loudly greppable
    rather than a crash, but every engine structure is covered above."""
    if isinstance(x, bool) or x is None or isinstance(x, (int, str)):
        return x
    if isinstance(x, float):
        return x
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted((jsonable(v) for v in x), key=repr)
    if isinstance(x, datetime.datetime):
        return x.isoformat()
    if isinstance(x, datetime.date):
        return x.isoformat()
    raw = getattr(x, "raw", None)
    if isinstance(raw, dict):
        return jsonable(raw)
    return str(x)


def canonical_json(x) -> str:
    """The ONE canonical serialization hashes are computed over: JSON with
    sorted keys, compact separators, ASCII-only, NaN refused."""
    return json.dumps(jsonable(x), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def sha256_of(x) -> str:
    """sha256 hex digest of ``canonical_json(x)`` (M1.3 inputs hash)."""
    return hashlib.sha256(canonical_json(x).encode("utf-8")).hexdigest()
