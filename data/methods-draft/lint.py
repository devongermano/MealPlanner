#!/usr/bin/env python3
"""Internal-consistency lint for data/methods-draft/ + data/techniques/.

Run from the repo root: python3 data/methods-draft/lint.py
Read-only over examples/components.yaml. Exits 1 on any finding.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
METHODS = ROOT / "data" / "methods-draft"
TECHNIQUES = ROOT / "data" / "techniques" / "techniques.yaml"
COMPONENTS = ROOT / "examples" / "components.yaml"

PHASES = ["prep", "cook", "finish"]
STATIONS = {"prep", "stove", "oven", "grill", "none"}
MODES = {"active", "passive"}

def main() -> int:
    errors: list[str] = []

    comp_ids = {c["id"] for c in yaml.safe_load(COMPONENTS.read_text())["components"]}
    tech_doc = yaml.safe_load(TECHNIQUES.read_text())
    techniques = tech_doc["techniques"]
    tech_ids = set(techniques)
    for tid, t in techniques.items():
        for field in ("name", "one_line", "how"):
            if not t.get(field):
                errors.append(f"techniques.yaml: {tid}: missing {field}")
        if not (isinstance(t.get("how"), list) and 3 <= len(t["how"]) <= 6):
            errors.append(f"techniques.yaml: {tid}: how must be 3-6 steps")
        if "video" not in t:
            errors.append(f"techniques.yaml: {tid}: missing video placeholder")

    files = {p.stem: p for p in METHODS.glob("*.yaml")}
    if missing := comp_ids - files.keys():
        errors.append(f"components with no method file: {sorted(missing)}")
    if extra := files.keys() - comp_ids:
        errors.append(f"method files with no component: {sorted(extra)}")

    used_ops: set[str] = set()
    totals: dict[str, tuple[int, int]] = {}
    for cid in sorted(files):
        doc = yaml.safe_load(files[cid].read_text())
        loc = f"{files[cid].name}"
        if doc.get("component") != cid:
            errors.append(f"{loc}: component field != filename stem")
        steps = doc.get("steps") or []
        if not steps:
            errors.append(f"{loc}: no steps")
        last_phase = -1
        active = passive = 0
        for i, s in enumerate(steps, 1):
            at = f"{loc} step {i}"
            phase = s.get("phase")
            if phase not in PHASES:
                errors.append(f"{at}: bad phase {phase!r}")
            else:
                idx = PHASES.index(phase)
                if idx < last_phase:
                    errors.append(f"{at}: phase {phase} after {PHASES[last_phase]}")
                last_phase = max(last_phase, idx)
            if not (s.get("text") or "").strip():
                errors.append(f"{at}: empty text")
            if s.get("station") not in STATIONS:
                errors.append(f"{at}: bad station {s.get('station')!r}")
            if s.get("mode") not in MODES:
                errors.append(f"{at}: bad mode {s.get('mode')!r}")
            d = s.get("duration_min")
            if not (isinstance(d, int) and d > 0):
                errors.append(f"{at}: duration_min must be a positive int, got {d!r}")
            else:
                if s.get("mode") == "active":
                    active += d
                else:
                    passive += d
            has_temp = "oven_temp_f" in s
            if s.get("station") == "oven" and not has_temp:
                errors.append(f"{at}: oven step missing oven_temp_f")
            if s.get("station") != "oven" and has_temp:
                errors.append(f"{at}: oven_temp_f on non-oven step")
            if has_temp and not (isinstance(s["oven_temp_f"], int) and s["oven_temp_f"] > 0):
                errors.append(f"{at}: bad oven_temp_f {s['oven_temp_f']!r}")
            if op := s.get("operation"):
                used_ops.add(op)
                if op not in tech_ids:
                    errors.append(f"{at}: unknown operation {op!r}")
        totals[cid] = (active, passive)

    if unused := tech_ids - used_ops:
        errors.append(f"unused techniques: {sorted(unused)}")

    if errors:
        print(f"LINT FAILED — {len(errors)} finding(s):")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"LINT CLEAN — {len(files)} components, {len(tech_ids)} techniques, "
          f"{len(used_ops)} operations in use")
    print(f"{'component':<24} {'active':>6} {'passive':>7} {'total':>6}  (min, one batch, provisional)")
    for cid, (a, p) in sorted(totals.items()):
        print(f"{cid:<24} {a:>6} {p:>7} {a + p:>6}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
