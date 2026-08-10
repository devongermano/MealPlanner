"""Transform tests: filter correctness + deterministic output. No network —
everything runs against the tiny committed fixture in fixtures/mini_cache."""

from conftest import FIXTURES

from transform import find_csv_dir, load_dataset, render_extract

MINI = FIXTURES / "mini_cache"


def load_sr():
    return load_dataset(find_csv_dir(MINI / "sr_legacy"), "sr_legacy_food")


def load_fnd():
    return load_dataset(find_csv_dir(MINI / "foundation"), "foundation_food")


def descriptions(entries):
    return {e["description"] for e in entries}


class TestFilterCorrectness:
    def test_raw_allowlisted_entry_kept(self):
        entries, _ = load_sr()
        assert (
            "Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw"
            in descriptions(entries)
        )

    def test_cooked_entry_dropped(self):
        entries, stats = load_sr()
        assert not any("cooked" in d for d in descriptions(entries))
        assert stats["blocklist_dropped"] >= 1

    def test_fast_food_category_dropped(self):
        entries, _ = load_sr()
        assert not any("WENDY" in d for d in descriptions(entries))

    def test_sausage_kept_only_when_raw(self):
        entries, _ = load_sr()
        descs = descriptions(entries)
        assert "Sausage, pork, chorizo, link or ground, raw" in descs
        assert "Bologna, beef" not in descs

    def test_missing_macros_dropped(self):
        entries, stats = load_sr()
        assert "Fixture, missing macros" not in descriptions(entries)
        assert stats["missing_macros"] == 1

    def test_implausible_macros_dropped(self):
        entries, stats = load_sr()
        assert "Fixture, impossible macros" not in descriptions(entries)
        assert stats["implausible"] == 1

    def test_macros_extracted_per_100g(self):
        entries, _ = load_sr()
        breast = next(e for e in entries if e["fdc_id"] == 171077)
        assert (breast["p"], breast["f"], breast["c"]) == (22.5, 2.62, 0.0)
        assert breast["category"] == "poultry"
        assert breast["data_type"] == "sr_legacy_food"

    def test_foundation_wrong_data_type_rows_skipped(self):
        entries, stats = load_fnd()
        assert {e["fdc_id"] for e in entries} == {2727567}
        assert stats["wrong_data_type"] == 1

    def test_foundation_small_negative_carb_clamped_to_zero(self):
        entries, stats = load_fnd()
        thigh = entries[0]
        assert thigh["c"] == 0.0
        assert stats["clamped_negative"] == 1
        assert stats["implausible"] == 0


class TestDeterminism:
    def test_same_input_renders_byte_identical(self):
        e1, _ = load_sr()
        e2, _ = load_sr()
        assert render_extract(e1, "snap") == render_extract(e2, "snap")

    def test_entry_order_is_by_fdc_id_regardless_of_input_order(self):
        entries, _ = load_sr()
        text_a = render_extract(entries, "snap")
        text_b = render_extract(list(reversed(entries)), "snap")
        assert text_a == text_b

    def test_extract_is_valid_json_with_snapshot(self):
        import json

        entries, _ = load_sr()
        doc = json.loads(render_extract(entries, "sr2018-04+fndTEST"))
        assert doc["snapshot"] == "sr2018-04+fndTEST"
        ids = [e["fdc_id"] for e in doc["entries"]]
        assert ids == sorted(ids)
