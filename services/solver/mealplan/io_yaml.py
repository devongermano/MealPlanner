"""io_yaml.py — library load/save with schema validation and atomic writes.

M0.2: the ONE sanctioned behavior addition of the extraction. Rules:

- Validation reports ALL problems in one structured error — never
  first-error-wins.
- Loads validate before the engine sees anything.
- Saves validate first, write to a temp file in the target directory, then
  atomically rename over the destination. An invalid save is refused entirely
  and leaves the original file untouched.
- Every document carries ``schema_version`` (currently only version 1 is
  known).
"""

from __future__ import annotations

import datetime
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from .model import (COOK_PLAN_STYLES, PERSON_MODES, SERVING_MODELS,
                    Ingredient, Pantry, Person, Settings, derive_component,
                    resolve_meal_slots)

SCHEMA_VERSION = 1
KNOWN_SCHEMA_VERSIONS = (1,)

ROLES = ("main", "starch", "veg", "accent", "drink")
BUDGET_MODES = ("shared", "per_person", "by_consumption", "off")

INGREDIENT_REQUIRED = ("p", "f", "c", "perishable", "pack_g",
                       "keeps_days", "cost")
COMPONENT_REQUIRED = ("id", "name", "cuisine", "role", "yield_g", "serve_g",
                      "keeps_days", "active_min", "ingredients")
PERSON_REQUIRED = ("targets", "tolerance")


# --------------------------------------------------------------------------- #
#  structured all-errors reporting
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ValidationIssue:
    """One problem, precisely located."""

    code: str        # machine-readable: unknown_ingredient, nonpositive_grams, …
    where: str       # e.g. "components.yaml: component 'picadillo', ingredient 'tofu'"
    message: str     # human-readable
    severity: str = "error"   # "error" blocks the load/save; "warning" is reported only

    def __str__(self):
        return f"[{self.severity}:{self.code}] {self.where}: {self.message}"


class ValidationError(Exception):
    """Carries EVERY issue found, not just the first."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = list(issues)
        self.errors = [i for i in self.issues if i.severity == "error"]
        self.warnings = [i for i in self.issues if i.severity == "warning"]
        super().__init__(
            f"{len(self.errors)} validation error(s):\n"
            + "\n".join(f"  - {i}" for i in self.issues))

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}


def _split(issues: list[ValidationIssue]):
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    return errors, warnings


def _report_warnings(warnings: list[ValidationIssue]) -> None:
    import sys
    for w in warnings:
        print(str(w), file=sys.stderr)


# --------------------------------------------------------------------------- #
#  per-document validators — each returns a list of issues, never raises early
# --------------------------------------------------------------------------- #
def _check_schema_version(doc: Any, fname: str) -> list[ValidationIssue]:
    out = []
    if not isinstance(doc, dict):
        return [ValidationIssue("bad_document", fname,
                                "document is not a mapping")]
    if "schema_version" not in doc:
        out.append(ValidationIssue(
            "missing_field", f"{fname}: schema_version",
            "document must declare a schema_version"))
    elif doc["schema_version"] not in KNOWN_SCHEMA_VERSIONS:
        out.append(ValidationIssue(
            "unknown_schema_version", f"{fname}: schema_version",
            f"unknown schema_version {doc['schema_version']!r}; "
            f"known: {list(KNOWN_SCHEMA_VERSIONS)}"))
    return out


def validate_ingredients_doc(doc: Any, fname: str = "ingredients.yaml"
                             ) -> list[ValidationIssue]:
    issues = _check_schema_version(doc, fname)
    if not isinstance(doc, dict):
        return issues
    ing = doc.get("ingredients")
    if not isinstance(ing, dict):
        issues.append(ValidationIssue(
            "missing_field", f"{fname}: ingredients",
            "top-level 'ingredients' mapping is required"))
        return issues
    for iid, d in ing.items():
        where = f"{fname}: ingredient '{iid}'"
        if not isinstance(d, dict):
            issues.append(ValidationIssue("bad_document", where,
                                          "entry is not a mapping"))
            continue
        for f_ in INGREDIENT_REQUIRED:
            if f_ not in d:
                issues.append(ValidationIssue(
                    "missing_field", f"{where}, field '{f_}'",
                    f"required field '{f_}' is missing"))
        # numeric fields must actually BE numbers — a stray string ("high",
        # "30g") would otherwise sail through validation and crash the
        # derivation with a raw TypeError (violating the all-errors contract)
        for f_ in ("p", "f", "c", "cost", "keeps_days"):
            v = d.get(f_)
            if f_ in d and (not isinstance(v, (int, float))
                            or isinstance(v, bool)):
                issues.append(ValidationIssue(
                    "bad_number", f"{where}, field '{f_}'",
                    f"'{f_}' must be a number (got {v!r})"))
        # kcal is never stored (M0.9) — it derives from macros, always
        if "kcal" in d:
            issues.append(ValidationIssue(
                "forbidden_field", f"{where}, field 'kcal'",
                "ingredient kcal is not stored; kcal derives from macros "
                "(Atwater 4/9/4)"))
        # all-zero macros are a data-entry smell unless flagged intentional
        macros = [d.get(k) for k in ("p", "f", "c")]
        if (all(isinstance(v, (int, float)) and not isinstance(v, bool)
                for v in macros)
                and not any(macros) and not d.get("negligible")):
            issues.append(ValidationIssue(
                "all_zero_macros", where,
                "all macros are zero; set 'negligible: true' if intentional, "
                "otherwise fill in real values", severity="warning"))
        ef = d.get("edible_fraction")
        if ef is not None and (not isinstance(ef, (int, float))
                               or isinstance(ef, bool) or not 0 < ef <= 1):
            issues.append(ValidationIssue(
                "bad_edible_fraction", f"{where}, field 'edible_fraction'",
                f"edible_fraction must be a number in (0, 1] (got {ef!r})"))
        fz = d.get("freezable")
        if fz is not None and not isinstance(fz, bool):
            issues.append(ValidationIssue(
                "bad_freezable", f"{where}, field 'freezable'",
                f"freezable must be a boolean (got {fz!r})"))
        pg = d.get("pack_g")
        if "pack_g" in d:
            if not isinstance(pg, (int, float)) or isinstance(pg, bool):
                issues.append(ValidationIssue(
                    "bad_number", f"{where}, field 'pack_g'",
                    f"pack_g must be a number (got {pg!r})"))
            elif pg <= 0:
                issues.append(ValidationIssue(
                    "nonpositive_grams", f"{where}, field 'pack_g'",
                    f"pack_g must be > 0 (got {pg})"))
    return issues


def validate_components_doc(doc: Any, known_ingredients: Optional[set] = None,
                            fname: str = "components.yaml"
                            ) -> list[ValidationIssue]:
    issues = _check_schema_version(doc, fname)
    if not isinstance(doc, dict):
        return issues
    comps = doc.get("components")
    if not isinstance(comps, list):
        issues.append(ValidationIssue(
            "missing_field", f"{fname}: components",
            "top-level 'components' list is required"))
        return issues
    for idx, c in enumerate(comps):
        if not isinstance(c, dict):
            issues.append(ValidationIssue(
                "bad_document", f"{fname}: components[{idx}]",
                "entry is not a mapping"))
            continue
        cid = c.get("id", f"components[{idx}]")
        where = f"{fname}: component '{cid}'"
        for f_ in COMPONENT_REQUIRED:
            if f_ not in c:
                issues.append(ValidationIssue(
                    "missing_field", f"{where}, field '{f_}'",
                    f"required field '{f_}' is missing"))
        role = c.get("role")
        if role is not None and role not in ROLES:
            issues.append(ValidationIssue(
                "bad_enum", f"{where}, field 'role'",
                f"role must be one of {'|'.join(ROLES)} (got {role!r})"))
        # batch_g was removed from the schema in M0.5 — nothing ever read it
        if "batch_g" in c:
            issues.append(ValidationIssue(
                "removed_field", f"{where}, field 'batch_g'",
                "'batch_g' was removed in schema v1; no engine consumed "
                "them — delete the field"))
        fz = c.get("freezes")
        if fz is not None and not isinstance(fz, bool):
            issues.append(ValidationIssue(
                "bad_freezes", f"{where}, field 'freezes'",
                f"freezes must be a boolean (got {fz!r})"))
        # tags are DERIVED from ingredients — declaring them by hand is forbidden
        if "tags" in c:
            issues.append(ValidationIssue(
                "forbidden_field", f"{where}, field 'tags'",
                "tags are derived from ingredient tags and must never be "
                "declared on a component"))
        # numeric fields must actually BE numbers (see ingredients — same
        # all-errors contract: report here, never crash the engine later)
        for f_ in ("yield_g", "active_min", "keeps_days"):
            v = c.get(f_)
            if f_ in c and (not isinstance(v, (int, float))
                            or isinstance(v, bool)):
                issues.append(ValidationIssue(
                    "bad_number", f"{where}, field '{f_}'",
                    f"'{f_}' must be a number (got {v!r})"))
        yg = c.get("yield_g")
        if isinstance(yg, (int, float)) and not isinstance(yg, bool) and yg <= 0:
            issues.append(ValidationIssue(
                "nonpositive_grams", f"{where}, field 'yield_g'",
                f"yield_g must be > 0 (got {yg})"))
        # unit_g (optional): must be a positive number when present. M0.8's
        # snap-and-clamp grid guarantee leans on the unit-alignment check
        # below, which can only run against a positive numeric unit_g — a
        # string or nonpositive value must be an ERROR, not a silent skip.
        ug = c.get("unit_g")
        if "unit_g" in c and (not isinstance(ug, (int, float))
                              or isinstance(ug, bool) or ug <= 0):
            issues.append(ValidationIssue(
                "bad_unit_g", f"{where}, field 'unit_g'",
                f"unit_g must be a positive number (got {ug!r})"))
        # household_unit (optional, M1.2 PRD §4.1): {"name": str, "grams": >0}
        # — the relaxed-mode rendering unit ("cup", "scoop"). Shape-validated
        # here; the eat-sheet renderer (artifacts.py) is the consumer.
        hu = c.get("household_unit")
        if hu is not None:
            hwhere = f"{where}, field 'household_unit'"
            if not isinstance(hu, dict):
                issues.append(ValidationIssue(
                    "bad_household_unit", hwhere,
                    "household_unit must be a mapping with 'name' and "
                    f"'grams' (got {hu!r})"))
            else:
                hname = hu.get("name")
                if not isinstance(hname, str) or not hname.strip():
                    issues.append(ValidationIssue(
                        "bad_household_unit", f"{hwhere}, 'name'",
                        f"household_unit name must be a non-empty string "
                        f"(got {hname!r})"))
                hg = hu.get("grams")
                if (not isinstance(hg, (int, float)) or isinstance(hg, bool)
                        or hg <= 0):
                    issues.append(ValidationIssue(
                        "bad_household_unit", f"{hwhere}, 'grams'",
                        f"household_unit grams must be a positive number "
                        f"(got {hg!r})"))
                extra = sorted(set(hu) - {"name", "grams"})
                if extra:
                    issues.append(ValidationIssue(
                        "bad_household_unit", hwhere,
                        f"unexpected household_unit field(s) {extra}; "
                        "only 'name' and 'grams' are allowed"))
        # pairs_with (optional, M1.9): accent affinity — the meal dealer
        # attaches an accent to the meal whose slot-main appears in its
        # pairs_with list (falling back to same-cuisine). Shape-validated
        # here; ids must be non-empty strings; references to components not
        # in this document are a WARNING (the affinity silently degrades to
        # the cuisine fallback — say so at load, not never).
        pw = c.get("pairs_with")
        if pw is not None:
            pwhere = f"{where}, field 'pairs_with'"
            if (not isinstance(pw, list)
                    or not all(isinstance(x, str) and x.strip() for x in pw)):
                issues.append(ValidationIssue(
                    "bad_pairs_with", pwhere,
                    f"pairs_with must be a list of component ids "
                    f"(got {pw!r})"))
            else:
                all_ids = {x.get("id") for x in comps if isinstance(x, dict)}
                unknown = sorted(set(pw) - all_ids)
                if unknown:
                    issues.append(ValidationIssue(
                        "pairs_with_unknown_component", pwhere,
                        f"pairs_with references unknown component(s) "
                        f"{unknown} — the affinity falls back to "
                        "same-cuisine for those", severity="warning"))
        sg = c.get("serve_g")
        if sg is not None:
            if not isinstance(sg, dict) or "min" not in sg or "max" not in sg:
                issues.append(ValidationIssue(
                    "missing_field", f"{where}, field 'serve_g'",
                    "serve_g must be a mapping with 'min' and 'max'"))
            else:
                lo, hi = sg.get("min"), sg.get("max")
                for k_, v_ in (("min", lo), ("max", hi)):
                    if not isinstance(v_, (int, float)) or isinstance(v_, bool):
                        issues.append(ValidationIssue(
                            "bad_number", f"{where}, field 'serve_g.{k_}'",
                            f"serve_g.{k_} must be a number (got {v_!r})"))
                if (isinstance(lo, (int, float)) and isinstance(hi, (int, float))
                        and lo > hi):
                    issues.append(ValidationIssue(
                        "serve_bounds_inverted", f"{where}, field 'serve_g'",
                        f"serve_g min ({lo}) exceeds max ({hi})"))
                ug = c.get("unit_g")
                if (isinstance(ug, (int, float)) and not isinstance(ug, bool)
                        and ug > 0):
                    mis = [k for k in ("min", "max")
                           if isinstance(sg.get(k), (int, float))
                           and sg[k] % ug != 0]
                    if mis:
                        # ERROR since M0.8: the plate LP's snap-and-clamp can
                        # only guarantee unit multiples if the bounds
                        # themselves sit on the unit grid.
                        issues.append(ValidationIssue(
                            "serve_bounds_not_unit_aligned",
                            f"{where}, field 'serve_g'",
                            f"serve_g {'/'.join(mis)} must be a multiple of "
                            f"unit_g={ug}"))
        ings = c.get("ingredients")
        if isinstance(ings, dict):
            for iname, grams in ings.items():
                iwhere = f"{where}, ingredient '{iname}'"
                if known_ingredients is not None and iname not in known_ingredients:
                    issues.append(ValidationIssue(
                        "unknown_ingredient", iwhere,
                        f"component '{cid}' references unknown ingredient "
                        f"'{iname}'"))
                if not isinstance(grams, (int, float)) or isinstance(grams, bool):
                    issues.append(ValidationIssue(
                        "bad_grams", iwhere,
                        f"grams must be a number (got {grams!r})"))
                elif grams <= 0:
                    issues.append(ValidationIssue(
                        "nonpositive_grams", iwhere,
                        f"grams must be > 0 (got {grams})"))
        elif ings is not None:
            issues.append(ValidationIssue(
                "bad_document", f"{where}, field 'ingredients'",
                "ingredients must be a mapping of ingredient -> grams"))
    return issues


def validate_people_doc(doc: Any, fname: str = "people.yaml"
                        ) -> list[ValidationIssue]:
    issues = _check_schema_version(doc, fname)
    if not isinstance(doc, dict):
        return issues
    people = doc.get("people")
    if not isinstance(people, dict):
        issues.append(ValidationIssue(
            "missing_field", f"{fname}: people",
            "top-level 'people' mapping is required"))
    else:
        for pname, p in people.items():
            where = f"{fname}: person '{pname}'"
            if not isinstance(p, dict):
                issues.append(ValidationIssue("bad_document", where,
                                              "entry is not a mapping"))
                continue
            for f_ in PERSON_REQUIRED:
                # M1.2: tolerance is optional for a relaxed person — their
                # effective tolerance defaults to model.RELAXED_TOLERANCE.
                if f_ == "tolerance" and p.get("mode") == "relaxed":
                    continue
                if f_ not in p:
                    issues.append(ValidationIssue(
                        "missing_field", f"{where}, field '{f_}'",
                        f"required field '{f_}' is missing"))
            # M1.2 (PRD §4.1): mode is presentation + tolerance default only
            pm = p.get("mode")
            if pm is not None and pm not in PERSON_MODES:
                issues.append(ValidationIssue(
                    "bad_enum", f"{where}, field 'mode'",
                    f"mode must be one of {'|'.join(PERSON_MODES)} "
                    f"(got {pm!r})"))
            tgt = p.get("targets")
            if isinstance(tgt, dict):
                for mac in ("protein", "fat", "carb"):
                    if mac not in tgt:
                        issues.append(ValidationIssue(
                            "missing_field", f"{where}, targets.{mac}",
                            f"daily target for '{mac}' is missing"))
            # M1.0: tolerance must be a number in (0, 0.5] — the elastic
            # macro band is a fraction of the target; 0 or negative makes
            # the LP band degenerate/inverted, and anything past 50% is a
            # config error, not a plan (loosening tolerance is already the
            # labeled last resort, PRD §8.3).
            tv = p.get("tolerance")
            if tv is not None and (not isinstance(tv, (int, float))
                                   or isinstance(tv, bool)
                                   or not 0 < tv <= 0.5):
                issues.append(ValidationIssue(
                    "bad_tolerance", f"{where}, field 'tolerance'",
                    f"tolerance must be a number in (0, 0.5] (got {tv!r})"))
            # meals_per_day is LIVE since M1.9 (PRD §4.0): the post-solve
            # dealer deals each solved day into n composed meals.
            mpd = p.get("meals_per_day")
            if mpd is not None and (not isinstance(mpd, int)
                                    or isinstance(mpd, bool) or mpd < 1):
                issues.append(ValidationIssue(
                    "bad_meals_per_day", f"{where}, field 'meals_per_day'",
                    f"meals_per_day must be an integer >= 1 (got {mpd!r})"))
            # M1.9 (PRD §4.0 amendments): serving_model enum per person;
            # meal_slots list with per-slot serving_model / interchangeable.
            sm = p.get("serving_model")
            if sm is not None and sm not in SERVING_MODELS:
                issues.append(ValidationIssue(
                    "bad_enum", f"{where}, field 'serving_model'",
                    f"serving_model must be one of "
                    f"{'|'.join(SERVING_MODELS)} (got {sm!r})"))
            ms = p.get("meal_slots")
            if ms is not None:
                mwhere = f"{where}, field 'meal_slots'"
                if not isinstance(ms, list) or not ms:
                    issues.append(ValidationIssue(
                        "bad_meal_slots", mwhere,
                        f"meal_slots must be a non-empty list of slot "
                        f"mappings (got {ms!r})"))
                else:
                    names = []
                    for k, s in enumerate(ms):
                        swhere = f"{mwhere}[{k}]"
                        if not isinstance(s, dict):
                            issues.append(ValidationIssue(
                                "bad_meal_slots", swhere,
                                f"slot must be a mapping with 'name' "
                                f"(got {s!r})"))
                            continue
                        nm = s.get("name")
                        if not isinstance(nm, str) or not nm.strip():
                            issues.append(ValidationIssue(
                                "bad_meal_slots", f"{swhere}, 'name'",
                                f"slot name must be a non-empty string "
                                f"(got {nm!r})"))
                        else:
                            names.append(nm)
                        ssm = s.get("serving_model")
                        if ssm is not None and ssm not in SERVING_MODELS:
                            issues.append(ValidationIssue(
                                "bad_enum", f"{swhere}, 'serving_model'",
                                f"slot serving_model must be one of "
                                f"{'|'.join(SERVING_MODELS)} (got {ssm!r})"))
                        ic = s.get("interchangeable")
                        if ic is not None and not isinstance(ic, bool):
                            issues.append(ValidationIssue(
                                "bad_meal_slots", f"{swhere}, "
                                "'interchangeable'",
                                f"interchangeable must be a boolean "
                                f"(got {ic!r})"))
                        extra = sorted(set(s) - {"name", "serving_model",
                                                 "interchangeable"})
                        if extra:
                            issues.append(ValidationIssue(
                                "bad_meal_slots", swhere,
                                f"unexpected slot field(s) {extra}; only "
                                "'name', 'serving_model' and "
                                "'interchangeable' are allowed"))
                    dupes = sorted({n for n in names if names.count(n) > 1})
                    if dupes:
                        issues.append(ValidationIssue(
                            "bad_meal_slots", mwhere,
                            f"duplicate slot name(s) {dupes} — slot names "
                            "must be unique"))
                    # both set → the counts must agree (all-errors, exit 3)
                    if (isinstance(mpd, int) and not isinstance(mpd, bool)
                            and mpd >= 1 and len(ms) != mpd):
                        issues.append(ValidationIssue(
                            "meal_slot_count_mismatch", mwhere,
                            f"meal_slots lists {len(ms)} slot(s) but "
                            f"meals_per_day is {mpd} — the counts must "
                            "agree (or set only one of the two)"))
            # removed in M0.5 — nothing ever read them
            for f_ in ("min_components_per_day", "max_components_per_day"):
                if f_ in p:
                    issues.append(ValidationIssue(
                        "removed_field", f"{where}, field '{f_}'",
                        f"'{f_}' was removed in schema v1; no engine "
                        "consumed them — delete the field"))
    st = doc.get("settings")
    if not isinstance(st, dict):
        issues.append(ValidationIssue(
            "missing_field", f"{fname}: settings",
            "top-level 'settings' mapping is required"))
    else:
        # M0.17: cook_days is REQUIRED — sessions are data, and the
        # prototype's silent [0, 3] code fallback is gone.
        cd = st.get("cook_days")
        cwhere = f"{fname}: settings, field 'cook_days'"
        if cd is None:
            issues.append(ValidationIssue(
                "missing_field", cwhere,
                "required field 'cook_days' is missing — list the 0-indexed "
                "cook-session start days (e.g. [0, 4]); there is no default"))
        elif (not isinstance(cd, list) or not cd
              or not all(isinstance(x, int) and not isinstance(x, bool)
                         for x in cd)):
            issues.append(ValidationIssue(
                "bad_cook_days", cwhere,
                "cook_days must be a non-empty list of 0-indexed day "
                f"indices (got {cd!r})"))
        else:
            days = st.get("days")
            hi = days if isinstance(days, int) else None
            bad = [x for x in cd
                   if x < 0 or (hi is not None and x >= hi)]
            if bad:
                issues.append(ValidationIssue(
                    "cook_day_out_of_range", cwhere,
                    f"cook day(s) {bad} outside the plan week "
                    f"[0, {hi if hi is not None else '?'})"))
        # M1.0 follow-up: the other two engine-indexed settings get the same
        # required-validation posture as cook_days — a load-time structured
        # error beats a KeyError mid-solve ("every corpus declares it" is a
        # hope, not a guarantee).
        for req, kind, msg in (
            ("active_min_budget", (int, float),
             "required field 'active_min_budget' is missing — total hands-on "
             "cooking minutes available per week; there is no default"),
            ("max_days_same_component", int,
             "required field 'max_days_same_component' is missing — variety "
             "cap for mains (days per week one main may appear); no default"),
        ):
            val = st.get(req)
            rwhere = f"{fname}: settings, field '{req}'"
            if val is None:
                issues.append(ValidationIssue("missing_field", rwhere, msg))
            elif (not isinstance(val, kind) or isinstance(val, bool)
                  or val <= 0):
                issues.append(ValidationIssue(
                    f"bad_{req}", rwhere,
                    f"{req} must be a positive number (got {val!r})"))
        # M0.6: shopping trips are data. shop_days (optional, default [0]):
        # at least one day index, every one inside the plan week.
        sd = st.get("shop_days")
        if sd is not None:
            where = f"{fname}: settings, field 'shop_days'"
            if (not isinstance(sd, list) or not sd
                    or not all(isinstance(x, int) and not isinstance(x, bool)
                               for x in sd)):
                issues.append(ValidationIssue(
                    "bad_shop_days", where,
                    "shop_days must be a non-empty list of 0-indexed day "
                    f"indices (got {sd!r})"))
            else:
                days = st.get("days")
                hi = days if isinstance(days, int) else None
                bad = [x for x in sd
                       if x < 0 or (hi is not None and x >= hi)]
                if bad:
                    issues.append(ValidationIssue(
                        "shop_day_out_of_range", where,
                        f"shop day(s) {bad} outside the plan week "
                        f"[0, {hi if hi is not None else '?'})"))
        # M0.5: freezer-bridging availability toggle (default true)
        uf = st.get("use_freezer")
        if uf is not None and not isinstance(uf, bool):
            issues.append(ValidationIssue(
                "bad_use_freezer", f"{fname}: settings, field 'use_freezer'",
                f"use_freezer must be a boolean (got {uf!r})"))
        # M1.10 (PRD §4.0 amendment): cook-plan style preference. "timeline"
        # is accepted (it is a first-class PRD option) but its scheduler is
        # M1.12 — until then it renders recipe blocks with an explicit note.
        cps = st.get("cook_plan_style")
        if cps is not None and cps not in COOK_PLAN_STYLES:
            issues.append(ValidationIssue(
                "bad_enum", f"{fname}: settings, field 'cook_plan_style'",
                f"cook_plan_style must be one of "
                f"{'|'.join(COOK_PLAN_STYLES)} (got {cps!r})"))
    # budget (optional; defaults to {"mode": "off"} at load). mode is an
    # enum; 'by_consumption' means NO ceiling — cost splits by consumption
    # share (costing.attribute), which render applies to every mode anyway.
    # 'period' is RESERVED (model.RESERVED_FIELDS): only 'week' is a known
    # value — M0/M1 plans are weekly by construction.
    bud = doc.get("budget")
    if bud is not None:
        bwhere = f"{fname}: budget"
        if not isinstance(bud, dict):
            issues.append(ValidationIssue(
                "bad_document", bwhere,
                f"budget must be a mapping (got {type(bud).__name__})"))
        else:
            mode = bud.get("mode", "off")
            if mode not in BUDGET_MODES:
                issues.append(ValidationIssue(
                    "bad_enum", f"{bwhere}, field 'mode'",
                    f"budget mode must be one of {'|'.join(BUDGET_MODES)} "
                    f"(got {mode!r})"))
            period = bud.get("period")
            if period is not None and period != "week":
                issues.append(ValidationIssue(
                    "bad_enum", f"{bwhere}, field 'period'",
                    f"budget period must be 'week' — plans are weekly "
                    f"(got {period!r})"))
    return issues


# --------------------------------------------------------------------------- #
#  pantry (M0.12, PRD §8.1) — schema + empty-state semantics
# --------------------------------------------------------------------------- #
PANTRY_STOCK_REQUIRED = ("ingredient", "grams", "acquired")
PANTRY_COOKED_REQUIRED = ("component", "grams", "cooked")


def _check_date(value: Any) -> bool:
    """Is ``value`` a parseable ISO date? yaml.safe_load already turns bare
    ISO dates into datetime.date; strings must be ISO-parseable."""
    if isinstance(value, datetime.datetime):
        return True
    if isinstance(value, datetime.date):
        return True
    if isinstance(value, str):
        try:
            datetime.date.fromisoformat(value)
            return True
        except ValueError:
            return False
    return False


def validate_pantry_doc(doc: Any, known_ingredients: Optional[set] = None,
                        known_components: Optional[set] = None,
                        fname: str = "pantry.yaml") -> list[ValidationIssue]:
    """All-errors validation of a pantry document.

    Schema: {schema_version: 1, stock: [{ingredient, grams, acquired}],
    cooked: [{component, grams, cooked}]}. Both lists are optional — an
    empty pantry is a valid state (PRD §8.1). Every entry needs a known
    reference, grams > 0, and a parseable ISO date.
    """
    issues = _check_schema_version(doc, fname)
    if not isinstance(doc, dict):
        return issues
    for key, required, ref_field, known, unknown_code in (
            ("stock", PANTRY_STOCK_REQUIRED, "ingredient",
             known_ingredients, "unknown_ingredient"),
            ("cooked", PANTRY_COOKED_REQUIRED, "component",
             known_components, "unknown_component")):
        rows = doc.get(key)
        if rows is None:
            continue                       # absent list == empty pantry
        if not isinstance(rows, list):
            issues.append(ValidationIssue(
                "bad_document", f"{fname}: {key}",
                f"'{key}' must be a list of entries (got {type(rows).__name__})"))
            continue
        date_field = required[2]
        for idx, r in enumerate(rows):
            where = f"{fname}: {key}[{idx}]"
            if not isinstance(r, dict):
                issues.append(ValidationIssue("bad_document", where,
                                              "entry is not a mapping"))
                continue
            ref = r.get(ref_field)
            if isinstance(ref, str):
                where = f"{fname}: {key}[{idx}] ('{ref}')"
            for f_ in required:
                if f_ not in r:
                    issues.append(ValidationIssue(
                        "missing_field", f"{where}, field '{f_}'",
                        f"required field '{f_}' is missing"))
            if (isinstance(ref, str) and known is not None
                    and ref not in known):
                issues.append(ValidationIssue(
                    unknown_code, f"{where}, field '{ref_field}'",
                    f"{key} entry references unknown {ref_field} '{ref}'"))
            g = r.get("grams")
            if g is not None:
                if not isinstance(g, (int, float)) or isinstance(g, bool):
                    issues.append(ValidationIssue(
                        "bad_grams", f"{where}, field 'grams'",
                        f"grams must be a number (got {g!r})"))
                elif g <= 0:
                    issues.append(ValidationIssue(
                        "nonpositive_grams", f"{where}, field 'grams'",
                        f"grams must be > 0 (got {g})"))
            dv = r.get(date_field)
            if dv is not None and not _check_date(dv):
                issues.append(ValidationIssue(
                    "bad_date", f"{where}, field '{date_field}'",
                    f"'{date_field}' must be an ISO date (got {dv!r})"))
    return issues


VALIDATORS = {
    "ingredients": lambda doc, **kw: validate_ingredients_doc(doc, **kw),
    "components": lambda doc, **kw: validate_components_doc(doc, **kw),
    "people": lambda doc, **kw: validate_people_doc(doc, **kw),
    "pantry": lambda doc, **kw: validate_pantry_doc(doc, **kw),
}


# --------------------------------------------------------------------------- #
#  load
# --------------------------------------------------------------------------- #
def load(library: str | os.PathLike):
    """Load a library directory (ingredients.yaml, components.yaml,
    people.yaml), validating everything first and reporting every problem in
    one ValidationError. Returns ``(ing, comps, people, settings)`` shaped
    exactly like the prototype's ``plan.load()``.
    """
    docs = load_raw_docs(library)
    return load_docs(docs["ingredients"], docs["components"],
                     docs["people"])


def load_raw_docs(library: str | os.PathLike) -> dict:
    """Read the three library YAML files as RAW parsed documents (no
    validation beyond YAML parse). Used by ``load`` and by the M1.3 locked
    plan artifact, whose inputs snapshot embeds these documents verbatim.
    Returns ``{"ingredients": doc, "components": doc, "people": doc}``."""
    lib = Path(library)
    docs, issues = {}, []
    for fname in ("ingredients.yaml", "components.yaml", "people.yaml"):
        path = lib / fname
        kind = fname.split(".")[0]
        if not path.exists():
            issues.append(ValidationIssue(
                "missing_file", str(path), "library file not found"))
            docs[kind] = None
            continue
        try:
            docs[kind] = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            issues.append(ValidationIssue(
                "bad_yaml", str(path), f"YAML parse error: {e}"))
            docs[kind] = None
    if any(d is None for d in docs.values()):
        raise ValidationError(issues)
    return docs


def load_docs(ing_doc, comp_doc, ppl_doc):
    """Validate + build ``(ing, comps, people, settings)`` from in-memory
    documents — the same validation and construction path as ``load``.
    Split out for M1.3 verify-plan, which re-solves from the library
    documents embedded in a locked plan's snapshot rather than from files.
    """
    issues = []
    issues += validate_ingredients_doc(ing_doc)
    known = set((ing_doc.get("ingredients") or {})
                if isinstance(ing_doc, dict) else ())
    issues += validate_components_doc(comp_doc, known_ingredients=known)
    issues += validate_people_doc(ppl_doc)
    errors, warnings = _split(issues)
    if errors:
        raise ValidationError(issues)
    _report_warnings(warnings)

    ing = {iid: Ingredient.from_raw(iid, d)
           for iid, d in ing_doc["ingredients"].items()}
    comps = {}
    for c in comp_doc["components"]:
        comps[c["id"]] = derive_component(c, ing)
    people = {pn: Person.from_raw(pn, d)
              for pn, d in ppl_doc["people"].items()}
    # prototype behavior: budget rides along inside settings
    settings = Settings.from_raw(ppl_doc["settings"],
                                 ppl_doc.get("budget", {"mode": "off"}))
    _report_warnings(meal_side_mass_issues(comps, people))
    return ing, comps, people, settings


def meal_side_mass_issues(comps, people) -> list[ValidationIssue]:
    """M1.9 load-time warning (M19_SPEC §2.5): a person with a multi-meal
    structure but ZERO splittable side mass in their eligible library (no
    starch/veg they can eat, or roles misauthored all-main) will see every
    meal flagged by the dealer — name the fix at load, before anyone sees a
    flagged week. Needs DERIVED component tags, hence post-construction
    (called from load_docs, not from validate_people_doc)."""
    out = []
    for pn, p in people.items():
        slots = resolve_meal_slots(p)
        if not slots or len(slots) < 2:
            continue
        excl = set(p.get("exclude") or [])
        splittable = [cid for cid, c in comps.items()
                      if c["role"] in ("starch", "veg")
                      and not (set(c["tags"]) & excl)]
        if not splittable:
            out.append(ValidationIssue(
                "meal_side_mass_missing", f"people.yaml: person '{pn}'",
                f"'{pn}' has {len(slots)} meals per day but the library has "
                "no starch/veg component they can eat — every meal will be "
                "flagged; add an eligible side dish (or fix misauthored "
                "roles)", severity="warning"))
    return out


def load_pantry(path: str | os.PathLike,
                known_ingredients: Optional[set] = None,
                known_components: Optional[set] = None) -> Pantry:
    """Load and validate an OPTIONAL pantry.yaml (M0.12, PRD §8.1).

    The pantry is optional at the call site — callers with no pantry simply
    never call this (purchase(pantry=None) is the no-op empty state). A path
    that IS given must exist and validate; every problem is reported in one
    ValidationError.
    """
    path = Path(path)
    if not path.exists():
        raise ValidationError([ValidationIssue(
            "missing_file", str(path), "pantry file not found")])
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ValidationError([ValidationIssue(
            "bad_yaml", str(path), f"YAML parse error: {e}")])
    issues = validate_pantry_doc(doc, known_ingredients=known_ingredients,
                                 known_components=known_components,
                                 fname=path.name)
    errors, warnings = _split(issues)
    if errors:
        raise ValidationError(issues)
    _report_warnings(warnings)
    return Pantry.from_raw(doc)


# --------------------------------------------------------------------------- #
#  atomic validated save
# --------------------------------------------------------------------------- #
def save(path: str | os.PathLike, doc: dict, kind: str,
         known_ingredients: Optional[set] = None) -> None:
    """Validate ``doc`` as a ``kind`` document ('ingredients' | 'components' |
    'people') and atomically write it to ``path``.

    Refuses invalid documents entirely: validate → write temp file in the same
    directory → atomic rename. A failed validation never touches the existing
    file.
    """
    if kind not in VALIDATORS:
        raise ValueError(f"unknown document kind {kind!r}; "
                         f"expected one of {sorted(VALIDATORS)}")
    path = Path(path)
    kwargs = {"fname": path.name}
    if kind == "components":
        kwargs["known_ingredients"] = known_ingredients
    issues = VALIDATORS[kind](doc, **kwargs)
    errors, warnings = _split(issues)
    if errors:
        raise ValidationError(issues)
    _report_warnings(warnings)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False)
        os.replace(tmp, path)     # atomic on POSIX
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
