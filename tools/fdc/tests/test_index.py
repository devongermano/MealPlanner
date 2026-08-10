"""Index/search tests over the fixture-derived mini extract. No network."""

import json

from conftest import FIXTURES

from index import build_index, normalize, search
from transform import find_csv_dir, load_dataset, render_extract

MINI = FIXTURES / "mini_cache"


def mini_extract():
    entries = []
    for name, dt in (("sr_legacy", "sr_legacy_food"), ("foundation", "foundation_food")):
        es, _ = load_dataset(find_csv_dir(MINI / name), dt)
        entries.extend(es)
    return json.loads(render_extract(entries, "snapTEST"))


class TestNormalize:
    def test_lowercase_tokens_no_punctuation(self):
        assert normalize("Chicken, broilers or fryers, breast!") == [
            "chicken", "broiler", "fryer", "breast",
        ]

    def test_stopwords_removed(self):
        assert normalize("beans with pork and sauce") == ["bean", "pork", "sauce"]

    def test_plural_folding(self):
        assert normalize("carrots eggs potatoes") == ["carrot", "egg", "potato"]

    def test_folding_is_consistent_both_sides(self):
        # Folding need not be linguistically right, only identical for query
        # and description text.
        assert normalize("molasses") == normalize("Molasses!")


class TestRanking:
    def setup_method(self):
        self.indexed = build_index(mini_extract())

    def test_chicken_breast_beats_chicken_fat(self):
        results = search("chicken breast raw", self.indexed)
        assert results, "no candidates"
        assert "breast" in results[0]["description"].lower()
        fat_ranks = [
            i for i, r in enumerate(results) if r["description"] == "Fat, chicken"
        ]
        assert not fat_ranks or fat_ranks[0] > 0

    def test_result_carries_fdc_id_and_macros(self):
        top = search("chicken breast raw", self.indexed)[0]
        assert top["fdc_id"] == 171077
        assert (top["p"], top["f"], top["c"]) == (22.5, 2.62, 0.0)

    def test_raw_preferred_over_dried_for_bare_eggs_query(self):
        top = search("eggs", self.indexed)[0]
        assert top["description"] == "Egg, whole, raw, fresh"

    def test_thigh_query_finds_foundation_entry(self):
        top = search("chicken thigh raw", self.indexed)[0]
        assert top["fdc_id"] == 2727567
        assert top["data_type"] == "foundation_food"

    def test_min_coverage_filters_junk(self):
        assert search("zebra cupcake", self.indexed) == []

    def test_no_index_internals_leak(self):
        top = search("butter", self.indexed)[0]
        assert not any(k.startswith("_") for k in top)
        assert 0 < top["score"] <= 1.0

    def test_search_is_pure_and_stable(self):
        a = search("chicken breast raw", self.indexed)
        b = search("chicken breast raw", self.indexed)
        assert a == b
