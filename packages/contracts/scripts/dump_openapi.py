#!/usr/bin/env python3
"""Dump the solver service's OpenAPI document — the codegen source of truth.

Usage: python dump_openapi.py [OUT_JSON]   (default: openapi.json next to
the package root). Requires the mealplan package with the ``service`` extra
(fastapi + pydantic) importable — use the repo venv.

Deterministic by construction: json.dumps(sort_keys=True) over
``app.openapi()``; two dumps of the same code are byte-identical
(tests/test_contracts.py asserts this across processes).

Supplementary schemas: engine result models that no route returns YET
(PlateResult, DoctorReport and its parts, StructuredWarning) are injected
into components.schemas so the generated TS covers the full contract surface
from day one. When M2 binds them to routes (e.g. POST /doctor), FastAPI will
emit them natively and the injection becomes a no-op (route-emitted names
win — we never overwrite an existing component).
"""

import json
import sys
from pathlib import Path

from mealplan import schemas
from mealplan.service import app

SUPPLEMENTARY = (schemas.StructuredWarning, schemas.PlateResult,
                 schemas.DoctorReport)

REF_TEMPLATE = "#/components/schemas/{model}"


def build_spec() -> dict:
    spec = app.openapi()
    comp = spec.setdefault("components", {}).setdefault("schemas", {})
    for model in SUPPLEMENTARY:
        s = model.model_json_schema(ref_template=REF_TEMPLATE)
        for name, sub in s.pop("$defs", {}).items():
            comp.setdefault(name, sub)
        comp.setdefault(model.__name__, s)
    return spec


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent.parent / "openapi.json"
    out.write_text(json.dumps(build_spec(), indent=2, sort_keys=True,
                              ensure_ascii=False) + "\n")
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
