"""M1.13 dish layer — FAST tier (zero CBC, milliseconds; M113_SPEC §14).

Covers the combinatorial logic with no LP solve anywhere: dishes.yaml
validation, availability/eligibility conjunctions, closure, band algebra
(t_max / kcal ranges / lean ratios), the skeleton (determinism, variety,
affinity, retry lever, slotless blocks, side nomination incl. the
permissive|strict orphan split), implicit-dish synthesis, the dish menu
score translations, the doctor dish section, and the heritage mode key
(no dishes.yaml ⇒ the layer never engages — the byte-identity itself is
guarded by the UNREGENERATED pre-M1.13 goldens in test_capabilities /
test_determinism, which run on dish-less libraries).
"""

import copy

import pytest

from mealplan import cli, dishes as D, io_yaml, model
from mealplan.io_yaml import validate_dishes_doc


# --------------------------------------------------------------------------- #
#  synthetic fixture — plain dicts, engine-style
# --------------------------------------------------------------------------- #
def _comp(cid, role="main", serve=(80, 400), unit=None, keeps=5, per100=None,
          tags=(), cuisine="test", freezes=None, pairs_with=None):
    d = dict(id=cid, name=cid.replace("_", " "), cuisine=cuisine, role=role,
             yield_g=1000, serve_g={"min": serve[0], "max": serve[1]},
             keeps_days=keeps, active_min=20, ingredients={"x": 100},
             per100=per100 or {"kcal": 150.0, "protein": 15.0, "fat": 5.0,
                               "carb": 10.0},
             tags=list(tags))
    if unit:
        d["unit_g"] = unit
    if freezes is not None:
        d["freezes"] = freezes
    if pairs_with:
        d["pairs_with"] = list(pairs_with)
    return d


COMPS = {
    # unit-discrete starch member (like gordita shells / tortillas)
    "shell": _comp("shell", role="starch", serve=(60, 240), unit=60, keeps=3,
                   per100={"kcal": 300.0, "protein": 8.0, "fat": 12.0,
                           "carb": 40.0}),
    # lean main (keeps long)
    "stew": _comp("stew", serve=(80, 400), keeps=5,
                  per100={"kcal": 120.0, "protein": 25.0, "fat": 2.0,
                          "carb": 1.0}),
    # fatty main
    "lard_braise": _comp("lard_braise", serve=(80, 400), keeps=5,
                         per100={"kcal": 260.0, "protein": 14.0,
                                 "fat": 22.0, "carb": 1.0}),
    # dairy accent (droppable)
    "cheese": _comp("cheese", role="accent", serve=(15, 100), keeps=10,
                    tags=("dairy",),
                    per100={"kcal": 350.0, "protein": 20.0, "fat": 28.0,
                            "carb": 2.0}),
    # sides
    "rice": _comp("rice", role="starch", serve=(100, 500), keeps=7,
                  per100={"kcal": 160.0, "protein": 3.0, "fat": 1.0,
                          "carb": 35.0}),
    "cuke": _comp("cuke", role="veg", serve=(50, 300), keeps=2,
                  per100={"kcal": 20.0, "protein": 1.0, "fat": 0.5,
                          "carb": 3.0}),
}

DISHES = {
    "taco": dict(
        id="taco", name="Taco", source="test", reconstruction="from_source",
        components={"shell": dict(base_g=120, min_g=60, max_g=240),
                    "stew": dict(base_g=150, min_g=80, max_g=300),
                    "cheese": dict(base_g=25, min_g=0, max_g=80)},
        accents=["cheese"], compatible_sides=["rice", "cuke"],
        meal_affinity=["lunch", "dinner"], cuisine=None, notes=None),
    "bowl": dict(
        id="bowl", name="Bowl", source="test", reconstruction="inferred",
        components={"rice": dict(base_g=200, min_g=100, max_g=400),
                    "stew": dict(base_g=160, min_g=80, max_g=320)},
        accents=[], compatible_sides=[], meal_affinity=[], cuisine=None,
        notes=None),
    "fat_plate": dict(
        id="fat_plate", name="Fat plate", source="test",
        reconstruction="invented",
        components={"lard_braise": dict(base_g=200, min_g=100, max_g=400),
                    "cheese": dict(base_g=30, min_g=0, max_g=100)},
        accents=["cheese"], compatible_sides=["rice"],
        meal_affinity=["breakfast"], cuisine=None, notes=None),
}

SETTINGS = dict(days=7, active_min_budget=600, batch_time_factor=0.5,
                max_days_same_component=4, cook_days=[0, 4], shop_days=[0],
                min_lean_anchors=1, max_batches_per_component=3,
                use_freezer=True, dish_layer="permissive",
                budget={"mode": "off"})

P1 = dict(name="p1", targets={"protein": 150, "fat": 70, "carb": 250},
          tolerance=0.1, exclude=[], dislikes=[],
          meal_slots=[{"name": "breakfast"}, {"name": "lunch"},
                      {"name": "dinner"}])
P_DAIRY = dict(name="pd", targets={"protein": 150, "fat": 70, "carb": 250},
               tolerance=0.1, exclude=["dairy"], dislikes=[])

SLOTS = [dict(name="breakfast", serving_model="portioned",
              interchangeable=False),
         dict(name="lunch", serving_model="portioned",
              interchangeable=False),
         dict(name="dinner", serving_model="portioned",
              interchangeable=False)]


def dishes_doc(dishes=None):
    return {"schema_version": 0,
            "dishes": [copy.deepcopy(d) for d in
                       (dishes or DISHES).values()]}


def codes(issues, severity=None):
    return {i.code for i in issues
            if severity is None or i.severity == severity}


# --------------------------------------------------------------------------- #
#  1. schema validation (all-errors; error/warning split per M113_SPEC §2)
# --------------------------------------------------------------------------- #
def test_valid_draft_shape_passes_with_no_errors():
    issues = validate_dishes_doc(dishes_doc(), comps=COMPS,
                                 people={"p1": P1})
    assert not [i for i in issues if i.severity == "error"], issues


def test_band_ordering_and_unknown_ids_error():
    doc = dishes_doc()
    doc["dishes"][0]["components"]["stew"] = dict(base_g=100, min_g=200,
                                                  max_g=150)
    doc["dishes"][0]["components"]["ghost"] = dict(base_g=10, min_g=1,
                                                   max_g=20)
    doc["dishes"][0]["compatible_sides"].append("phantom")
    issues = validate_dishes_doc(doc, comps=COMPS)
    errs = codes(issues, "error")
    assert "dish_band_invalid" in errs
    assert "unknown_component" in errs


def test_dish_band_exceeds_serve_error_and_warning_split():
    doc = dishes_doc()
    # min_g above serve max: one serving cannot exist — ERROR
    doc["dishes"][1]["components"]["stew"]["min_g"] = 450
    doc["dishes"][1]["components"]["stew"]["base_g"] = 460
    doc["dishes"][1]["components"]["stew"]["max_g"] = 470
    # max_g above serve max only: cap truncates the band — WARNING
    doc["dishes"][0]["components"]["stew"]["max_g"] = 500
    issues = validate_dishes_doc(doc, comps=COMPS)
    errs = [i for i in issues if i.code == "dish_band_exceeds_serve"]
    assert {i.severity for i in errs} == {"error", "warning"}
    assert any("450" in i.message and "400" in i.message
               for i in errs if i.severity == "error")


def test_accents_must_be_min_zero_members():
    doc = dishes_doc()
    doc["dishes"][0]["accents"] = ["rice"]        # not in components
    issues = validate_dishes_doc(doc, comps=COMPS)
    assert "accent_not_in_components" in codes(issues, "error")
    doc = dishes_doc()
    doc["dishes"][0]["components"]["cheese"]["min_g"] = 10
    issues = validate_dishes_doc(doc, comps=COMPS)
    assert "accent_min_nonzero" in codes(issues, "error")


def test_unit_grid_must_admit_a_serving():
    doc = dishes_doc()
    # shell unit_g 60: [70, 110] contains no multiple of 60
    doc["dishes"][0]["components"]["shell"] = dict(base_g=90, min_g=70,
                                                   max_g=110)
    issues = validate_dishes_doc(doc, comps=COMPS)
    assert "dish_band_off_grid" in codes(issues, "error")


def test_dish_band_loose_is_a_warning_with_the_ratio():
    doc = dishes_doc()
    # widen taco bands to a >3x worst-case pairwise ratio error
    doc["dishes"][0]["components"]["shell"] = dict(base_g=120, min_g=60,
                                                   max_g=240)
    doc["dishes"][0]["components"]["stew"] = dict(base_g=150, min_g=80,
                                                  max_g=400)
    issues = validate_dishes_doc(doc, comps=COMPS)
    loose = [i for i in issues if i.code == "dish_band_loose"]
    assert loose and all(i.severity == "warning" for i in loose)


def test_affinity_labels_validated_and_any_unmixed():
    doc = dishes_doc()
    doc["dishes"][0]["meal_affinity"] = ["second_breakfast"]
    doc["dishes"][1]["meal_affinity"] = ["any", "dinner"]
    issues = validate_dishes_doc(doc, comps=COMPS, people={"p1": P1})
    msgs = [i for i in issues if i.code == "bad_meal_affinity"]
    assert len(msgs) >= 2 and all(i.severity == "error" for i in msgs)


def test_affinity_slot_mismatch_note_per_person():
    ppl = {"pj": dict(P_DAIRY, meals_per_day=3)}   # slots meal_1..meal_3
    issues = validate_dishes_doc(dishes_doc(), comps=COMPS, people=ppl)
    hits = [i for i in issues if i.code == "affinity_slot_mismatch"]
    assert len(hits) == 1 and hits[0].severity == "warning"
    assert "meal_1" in hits[0].message


def test_duplicate_id_and_garnish_only_error():
    doc = dishes_doc()
    doc["dishes"].append(copy.deepcopy(doc["dishes"][0]))
    doc["dishes"].append(dict(
        id="garnish", name="G", source="t", reconstruction="invented",
        components={"cheese": dict(base_g=20, min_g=0, max_g=80)},
        accents=["cheese"], compatible_sides=[], meal_affinity=[]))
    issues = validate_dishes_doc(doc, comps=COMPS)
    errs = codes(issues, "error")
    assert "duplicate_dish_id" in errs
    assert "dish_only_garnish" in errs


def test_corpus_reachability_warnings():
    """main in no dish → component_unreachable; accent attached nowhere →
    orphan_component; starch/veg in no compatible_sides → orphan_side —
    all WARNINGS (incremental authoring must not brick a library)."""
    only_bowl = {"bowl": DISHES["bowl"]}
    issues = validate_dishes_doc(dishes_doc(only_bowl), comps=COMPS)
    warns = codes(issues, "warning")
    assert {"component_unreachable", "orphan_component",
            "orphan_side"} <= warns
    assert not [i for i in issues if i.severity == "error"]


def test_dead_config_new_fields_validated():
    """M113_SPEC §11: max_dishes_per_slot and dish_layer are validated
    schema/settings fields (the dead-config gate itself covers consumption
    in test_dead_config)."""
    from mealplan.io_yaml import validate_people_doc
    doc = {"schema_version": 1,
           "people": {"p": {"targets": {"protein": 1, "fat": 1, "carb": 1},
                            "tolerance": 0.1, "max_dishes_per_slot": 0,
                            "meal_slots": [{"name": "a",
                                            "max_dishes_per_slot": "two"}]}},
           "settings": {"days": 7, "active_min_budget": 60,
                        "max_days_same_component": 4, "cook_days": [0],
                        "dish_layer": "yolo"}}
    issues = validate_people_doc(doc)
    errs = codes(issues, "error")
    assert "bad_max_dishes_per_slot" in errs
    assert "bad_enum" in errs           # dish_layer


# --------------------------------------------------------------------------- #
#  2/3. availability conjunction + eligibility
# --------------------------------------------------------------------------- #
def test_dish_dies_with_shortest_lived_core_member():
    """shell keeps 3 from cook days [0, 4] — days 3 has no live shell (no
    freeze), so the taco dies on day 3 and the killer is NAMED."""
    ok, killer = D.dish_available_on(DISHES["taco"], COMPS, 0, SETTINGS)
    assert ok and killer is None
    ok, killer = D.dish_available_on(DISHES["taco"], COMPS, 3, SETTINGS)
    assert not ok and killer == "shell"
    # the bowl (rice+stew, keeps 7/5) survives day 3
    assert D.dish_available_on(DISHES["bowl"], COMPS, 3, SETTINGS)[0]


def test_freezer_bridging_applies_per_member():
    comps = copy.deepcopy(COMPS)
    comps["shell"]["freezes"] = True
    ok, killer = D.dish_available_on(DISHES["taco"], comps, 3, SETTINGS)
    assert ok, killer


def test_eligibility_core_kill_vs_accent_drop():
    # dairy person: cheese is an ACCENT of taco → dropped, dish intact
    ok, hit = D.dish_eligible(DISHES["taco"], COMPS, P_DAIRY)
    assert ok
    assert D.eligible_accents(DISHES["taco"], COMPS, P_DAIRY) == []
    # make cheese a CORE member → the dish dies, blocker named
    d = copy.deepcopy(DISHES["taco"])
    d["components"]["cheese"]["min_g"] = 10
    d["accents"] = []
    ok, hit = D.dish_eligible(d, COMPS, P_DAIRY)
    assert not ok and hit == ("cheese", "dairy")


# --------------------------------------------------------------------------- #
#  4. closure + orphan sides
# --------------------------------------------------------------------------- #
def test_closure_is_members_accents_sides_union():
    assert D.closure(DISHES, ["taco"]) == ["cheese", "cuke", "rice",
                                           "shell", "stew"]
    assert D.closure(DISHES, ["bowl"]) == ["rice", "stew"]


def test_orphan_sides_are_starch_veg_in_no_sides_list():
    only_bowl = {"bowl": DISHES["bowl"]}
    assert D.orphan_sides(only_bowl, COMPS) == ["cuke", "rice", "shell"]
    assert D.orphan_sides(DISHES, COMPS) == ["shell"]


def test_side_candidates_respect_dish_layer():
    """permissive admits orphan starch/veg from the menu closure (flagged);
    strict serves authored compatible_sides ONLY."""
    only_bowl = {"bowl": DISHES["bowl"]}
    perm = dict(SETTINGS, dish_layer="permissive")
    strict = dict(SETTINGS, dish_layer="strict")
    W = D._weights()
    take, orph = D._side_candidates(only_bowl["bowl"], only_bowl, COMPS,
                                    P1, 0, perm, None, None, {},
                                    set(D.closure(only_bowl, ["bowl"])), W)
    assert take == [] or all(t in {"rice"} for t in take)  # rice is a member
    take_s, orph_s = D._side_candidates(
        DISHES["bowl"], DISHES, COMPS, P1, 0, strict, None, None, {},
        set(D.closure(DISHES, list(DISHES))), W)
    assert take_s == [] and orph_s == set()   # authored [] means NO sides
    take_p, orph_p = D._side_candidates(
        DISHES["taco"], DISHES, COMPS, P1, 0, perm, None, None, {},
        set(D.closure(DISHES, list(DISHES))), W)
    assert "rice" in take_p and orph_p <= {"shell"}


# --------------------------------------------------------------------------- #
#  6. band algebra
# --------------------------------------------------------------------------- #
def test_t_max_is_min_over_core_serve_ratio():
    # shell: 240/120 = 2.0; stew: 400/150 ≈ 2.67 → t_max 2.0, limiter shell
    assert D.t_max(DISHES["taco"], COMPS, P1) == pytest.approx(2.0)
    assert D.t_max_limiter(DISHES["taco"], COMPS, P1) == "shell"


def test_kcal_range_scales_by_t_and_caps_by_serve():
    lo, hi = D.dish_kcal_range(DISHES["taco"], COMPS, P1)
    # lo = 0.5 * (60*3.0 + 80*1.2) = 0.5 * (180 + 96)
    assert lo == pytest.approx(0.5 * (60 * 3.0 + 80 * 1.2))
    # hi caps shell at min(240*2, serve 240) = 240
    assert hi == pytest.approx(240 * 3.0 + min(300 * 2.0, 400) * 1.2
                               + min(80 * 2.0, 100) * 3.5)


def test_composite_fat_heavy_dish_is_not_lean():
    """Integration note 3: leanness is the DISH's own achievable ratio —
    stew alone is lean, stew buried in a fat plate is not."""
    need = P1["targets"]["protein"] / P1["targets"]["fat"]   # ≈ 2.14
    assert D.dish_macro_ratio_max(DISHES["bowl"], COMPS, P1) \
        >= need * 1.25
    assert D.dish_macro_ratio_max(DISHES["fat_plate"], COMPS, P1) \
        < need * 1.25


# --------------------------------------------------------------------------- #
#  5. skeleton — deterministic, RNG-free, zero-LP
# --------------------------------------------------------------------------- #
def _skel(**kw):
    args = dict(person=P1, dishes=DISHES, comps=COMPS, settings=SETTINGS,
                day=0, slots=SLOTS, menu=sorted(DISHES))
    args.update(kw)
    person = args.pop("person")
    dishes = args.pop("dishes")
    comps = args.pop("comps")
    settings = args.pop("settings")
    day = args.pop("day")
    slots = args.pop("slots")
    menu = args.pop("menu")
    return D.skeleton_day(person, dishes, comps, settings, day, slots, menu,
                          **args)


def test_skeleton_deterministic_and_rng_free():
    a1, n1 = _skel()
    a2, n2 = _skel()
    assert a1 == a2 and n1 == n2


def test_skeleton_no_same_day_repeat_when_supply_allows():
    asn, notes = _skel()
    picked = [a["dishes"][0] for a in asn]
    assert len(set(picked)) == 3          # 3 dishes for 3 slots — no repeat
    assert not [x for x in notes if x["code"] == "variety_unmet"]


def test_skeleton_variety_unmet_declared_with_repeat_cap():
    two = {k: DISHES[k] for k in ("taco", "bowl")}
    asn, notes = _skel(dishes=two, menu=sorted(two))
    vu = [x for x in notes if x["code"] == "variety_unmet"]
    assert vu and vu[0]["repeat_cap"] == 2
    picked = [a["dishes"][0] for a in asn]
    assert max(picked.count(j) for j in set(picked)) <= 2


def test_skeleton_affinity_steers_breakfast():
    """taco authors lunch/dinner — the mismatch penalty keeps it OFF
    breakfast; the bowl (no authored affinity) is INERT and may serve any
    slot without penalty (M113_SPEC §2: affinity never guessed). With
    three dishes and three slots each dish serves once, and taco lands on
    an affinity-matched slot."""
    asn, _ = _skel()
    by_slot = {a["slot"]["name"]: a["dishes"][0] for a in asn}
    assert by_slot["breakfast"] != "taco"
    assert by_slot == {"breakfast": "bowl", "lunch": "taco",
                       "dinner": "fat_plate"}


def test_skeleton_used_days_variety_pressure():
    """A dish used 3 days running scores 900 worse — the next day picks the
    fresh alternative for the same slot."""
    asn0, _ = _skel(dishes={k: DISHES[k] for k in ("taco", "bowl")},
                    menu=["bowl", "taco"], slots=SLOTS[:1])
    first = asn0[0]["dishes"][0]
    other = "taco" if first == "bowl" else "bowl"
    asn1, _ = _skel(dishes={k: DISHES[k] for k in ("taco", "bowl")},
                    menu=["bowl", "taco"], slots=SLOTS[:1],
                    used_days={first: 3})
    assert asn1[0]["dishes"][0] == other


def test_skeleton_rank_bump_is_the_retry_lever():
    asn0, _ = _skel(slots=SLOTS[:1])
    asn1, _ = _skel(slots=SLOTS[:1], rank_bump={0: 1})
    assert asn0[0]["dishes"] != asn1[0]["dishes"]


def test_skeleton_veto_and_blockers_named():
    only = {k: DISHES[k] for k in ("taco", "bowl")}
    asn, notes = _skel(dishes=only, menu=["bowl", "taco"], slots=SLOTS[:1],
                       veto=["bowl", "taco"])
    assert asn[0]["dishes"] == []
    # day-3 shell death shows up as a named blocker
    asn3, _ = _skel(day=3, slots=SLOTS[:1])
    blk = asn3[0]["blockers"]
    assert blk.get("taco", {}).get("component") == "shell"
    assert blk["taco"]["code"] == "dish_unavailable"


def test_skeleton_second_dish_only_on_optin_and_capacity():
    """rung 3 (§7): default 1 dish/slot even for a huge target; opt-in 2 +
    capacity gap → second_dish_added with the kcal gap."""
    big = dict(P1, targets={"protein": 400, "fat": 200, "carb": 700})
    asn, notes = _skel(person=big, slots=SLOTS[:1])
    assert len(asn[0]["dishes"]) == 1
    assert not [x for x in notes if x["code"] == "second_dish_added"]
    big2 = dict(big, max_dishes_per_slot=2)
    asn2, notes2 = _skel(person=big2, slots=SLOTS[:1])
    added = [x for x in notes2 if x["code"] == "second_dish_added"]
    assert len(asn2[0]["dishes"]) == 2 and added
    assert added[0]["kcal_gap"] > 0


def test_slotless_blocks_capacity_arithmetic():
    """No slots → implicit day-block: enough dish blocks to clear the day
    target, capped at DISHES_PER_DAY."""
    small = dict(name="s", targets={"protein": 40, "fat": 20, "carb": 60},
                 tolerance=0.2, exclude=[], dislikes=[])
    blocks = D._slotless_blocks(small, DISHES, COMPS, sorted(DISHES), 0,
                                SETTINGS, None, {}, D._weights())
    assert len(blocks) == 1
    huge = dict(small, targets={"protein": 500, "fat": 250, "carb": 900})
    blocks = D._slotless_blocks(huge, DISHES, COMPS, sorted(DISHES), 0,
                                SETTINGS, None, {}, D._weights())
    assert len(blocks) == D.DISH_WEIGHTS["DISHES_PER_DAY"]


# --------------------------------------------------------------------------- #
#  7. implicit synthesis
# --------------------------------------------------------------------------- #
def test_implicit_dishes_shape_and_flags():
    comps = copy.deepcopy(COMPS)
    comps["cheese"]["pairs_with"] = ["lard_braise"]
    out, warns = D.implicit_dishes(comps, existing={"bowl": DISHES["bowl"]})
    # stew is covered by the bowl; lard_braise is not
    assert set(out) == {"lard_braise_implicit"}
    d = out["lard_braise_implicit"]
    band = d["components"]["lard_braise"]
    assert (band["min_g"], band["max_g"]) == (80, 400)
    assert band["base_g"] == 240
    assert d["accents"] == ["cheese"]                 # pairs_with graft
    assert set(d["compatible_sides"]) == {"shell", "rice", "cuke"}
    assert [w["code"] for w in warns] == ["implicit_dish"]


# --------------------------------------------------------------------------- #
#  8. menu-score translations (zero LP)
# --------------------------------------------------------------------------- #
def _ing():
    return {"x": dict(p=10.0, f=5.0, c=10.0, perishable=False, pack_g=500,
                      keeps_days=99, cost=3.0, tags=[])}


def test_score_penalizes_missing_lean_dish():
    """lean_anchor_floor fires on the DISH's achievable ratio: a menu of
    only the fat plate pays it; adding the bowl (lean) clears it."""
    s_fat, _ = D.score_menu_dishes(COMPS, _ing(), DISHES, ["fat_plate"],
                                   SETTINGS, {"p1": P1})
    s_mix, _ = D.score_menu_dishes(COMPS, _ing(), DISHES,
                                   ["fat_plate", "bowl", "taco"],
                                   SETTINGS, {"p1": P1})
    assert s_fat > s_mix


def test_score_slot_coverage_and_capacity_terms_fire():
    """A one-dish menu cannot cover three slots for a person whose slot
    names carry affinity, nor feed a huge target — both new penalties
    apply vs a fuller menu."""
    big = dict(P1, targets={"protein": 450, "fat": 220, "carb": 800})
    s_one, _ = D.score_menu_dishes(COMPS, _ing(), DISHES, ["taco"],
                                   SETTINGS, {"p": big})
    s_all, _ = D.score_menu_dishes(COMPS, _ing(), DISHES, list(DISHES),
                                   SETTINGS, {"p": big})
    assert s_one > s_all


def test_score_closure_rides_in_info():
    _, info = D.score_menu_dishes(COMPS, _ing(), DISHES, ["taco"], SETTINGS)
    assert info["closure"] == D.closure(DISHES, ["taco"])
    assert info["roles"] == {"dish": 1}


# --------------------------------------------------------------------------- #
#  9. doctor dish section (arithmetic only — zero LP)
# --------------------------------------------------------------------------- #
def test_doctor_dish_section_names_killers_and_unreachable_slots():
    jimbo = dict(name="j", targets={"protein": 500, "fat": 250, "carb": 900},
                 tolerance=0.05, exclude=["dairy"], dislikes=[],
                 meal_slots=[{"name": "lunch"}, {"name": "dinner"},
                             {"name": "third"}])
    lines, data = D.doctor_dish_section(COMPS, {"j": jimbo}, SETTINGS,
                                        DISHES)
    text = "\n".join(lines)
    # day-3 shell death named
    assert data["availability"][3]["dead"]["taco"] == "shell"
    # slot_target_unreachable: 4700 kcal / 3 slots beats every dish+sides
    unreach = data["per_person"]["j"]["slot_target_unreachable"]
    assert unreach and "slot_target_unreachable" in text
    # provenance: the invented dish is called out
    assert any(x["reconstruction"] == "invented" for x in data["dishes"])
    # M1.11 inertness: a no-profile person's entry names the bare person,
    # exactly as pre-M1.11 (their label IS their name)
    assert {x["person"] for x in unreach} == {"j"}


def test_doctor_dish_slot_unreachable_names_its_day_type():
    """M1.11 mirror fidelity: the reachability checks run once per
    DISTINCT day-type, so a `slot_target_unreachable` entry has to name
    the type it was computed against — the labeled key it is filed under.
    A bare person name would make a lift-day-only miss indistinguishable
    from a base-day one to anything reading the structured mirror (the
    rendered line is unambiguous only because it sits under the labeled
    block)."""
    base_t = {"protein": 500, "fat": 250, "carb": 900}
    jimbo = dict(name="j", targets=base_t, tolerance=0.05,
                 exclude=["dairy"], dislikes=[],
                 target_profiles={"lift": {"protein": 700, "fat": 300,
                                           "carb": 1200}},
                 week={"mon": "lift", "wed": "lift"},
                 meal_slots=[{"name": "lunch"}, {"name": "dinner"},
                             {"name": "third"}])
    _, data = D.doctor_dish_section(COMPS, {"j": jimbo}, SETTINGS, DISHES)
    base_lbl = "j — day-type 'base' (tue, thu, fri, sat, sun)"
    lift_lbl = "j — day-type 'lift' (mon, wed)"
    assert set(data["per_person"]) == {base_lbl, lift_lbl}
    per_type = {}
    for lbl in (base_lbl, lift_lbl):
        entries = data["per_person"][lbl]["slot_target_unreachable"]
        assert entries, lbl
        assert {e["person"] for e in entries} == {lbl}, lbl
        per_type[lbl] = {e["target_kcal"] for e in entries}
    # non-vacuous: the two day-types really were checked against DIFFERENT
    # slot targets, so the labels are carrying information
    assert per_type[base_lbl] != per_type[lift_lbl], per_type


def test_engine_doctor_splices_dish_section_only_in_dish_mode():
    """The dish section is present iff dishes are passed — heritage doctor
    text has no '## Dish layer' (byte-identity is pinned by the goldens;
    this guards the seam)."""
    import inspect
    from mealplan import engine
    src = inspect.getsource(engine.doctor)
    assert "doctor_dish_section" in src and "if dishes" in src


# --------------------------------------------------------------------------- #
#  10. heritage mode key — no dishes.yaml ⇒ the layer never engages
# --------------------------------------------------------------------------- #
def test_mode_key_is_dishes_yaml_presence(tmp_path):
    assert cli._load_dish_layer(None, False, tmp_path, COMPS,
                                {"p1": P1}) is None
    with pytest.raises(cli.CliError) as e:
        cli._load_dish_layer(str(tmp_path / "nope.yaml"), False, tmp_path,
                             COMPS, {"p1": P1})
    assert e.value.code == "missing_dishes_file"


def test_implicit_flag_synthesizes_without_a_file(tmp_path, capsys):
    dm = cli._load_dish_layer(None, True, tmp_path, COMPS, {"p1": P1})
    assert dm and all(j.endswith("_implicit") for j in dm)
    assert "implicit_dish" in capsys.readouterr().err


def test_dish_dataclass_roundtrip():
    d = model.Dish.from_raw(DISHES["taco"])
    assert d["components"]["shell"]["base_g"] == 120
    assert d.accents == ["cheese"]
    assert D.core_members(d) == ["shell", "stew"]
