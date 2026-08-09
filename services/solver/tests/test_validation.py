"""M0.2 schema validation tests.

Every rejection produces a structured, all-errors result with exact locations;
atomic writes never leave a partial or corrupted file behind.
"""

from pathlib import Path

import pytest
import yaml

from mealplan import io_yaml
from mealplan.io_yaml import (ValidationError, save, validate_components_doc,
                              validate_people_doc)

ING_DOC = {
    "schema_version": 1,
    "ingredients": {
        "chicken": {"p": 22.5, "f": 2.6, "c": 0.0,
                    "perishable": True, "pack_g": 907, "keeps_days": 3,
                    "tags": [], "cost": 9.0},
        "rice": {"p": 7.1, "f": 0.7, "c": 80.0,
                 "perishable": False, "pack_g": 907, "keeps_days": 999,
                 "tags": [], "cost": 3.5},
    },
}

PPL_DOC = {
    "schema_version": 1,
    "people": {
        "p1": {"targets": {"protein": 150, "fat": 80, "carb": 300},
               "tolerance": 0.05},
    },
    "settings": {"days": 7, "active_min_budget": 180,
                 "max_days_same_component": 4, "cook_days": [0, 4]},
}

KNOWN = set(ING_DOC["ingredients"])


def comp(cid, **over):
    d = {"id": cid, "name": cid, "cuisine": "test", "role": "main",
         "yield_g": 1000, "serve_g": {"min": 80, "max": 400},
         "keeps_days": 5, "active_min": 10,
         "ingredients": {"chicken": 500, "rice": 500}}
    d.update(over)
    return d


def comp_doc(*comps_):
    return {"schema_version": 1, "components": list(comps_)}


def errors_of(issues):
    return [i for i in issues if i.severity == "error"]


def warnings_of(issues):
    return [i for i in issues if i.severity == "warning"]


# --------------------------------------------------------------------------- #
#  all-errors reporting: three errors at once, all three reported
# --------------------------------------------------------------------------- #
def test_three_errors_reported_together(tmp_path):
    doc = comp_doc(
        comp("a", ingredients={"ghost_meat": 100, "rice": 200}),  # unknown ref
        comp("b", tags=["dairy"]),                                # declared tags
        comp("c", role="snack"),                                  # bad role enum
    )
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(ING_DOC))
    (lib / "components.yaml").write_text(yaml.safe_dump(doc))
    (lib / "people.yaml").write_text(yaml.safe_dump(PPL_DOC))

    with pytest.raises(ValidationError) as ei:
        io_yaml.load(lib)
    errs = ei.value.errors
    assert len(errs) == 3, [str(e) for e in errs]
    by_code = {e.code: e for e in errs}
    assert set(by_code) == {"unknown_ingredient", "forbidden_field", "bad_enum"}
    # exact locations: file, entry id, field — all present in `where`
    assert "components.yaml" in by_code["unknown_ingredient"].where
    assert "'a'" in by_code["unknown_ingredient"].where
    assert "ghost_meat" in by_code["unknown_ingredient"].where
    assert "'b'" in by_code["forbidden_field"].where
    assert "tags" in by_code["forbidden_field"].where
    assert "'c'" in by_code["bad_enum"].where
    assert "role" in by_code["bad_enum"].where


# --------------------------------------------------------------------------- #
#  individual rejection cases
# --------------------------------------------------------------------------- #
def test_rejects_zero_and_negative_grams():
    doc = comp_doc(comp("z", ingredients={"chicken": 0, "rice": -50}))
    errs = errors_of(validate_components_doc(doc, known_ingredients=KNOWN))
    assert len(errs) == 2
    assert all(e.code == "nonpositive_grams" for e in errs)
    assert any("'chicken'" in e.where for e in errs)
    assert any("'rice'" in e.where for e in errs)


def test_rejects_missing_yield_g():
    c = comp("noyield")
    del c["yield_g"]
    errs = errors_of(validate_components_doc(comp_doc(c),
                                             known_ingredients=KNOWN))
    assert any(e.code == "missing_field" and "yield_g" in e.where for e in errs)


def test_rejects_declared_tags():
    errs = errors_of(validate_components_doc(comp_doc(comp("t", tags=[])),
                                             known_ingredients=KNOWN))
    assert any(e.code == "forbidden_field" and "tags" in e.where for e in errs)


def test_rejects_bad_role_enum():
    errs = errors_of(validate_components_doc(comp_doc(comp("r", role="dessert")),
                                             known_ingredients=KNOWN))
    assert any(e.code == "bad_enum" and "'r'" in e.where for e in errs)


def test_rejects_serve_min_over_max():
    doc = comp_doc(comp("s", serve_g={"min": 400, "max": 80}))
    errs = errors_of(validate_components_doc(doc, known_ingredients=KNOWN))
    assert any(e.code == "serve_bounds_inverted" for e in errs)


def test_unit_misalignment_is_error():
    # promoted from warning to error at M0.8: the LP's snap-and-clamp can only
    # guarantee unit multiples when the bounds sit on the unit grid
    doc = comp_doc(comp("u", unit_g=45, serve_g={"min": 90, "max": 500}))
    errs = errors_of(validate_components_doc(doc, known_ingredients=KNOWN))
    assert any(e.code == "serve_bounds_not_unit_aligned" for e in errs)
    # ...and an aligned one produces no issue at all
    ok = comp_doc(comp("u2", unit_g=45, serve_g={"min": 90, "max": 450}))
    assert not validate_components_doc(ok, known_ingredients=KNOWN)


def test_rejects_missing_schema_version():
    doc = {"components": [comp("v")]}
    errs = errors_of(validate_components_doc(doc, known_ingredients=KNOWN))
    assert any("schema_version" in e.where for e in errs)


def test_people_doc_validated_too():
    doc = {"schema_version": 1,
           "people": {"p1": {"tolerance": 0.05}},   # no targets
           "settings": {}}
    errs = errors_of(validate_people_doc(doc))
    assert any("targets" in e.where for e in errs)


# --------------------------------------------------------------------------- #
#  atomic writes
# --------------------------------------------------------------------------- #
def test_invalid_save_leaves_no_file(tmp_path):
    target = tmp_path / "components.yaml"
    bad = comp_doc(comp("x", role="snack"))
    with pytest.raises(ValidationError):
        save(target, bad, "components", known_ingredients=KNOWN)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == [], "temp/partial files left behind"


def test_invalid_save_leaves_existing_file_untouched(tmp_path):
    target = tmp_path / "components.yaml"
    good = comp_doc(comp("good"))
    save(target, good, "components", known_ingredients=KNOWN)
    before = target.read_text()

    with pytest.raises(ValidationError):
        save(target, comp_doc(comp("bad", tags=["x"])), "components",
             known_ingredients=KNOWN)
    assert target.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["components.yaml"]


def test_valid_save_roundtrips(tmp_path):
    lib = tmp_path / "lib"
    save(lib / "ingredients.yaml", ING_DOC, "ingredients")
    save(lib / "components.yaml", comp_doc(comp("good")), "components",
         known_ingredients=KNOWN)
    save(lib / "people.yaml", PPL_DOC, "people")
    ing, comps, people, settings = io_yaml.load(lib)
    assert list(comps) == ["good"]
    assert comps["good"]["per100"]["protein"] > 0
    assert people["p1"]["tolerance"] == 0.05
