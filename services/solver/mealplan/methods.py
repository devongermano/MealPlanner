"""methods.py — method-fragment loader + compiled-cook-script derivations
(M1.10, PRD §4.0 / §6).

Recipes are ground truth, never rendered (PRD §6): library components carry
composable method-STEP fragments (one file per component in a methods
directory — Track E's ``data/methods-draft``), and what users follow is the
compiled cook script synthesized per plan. This module owns:

1. **Loading + validation** of the fragment directory against the schema the
   fragments' README declares (schema_version 0): step enums
   (phase/station/mode), positive durations, ``oven_temp_f`` present iff
   ``station: oven``, ``prep -> cook -> finish`` phase ordering, and
   ``operation`` ids resolving against the technique library. Validation is
   GRACEFUL by design: an invalid fragment file is skipped with a structured
   warning and its component degrades to the pre-M1.10 ingredient-list
   rendering — a malformed fragment can never take the cook plan down.
2. **Ingredient identity** for a step (``step_ingredient``): the compiler
   injects batch-scaled quantities into step text, and shared-prep
   consolidation needs to know WHICH ingredient a step handles. Identity is
   derived by matching the component's own ingredient ids against the step
   text (word-prefix match on id tokens); a step matching zero or several
   ingredients has NO identity and therefore never merges (the
   spec's rule: steps lacking operation/ingredient identity never merge).
3. **Shared-prep consolidation** (``consolidate_shared_prep``): prep-phase
   steps with the same ``(operation, ingredient)`` key merge across a
   session's components into one step with per-dish gram allocation
   ("Dice 500g onion_yellow — 300g picadillo, 200g sausage_sugo").
   Deterministic: sorted component order, sorted keys, batch-scaled grams.
4. **Station summary** (``station_summary``): per-session aggregation of
   step durations by station, oven time bucketed by temperature —
   the temp-keyed shareable buckets of PRD §4.0 ("steaks finish at 425
   because the veggies already made it a 425 oven"). Durations are
   single-batch estimates and PROVISIONAL until cook-day calibration
   (PRD §4.0); nothing may assert on them in tests.

The loader takes a caller-supplied directory (CLI ``--methods PATH``;
default ``./data/methods-draft`` relative to the cwd when it exists — the
package hardcodes NO repo paths, PRD P1). The technique library resolves
from ``<methods_dir>/../techniques/techniques.yaml`` by default.
"""

import re
from pathlib import Path

import yaml

METHODS_SCHEMA_VERSION = 0
PHASES = ("prep", "cook", "finish")
STATIONS = ("prep", "stove", "oven", "grill", "none")
STEP_MODES = ("active", "passive")

STEP_REQUIRED = ("phase", "text", "station", "mode", "duration_min")
STEP_ALLOWED = STEP_REQUIRED + ("oven_temp_f", "operation")


# --------------------------------------------------------------------------- #
#  path resolution — caller-supplied, no package-relative repo paths (P1)
# --------------------------------------------------------------------------- #
def default_methods_dir(cwd=None):
    """``<cwd>/data/methods-draft`` when it exists, else None. Mirrors the
    --library default (./examples relative to the cwd): the package never
    hardcodes a repo path."""
    d = Path(cwd or Path.cwd()) / "data" / "methods-draft"
    return d if d.is_dir() else None


def default_techniques_path(methods_dir):
    """``<methods_dir>/../techniques/techniques.yaml`` when it exists —
    the repo layout the fragments' README documents."""
    if methods_dir is None:
        return None
    p = Path(methods_dir).parent / "techniques" / "techniques.yaml"
    return p if p.is_file() else None


# --------------------------------------------------------------------------- #
#  loading + validation (graceful degradation, structured warnings)
# --------------------------------------------------------------------------- #
def _warn(warnings, code, where, message):
    warnings.append(dict(code=code, where=where, message=message))


def load_techniques(path):
    """The technique library: ``{operation_id: {name, one_line, how, ...}}``.
    Returns ``(techniques, warnings)`` — a missing/unreadable file degrades
    to no technique footnotes, never an error."""
    warnings = []
    if path is None:
        return {}, warnings
    p = Path(path)
    try:
        doc = yaml.safe_load(p.read_text())
    except (OSError, yaml.YAMLError) as e:
        _warn(warnings, "invalid_techniques", str(p),
              f"technique library unreadable ({e}) — steps render without "
              "technique footnotes")
        return {}, warnings
    techs = (doc or {}).get("techniques")
    if not isinstance(techs, dict):
        _warn(warnings, "invalid_techniques", str(p),
              "technique library has no 'techniques' mapping — steps render "
              "without technique footnotes")
        return {}, warnings
    return techs, warnings


def _validate_fragment(doc, fname):
    """One fragment document -> (component_id, steps) or a ValueError with
    the first structural problem (the loader turns it into a warning and
    skips the file — graceful degradation)."""
    if not isinstance(doc, dict):
        raise ValueError("fragment is not a mapping")
    if doc.get("schema_version") != METHODS_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version {doc.get('schema_version')!r} != "
            f"{METHODS_SCHEMA_VERSION}")
    cid = doc.get("component")
    if not cid or not isinstance(cid, str):
        raise ValueError("missing 'component' id")
    if cid != Path(fname).stem:
        raise ValueError(f"component '{cid}' does not match filename "
                         f"'{Path(fname).stem}'")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("'steps' must be a non-empty list")
    phase_rank = {p: k for k, p in enumerate(PHASES)}
    last = 0
    for i, st in enumerate(steps):
        where = f"steps[{i}]"
        if not isinstance(st, dict):
            raise ValueError(f"{where}: step is not a mapping")
        for req in STEP_REQUIRED:
            if req not in st:
                raise ValueError(f"{where}: missing '{req}'")
        extra = sorted(set(st) - set(STEP_ALLOWED))
        if extra:
            raise ValueError(f"{where}: unknown field(s) {extra}")
        if st["phase"] not in PHASES:
            raise ValueError(f"{where}: phase {st['phase']!r} not in "
                             f"{'|'.join(PHASES)}")
        if st["station"] not in STATIONS:
            raise ValueError(f"{where}: station {st['station']!r} not in "
                             f"{'|'.join(STATIONS)}")
        if st["mode"] not in STEP_MODES:
            raise ValueError(f"{where}: mode {st['mode']!r} not in "
                             f"{'|'.join(STEP_MODES)}")
        if not isinstance(st["text"], str) or not st["text"].strip():
            raise ValueError(f"{where}: 'text' must be a non-empty string")
        d = st["duration_min"]
        if isinstance(d, bool) or not isinstance(d, (int, float)) or d <= 0:
            raise ValueError(f"{where}: duration_min must be positive "
                             f"(got {d!r})")
        has_temp = st.get("oven_temp_f") is not None
        if (st["station"] == "oven") != has_temp:
            raise ValueError(
                f"{where}: oven_temp_f is required iff station is 'oven' "
                f"(station={st['station']!r}, "
                f"oven_temp_f={st.get('oven_temp_f')!r})")
        rank = phase_rank[st["phase"]]
        if rank < last:
            raise ValueError(
                f"{where}: phase {st['phase']!r} appears after a later "
                "phase — steps must run prep -> cook -> finish")
        last = rank
    return cid, steps


def load_methods(dirpath, known_components=None, known_operations=None):
    """Load every ``*.yaml`` fragment in ``dirpath``.

    Returns ``(methods, warnings)`` where ``methods`` is
    ``{component_id: [validated step dicts]}``. Degradation contract
    (M1.10): a fragment that fails validation is SKIPPED with a structured
    ``invalid_method_fragment`` warning — its component keeps the plain
    ingredient-list rendering. A step naming an ``operation`` that does not
    resolve against ``known_operations`` keeps the step but drops the
    operation ref (``unknown_technique`` warning). A fragment for a
    component NOT in ``known_components`` is skipped silently — fragments
    are content for a specific library; against another library they are
    simply irrelevant (the fragments' own lint enforces the file-set
    contract for their corpus). Deterministic: files processed in sorted
    order."""
    methods, warnings = {}, []
    d = Path(dirpath)
    if not d.is_dir():
        _warn(warnings, "missing_methods_dir", str(d),
              f"methods directory {d} does not exist — cook plans render "
              "ingredient lists only")
        return methods, warnings
    for f in sorted(d.glob("*.yaml")):
        if known_components is not None and f.stem not in known_components:
            continue                     # content for a different library
        try:
            doc = yaml.safe_load(f.read_text())
            cid, steps = _validate_fragment(doc, f.name)
        except (OSError, yaml.YAMLError, ValueError) as e:
            _warn(warnings, "invalid_method_fragment", str(f),
                  f"{f.name}: {e} — fragment skipped; '{Path(f).stem}' "
                  "degrades to ingredient-list rendering")
            continue
        clean = []
        for i, st in enumerate(steps):
            st = dict(st)
            op = st.get("operation")
            if (op is not None and known_operations is not None
                    and op not in known_operations):
                _warn(warnings, "unknown_technique", f"{f.name}: steps[{i}]",
                      f"operation '{op}' does not resolve in the technique "
                      "library — step kept, footnote dropped")
                st.pop("operation")
            clean.append(st)
        methods[cid] = clean
    return methods, warnings


# --------------------------------------------------------------------------- #
#  ingredient identity — quantities + the shared-prep merge key
# --------------------------------------------------------------------------- #
def step_ingredient(step, comp):
    """The ONE ingredient a step handles, or None.

    Matching: each ingredient id of the component is tokenized on ``_``
    (numeric tokens dropped: ``ground_beef_85`` -> ground, beef); a token
    matches when it appears as a word prefix in the lowercased step text
    ("potato" matches "potatoes"). Exactly one matching ingredient => that
    is the step's identity; zero or several => None (a multi-ingredient
    step like "Dice the potatoes and the carrot" has no single identity and
    never merges — the spec's rule)."""
    text = (step.get("text") or "").lower()
    hits = []
    for iid in sorted(comp["ingredients"]):
        toks = [t for t in iid.lower().split("_") if t and not t.isdigit()]
        if any(re.search(r"\b" + re.escape(t), text) for t in toks):
            hits.append(iid)
    return hits[0] if len(hits) == 1 else None


def scaled_step_grams(step, comp, batches):
    """Batch-scaled grams of the step's ingredient for one dish, or None
    when the step has no single-ingredient identity."""
    iid = step_ingredient(step, comp)
    if iid is None:
        return None, None
    return iid, comp["ingredients"][iid] * batches


# --------------------------------------------------------------------------- #
#  shared-prep consolidation (PRD §4.0 amendment)
# --------------------------------------------------------------------------- #
def consolidate_shared_prep(session_cids, batches, comps, methods):
    """Merge identical prep across a session's dishes.

    Key: ``(operation, ingredient)`` — prep-phase steps only, and only
    steps carrying BOTH an operation and a single-ingredient identity
    (steps lacking either never merge). Groups spanning >= 2 distinct
    components merge into one consolidated step with per-dish allocation;
    a single dish's step stays in its own block.

    Returns ``(merged, merged_keys)``:
    - merged: sorted list of ``{operation, ingredient, total_g, station,
      mode, duration_min, parts: [{component, grams, text}]}``
    - merged_keys: ``{(component_id, step_index)}`` — the per-dish steps the
      renderer replaces with a pointer to the shared block.
    """
    groups = {}
    for cid in sorted(session_cids):
        for idx, st in enumerate(methods.get(cid) or []):
            if st.get("phase") != "prep" or not st.get("operation"):
                continue
            iid = step_ingredient(st, comps[cid])
            if iid is None:
                continue
            key = (st["operation"], iid)
            groups.setdefault(key, []).append(dict(
                component=cid, step_index=idx,
                grams=comps[cid]["ingredients"][iid] * batches.get(cid, 0),
                station=st["station"], mode=st["mode"],
                duration_min=st["duration_min"], text=st["text"]))
    merged, merged_keys = [], set()
    for op, iid in sorted(groups):
        parts = groups[(op, iid)]
        if len({p["component"] for p in parts}) < 2:
            continue
        merged.append(dict(
            operation=op, ingredient=iid,
            total_g=sum(p["grams"] for p in parts),
            station=parts[0]["station"], mode=parts[0]["mode"],
            duration_min=sum(p["duration_min"] for p in parts),
            parts=[dict(component=p["component"], grams=p["grams"],
                        text=p["text"]) for p in parts]))
        merged_keys.update((p["component"], p["step_index"]) for p in parts)
    return merged, merged_keys


# --------------------------------------------------------------------------- #
#  station summary — oven time temp-bucketed (shareable buckets, PRD §4.0)
# --------------------------------------------------------------------------- #
def station_summary(session_cids, methods):
    """One line of per-session station load, or None without step data.
    Oven minutes bucket by ``oven_temp_f`` (same-temp steps can co-reside);
    other stations split active vs passive minutes. Single-batch estimates,
    PROVISIONAL until cook-day calibration — labeled as such by the
    renderer, never asserted on."""
    oven, active, passive = {}, {}, {}
    for cid in sorted(session_cids):
        for st in methods.get(cid) or []:
            d = st.get("duration_min") or 0
            if st.get("station") == "oven":
                t = st.get("oven_temp_f")
                oven[t] = oven.get(t, 0) + d
            elif st.get("mode") == "passive":
                passive[st["station"]] = passive.get(st["station"], 0) + d
            else:
                active[st["station"]] = active.get(st["station"], 0) + d
    if not (oven or active or passive):
        return None
    parts = []
    for station in STATIONS:
        if station == "oven":
            continue
        a, p = active.get(station, 0), passive.get(station, 0)
        if not (a or p):
            continue
        bits = []
        if a:
            bits.append(f"{a:g} min active")
        if p:
            bits.append(f"{p:g} min passive")
        parts.append(f"{station} {' + '.join(bits)}")
    if oven:
        buckets = ", ".join(f"{t}°F {m:g} min"
                            for t, m in sorted(oven.items()))
        parts.append(f"oven: {buckets} — shareable buckets")
    return " · ".join(parts)
