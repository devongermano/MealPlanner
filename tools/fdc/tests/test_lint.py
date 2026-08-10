"""Lint tests: physics rules on a deliberately-broken fixture + FDC deviation
warnings against the fixture-derived mini extract. No network, no mealplan
package import (the linter parses YAML directly)."""

import json

import yaml
from conftest import FIXTURES

from index import build_index
from lint_ingredients import atwater_kcal, lint, physics_findings
from transform import find_csv_dir, load_dataset, render_extract

MINI = FIXTURES / "mini_cache"


def mini_indexed():
    entries = []
    for name, dt in (("sr_legacy", "sr_legacy_food"), ("foundation", "foundation_food")):
        es, _ = load_dataset(find_csv_dir(MINI / name), dt)
        entries.extend(es)
    return build_index(json.loads(render_extract(entries, "snapTEST")))


def load_yaml(name):
    return yaml.safe_load((FIXTURES / name).read_text())


def by_name_check(findings):
    return {(f["name"], f["check"]): f for f in findings}


class TestPhysics:
    def test_atwater(self):
        assert atwater_kcal(10, 10, 10) == 170.0

    def test_mass_overflow_is_error(self):
        fs = physics_findings("x", {"p": 40, "f": 40, "c": 40})
        assert any(f["check"] == "mass_overflow" and f["level"] == "ERROR" for f in fs)

    def test_negative_macro_is_error(self):
        fs = physics_findings("x", {"p": 0, "f": -5, "c": 0})
        assert any(f["check"] == "negative_macro" for f in fs)

    def test_kcal_density_error_when_over_900(self):
        fs = physics_findings("x", {"p": 10, "f": 110, "c": 10})
        assert any(f["check"] == "kcal_density" for f in fs)

    def test_plausible_ingredient_is_clean(self):
        assert physics_findings("x", {"p": 22.5, "f": 2.6, "c": 0.0}) == []


class TestLintDocuments:
    def test_broken_fixture_produces_errors(self):
        findings = lint(load_yaml("ingredients_broken.yaml"), None, "")
        idx = by_name_check(findings)
        assert idx[("overweight_paste", "mass_overflow")]["level"] == "ERROR"
        assert idx[("antimatter_oil", "negative_macro")]["level"] == "ERROR"
        assert ("rocket_fuel", "kcal_density") in idx
        assert ("rocket_fuel", "mass_overflow") in idx

    def test_ok_fixture_has_no_errors(self):
        findings = lint(load_yaml("ingredients_ok.yaml"), None, "")
        assert [f for f in findings if f["level"] == "ERROR"] == []

    def test_missing_ingredients_key_is_error(self):
        findings = lint({"whatever": 1}, None, "")
        assert findings[0]["check"] == "format"


class TestFdcDeviation:
    def setup_method(self):
        self.indexed = mini_indexed()

    def test_deviation_over_15_percent_warns_with_candidate(self):
        findings = lint(
            load_yaml("ingredients_broken.yaml"), self.indexed, "snapTEST"
        )
        dev = by_name_check(findings)[("chicken_breast", "fdc_macro_deviation")]
        assert dev["level"] == "WARN"
        assert dev["candidate"].startswith("usda_fdc:171077@snapTEST")

    def test_matching_macros_report_ok(self):
        findings = lint(load_yaml("ingredients_ok.yaml"), self.indexed, "snapTEST")
        idx = by_name_check(findings)
        assert idx[("chicken_breast", "fdc_match")]["level"] == "OK"

    def test_unmatched_name_is_info_not_error(self):
        doc = {"ingredients": {"gochugaru": {"p": 10, "f": 5, "c": 50}}}
        findings = lint(doc, self.indexed, "snapTEST")
        assert all(f["level"] == "INFO" for f in findings)
