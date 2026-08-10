"""M1.12 fast-tier tests — the timeline compiler v0 (schedule.py) + the
timeline cook-plan rendering + the settings.stations schema.

The scheduler is PURE (deterministic, RNG-free, zero LP), so everything
here is hand-built or synthetic — no solves. The ONE slow e2e (examples
corpus + --methods + cook_plan_style: timeline, byte-stable x2) lives in
test_timeline_e2e.py (SLOW_MODULES).

Durations are provisional estimates (P9): tests assert on the scheduler's
INVARIANTS (one active task at a time, station capacities, oven temp
buckets, topological order, makespan <= naive) and on rendering shape —
never on wall-clock truth.
"""

import random

from mealplan import model
from mealplan.artifacts import render_cook_plan
from mealplan.io_yaml import validate_people_doc
from mealplan.schedule import SHARED, compile_session, format_min

META = {"seed": 7, "library": "testlib", "date": "2026-08-09"}

P_MAIN = {"kcal": 145.0, "protein": 20.0, "fat": 5.0, "carb": 5.0}


def _comp(cid, ingredients):
    return dict(id=cid, name=cid, cuisine="test", role="main", yield_g=500,
                serve_g={"min": 50, "max": 600}, keeps_days=5, active_min=20,
                ingredients=ingredients, per100=P_MAIN, tags=[])


COMPS = {
    "picadillo": _comp("picadillo", {"onion_yellow": 150, "beef": 454,
                                     "potato": 300, "carrot": 60}),
    "sugo": _comp("sugo", {"onion_yellow": 200, "sausage": 500}),
    "veg": _comp("veg", {"broccoli": 400}),
    "steak": _comp("steak", {"beef": 600}),
}

METHODS = {
    "picadillo": [
        dict(phase="prep", text="Dice the onion", station="prep",
             mode="active", duration_min=4, operation="dice"),
        dict(phase="prep", text="Dice the potatoes and the carrot",
             station="prep", mode="active", duration_min=8,
             operation="dice"),
        dict(phase="cook", text="Simmer until the potato is tender",
             station="stove", mode="passive", duration_min=20,
             operation="simmer"),
    ],
    "sugo": [
        dict(phase="prep", text="Dice the onion", station="prep",
             mode="active", duration_min=4, operation="dice"),
        dict(phase="cook", text="Brown the sausage", station="stove",
             mode="active", duration_min=8, operation="brown"),
        dict(phase="finish", text="Roast to finish", station="oven",
             mode="passive", duration_min=25, oven_temp_f=425,
             operation="roast"),
    ],
}


def _settings(style="timeline", stations=None, factor=0.45):
    d = dict(days=7, active_min_budget=100, batch_time_factor=factor,
             max_days_same_component=4, cook_days=[0, 4], shop_days=[0],
             cook_plan_style=style)
    if stations is not None:
        d["stations"] = stations
    return model.Settings.from_raw(d, {"mode": "off"})


SET = _settings()


def _sp(batches):
    return dict(
        sessions=[dict(index=0, start=0,
                       demand_g={c: 500 * b for c, b in batches.items()},
                       batches=dict(batches),
                       made_g={c: 500 * b for c, b in batches.items()},
                       minutes=60, thaw_notes=[], freezer_notes=[],
                       feeds=[])],
        batches=dict(batches), minutes=60, unattributed=[], freezer=[],
        leftover=[])


# --------------------------------------------------------------------------- #
#  invariant checkers (shared by hand-built and synthetic properties)
# --------------------------------------------------------------------------- #
def _overlap(a, b):
    return a["t_start"] < b["t_end"] and b["t_start"] < a["t_end"]


def _check_invariants(out, methods, batches, stations):
    entries = out["entries"]
    # one ACTIVE task at a time — the cook is a unary resource
    actives = [e for e in entries if e["mode"] == "active"]
    for i, a in enumerate(actives):
        for b in actives[i + 1:]:
            assert not _overlap(a, b), (a, b)
    # station capacities respected at every instant (occupancy sampled at
    # every start time — occupancy only changes at entry boundaries)
    cap = {"prep": stations["prep"], "stove": stations["burners"],
           "oven": stations["oven_slots"], "grill": 1}
    for t in sorted({e["t_start"] for e in entries}):
        for st, c in cap.items():
            resident = [e for e in entries if e["station"] == st
                        and e["t_start"] <= t < e["t_end"]]
            assert len(resident) <= c, (st, t, resident)
            # oven co-residency: same temp bucket only
            if st == "oven" and resident:
                temps = {e["oven_temp_f"] for e in resident}
                assert len(temps) == 1, (t, resident)
    # topological: a component's entries appear in fragment order and
    # never overlap their predecessor
    for cid in batches:
        seq = [e for e in entries if e["component"] == cid]
        for prev, nxt in zip(seq, seq[1:]):
            assert prev["t_end"] <= nxt["t_start"], (prev, nxt)
    # makespan can never beat doing nothing in parallel... backwards:
    # parallelism may only HELP
    assert out["makespan_min"] <= out["naive_min"]
    # durations positive, timers on passive entries only
    for e in entries:
        assert e["t_end"] > e["t_start"]
        if e["mode"] == "passive":
            assert e["timers"] and e["timers"][0]["at_min"] == e["t_end"]
        else:
            assert e["timers"] == []


# =========================================================================== #
#  the greedy scheduler — hand-built cases
# =========================================================================== #
def test_schedule_deterministic():
    a = compile_session({"picadillo": 2, "sugo": 1}, COMPS, METHODS, SET)
    b = compile_session({"picadillo": 2, "sugo": 1}, COMPS, METHODS, SET)
    assert a == b


def test_schedule_invariants_on_hand_case():
    out = compile_session({"picadillo": 2, "sugo": 1}, COMPS, METHODS, SET)
    _check_invariants(out, METHODS, {"picadillo": 2, "sugo": 1},
                      model.STATIONS_DEFAULTS)
    assert out["unscheduled"] == [] and out["warnings"] == []


def test_schedule_shared_prep_merged_first():
    out = compile_session({"picadillo": 2, "sugo": 1}, COMPS, METHODS, SET)
    first = out["entries"][0]
    assert first["component"] == SHARED
    assert first["t_start"] == 0
    assert "onion_yellow" in first["step_text"]
    # exactly one shared entry — the two per-dish onion dices merged
    assert sum(1 for e in out["entries"] if e["component"] == SHARED) == 1


def test_schedule_longest_passive_first_seeding():
    """Two independent one-step-then-passive components: the one with the
    LONGER passive gets its active step first, so the long unattended work
    starts earliest."""
    comps = {"quick": _comp("quick", {"a": 100}),
             "slow": _comp("slow", {"b": 100})}
    methods = {
        "quick": [dict(phase="prep", text="Chop a", station="prep",
                       mode="active", duration_min=5),
                  dict(phase="cook", text="Short simmer", station="stove",
                       mode="passive", duration_min=10)],
        "slow": [dict(phase="prep", text="Chop b", station="prep",
                      mode="active", duration_min=5),
                 dict(phase="cook", text="Long braise", station="stove",
                      mode="passive", duration_min=60)],
    }
    out = compile_session({"quick": 1, "slow": 1}, comps, methods, SET)
    order = [e["component"] for e in out["entries"]]
    assert order[0] == "slow"
    # and the braise runs while the quick dish is worked on
    braise = next(e for e in out["entries"] if e["step_text"] == "Long braise")
    chop_a = next(e for e in out["entries"] if e["step_text"] == "Chop a")
    assert _overlap(braise, chop_a)
    assert chop_a["meanwhile"] is True


def test_schedule_active_durations_scale_with_batches_passive_fixed():
    one = compile_session({"picadillo": 1}, COMPS, METHODS, SET)
    three = compile_session({"picadillo": 3}, COMPS, METHODS, SET)

    def _dur(out, text):
        e = next(e for e in out["entries"] if e["step_text"] == text)
        return e["t_end"] - e["t_start"]

    # active: 8 min -> 8 * (1 + 0.45 * 2) = 15.2 -> 15
    assert _dur(one, "Dice the potatoes and the carrot") == 8
    assert _dur(three, "Dice the potatoes and the carrot") == 15
    # passive: fixed regardless of batches
    assert _dur(one, "Simmer until the potato is tender") == 20
    assert _dur(three, "Simmer until the potato is tender") == 20


def test_schedule_oven_temp_buckets_share_and_sequence():
    comps = {c: _comp(c, {"x": 100}) for c in ("a425", "b425", "c350")}
    def frag(temp):
        return [
            dict(phase="prep", text="Prep", station="prep", mode="active",
                 duration_min=2),
            dict(phase="cook", text=f"Roast at {temp}", station="oven",
                 mode="passive", duration_min=30, oven_temp_f=temp)]
    methods = {"a425": frag(425), "b425": frag(425), "c350": frag(350)}
    out = compile_session({"a425": 1, "b425": 1, "c350": 1}, comps, methods,
                          SET)
    _check_invariants(out, methods, {"a425": 1, "b425": 1, "c350": 1},
                      model.STATIONS_DEFAULTS)
    roasts = [e for e in out["entries"] if e["station"] == "oven"]
    a, b = [e for e in roasts if e["oven_temp_f"] == 425]
    c, = [e for e in roasts if e["oven_temp_f"] == 350]
    # same temp co-resides; different temp sequences with BOTH
    assert _overlap(a, b)
    assert not _overlap(c, a) and not _overlap(c, b)


def test_schedule_oven_slots_capacity_respected():
    comps = {c: _comp(c, {"x": 100}) for c in ("a", "b", "c")}
    frag = [dict(phase="cook", text="Roast", station="oven", mode="passive",
                 duration_min=30, oven_temp_f=400)]
    methods = {c: list(frag) for c in comps}
    out = compile_session({"a": 1, "b": 1, "c": 1}, comps, methods,
                          _settings(stations={"oven_slots": 2}))
    roasts = sorted((e["t_start"], e["t_end"]) for e in out["entries"])
    assert roasts == [(0, 30), (0, 30), (30, 60)]   # 2 slots, third waits


def test_schedule_no_fragment_component_reported_unscheduled():
    out = compile_session({"picadillo": 1, "veg": 1}, COMPS, METHODS, SET)
    assert out["unscheduled"] == ["veg"]
    assert any(w["code"] == "no_fragment" for w in out["warnings"])
    assert all(e["component"] != "veg" for e in out["entries"])


def test_schedule_grill_without_grill_warns_but_schedules():
    comps = {"steak": _comp("steak", {"beef": 600})}
    methods = {"steak": [dict(phase="cook", text="Grill the steak",
                              station="grill", mode="active",
                              duration_min=10, operation="grill")]}
    out = compile_session({"steak": 1}, comps, methods,
                          _settings(stations={"grill": False}))
    assert any(w["code"] == "no_grill" for w in out["warnings"])
    assert len(out["entries"]) == 1              # scheduled anyway, loudly
    out2 = compile_session({"steak": 1}, comps, methods,
                           _settings(stations={"grill": True}))
    assert not any(w["code"] == "no_grill" for w in out2["warnings"])


def test_schedule_idle_windows_are_passive_only_gaps():
    out = compile_session({"picadillo": 2, "sugo": 1}, COMPS, METHODS, SET)
    for a, b in out["idle_windows"]:
        assert a < b
        for e in out["entries"]:
            if e["mode"] == "active":
                assert e["t_end"] <= a or e["t_start"] >= b
        assert any(e["mode"] == "passive"
                   and e["t_start"] < b and e["t_end"] > a
                   for e in out["entries"])


def test_format_min():
    assert format_min(0) == "0:00"
    assert format_min(5) == "0:05"
    assert format_min(65) == "1:05"
    assert format_min(130) == "2:10"


# =========================================================================== #
#  property: invariants over synthetic fragment sets (seeded, reproducible)
# =========================================================================== #
def _synthetic_case(rng):
    n = rng.randint(1, 5)
    comps, methods, batches = {}, {}, {}
    stations = ["prep", "stove", "oven", "grill", "none"]
    for i in range(n):
        cid = f"c{i}"
        comps[cid] = _comp(cid, {f"ing{i}": 100})
        steps = []
        phases = (["prep"] * rng.randint(0, 2) + ["cook"]
                  + ["finish"] * rng.randint(0, 1))
        for k, ph in enumerate(phases):
            st = rng.choice(stations)
            mode = rng.choice(["active", "active", "passive"])
            step = dict(phase=ph, text=f"{cid} step {k}", station=st,
                        mode=mode, duration_min=rng.randint(1, 40))
            if st == "oven":
                step["oven_temp_f"] = rng.choice([350, 400, 425])
            steps.append(step)
        methods[cid] = steps
        batches[cid] = rng.randint(1, 3)
    inv = {"burners": rng.randint(1, 4), "oven_slots": rng.randint(1, 2),
           "grill": True, "prep": rng.randint(1, 2)}
    return comps, methods, batches, inv


def test_schedule_property_invariants_over_synthetic_sets():
    """20 seeded synthetic sessions: every schedule satisfies the unary-
    cook, station-capacity, temp-bucket, topological, and makespan <=
    naive invariants, deterministically. (The RNG builds the CASES; the
    scheduler itself is RNG-free.)"""
    for seed in range(20):
        rng = random.Random(seed)
        comps, methods, batches, inv = _synthetic_case(rng)
        settings = _settings(stations=inv)
        out = compile_session(batches, comps, methods, settings)
        again = compile_session(batches, comps, methods, settings)
        assert out == again, f"seed {seed} not deterministic"
        stations = {**model.STATIONS_DEFAULTS, **inv}
        _check_invariants(out, methods, batches, stations)
        # completeness: every step of every component is on the timeline
        # (synthetic steps carry no operation, so nothing consolidates —
        # one entry per step, none lost, none doubled)
        n_steps = sum(len(methods[c]) for c in batches)
        assert len(out["entries"]) == n_steps, f"seed {seed}"


# =========================================================================== #
#  the timeline renderer
# =========================================================================== #
TECHNIQUES = {
    "dice": dict(name="Dice", one_line="Cut into even small cubes."),
    "simmer": dict(name="Simmer", one_line="Hold at a lazy bubble."),
    "brown": dict(name="Brown", one_line="Color it, don't gray it."),
    "roast": dict(name="Roast", one_line="High dry oven heat."),
}


def test_timeline_render_snapshot_hand_built_schedule():
    """The interleaved stream for the hand case, asserted line by line —
    timestamps from 0:00, shared prep first, 'Meanwhile' framing, timer
    callouts, the honest makespan-vs-naive line, the estimates header."""
    out = render_cook_plan(COMPS, SET, _sp({"picadillo": 2, "sugo": 1}),
                           meta=META, methods=METHODS,
                           techniques=TECHNIQUES)
    # honesty: estimates, twice (global header + per-session line)
    assert ("Times are ESTIMATES — calibrate by cooking (durations are "
            "provisional until cook-day calibration)") in out
    assert "_Times are estimates; calibrate by cooking._" in out
    # the honest parallelization line
    assert ("Interleaved: **50 min** start to finish vs 75 min "
            "one-thing-at-a-time — parallelization saves you **25 min**."
            in out)
    # the stream itself
    assert ("- [ ] **0:00–0:10** Shared prep: Dice 500g onion_yellow — "
            "300g picadillo, 200g sugo (shared prep, merged once) — "
            "_prep · active ~10 min · [dice]_" in out)
    assert ("- [ ] **0:10–0:18** sugo: Brown the sausage (500g sausage) — "
            "_stove · active ~8 min · [brown]_" in out)
    assert ("- [ ] **0:18–0:30** Meanwhile — picadillo: Dice the potatoes "
            "and the carrot — _prep · active ~12 min · [dice]_" in out)
    assert ("- [ ] **0:18–0:43** sugo: Roast to finish — _oven 425°F · "
            "passive · [roast]_ — ⏱ set a timer for 25 min (up at 0:43)"
            in out)
    assert ("- [ ] **0:30–0:50** picadillo: Simmer until the potato is "
            "tender (600g potato) — _stove · passive · [simmer]_ — "
            "⏱ set a timer for 20 min (up at 0:50)" in out)
    # mise en place with batch-scaled quantities
    assert ("- [ ] **picadillo** × 2 batches (makes 1000g, need 1000g): "
            "onion_yellow 300g, beef 908g, potato 600g, carrot 120g" in out)
    # glossary still resolves the referenced operations
    assert "- **[simmer]** Simmer — Hold at a lazy bubble." in out
    # session tail survives
    assert "Shortest keeps this session" in out


def test_timeline_render_deterministic():
    args = (COMPS, SET, _sp({"picadillo": 2, "sugo": 1}))
    kw = dict(meta=META, methods=METHODS, techniques=TECHNIQUES)
    assert render_cook_plan(*args, **kw) == render_cook_plan(*args, **kw)


def test_timeline_render_no_injection_while_still_cooking():
    """Readiness filter: picadillo's last step (the simmer) runs through
    every idle-hands window, so its containers must NOT be injected into
    the stream — the cook can't pack food that is still cooking. The
    portion & pack block lands AFTER the stream instead."""
    sp = _sp({"picadillo": 2, "sugo": 1})
    sp["sessions"][0]["feeds"] = [
        dict(component="picadillo", day=0, grams=450)]
    weeks = {"ada": [{"picadillo": 450}] + [{}] * 6}
    meals = {"ada": [dict(meals=[dict(slot="lunch",
                                      serving_model="portioned",
                                      items={"picadillo": 450})],
                          notes=[])] + [dict(meals=[], notes=[])] * 6}
    from mealplan.artifacts import build_portioning
    matrix = build_portioning(sp, weeks, {"ada": {}}, meals, COMPS)
    out = render_cook_plan(COMPS, SET, sp, meta=META, methods=METHODS,
                           techniques=TECHNIQUES, matrix=matrix)
    assert "Idle hands —" not in out
    # the matrix still renders, after the stream
    assert "Portion & pack — session" in out
    assert "- [ ] ada · eat day 1 · lunch — 450g" in out
    assert out.index("Portion & pack — session") > out.rindex(
        "⏱ set a timer")


def test_timeline_render_portioning_injected_only_when_ready():
    """A component whose cook steps are all done before the idle-hands
    window IS injected there; a component still cooking through the
    window packs after the stream — the injection is readiness-filtered,
    not the whole matrix."""
    methods = {
        "picadillo": METHODS["picadillo"],
        "steak": [
            dict(phase="cook", text="Sear the steak", station="stove",
                 mode="active", duration_min=6, operation="brown"),
        ],
    }
    sp = _sp({"picadillo": 2, "steak": 1})
    sp["sessions"][0]["feeds"] = [
        dict(component="picadillo", day=0, grams=450),
        dict(component="steak", day=0, grams=200)]
    weeks = {"ada": [{"picadillo": 450, "steak": 200}] + [{}] * 6}
    meals = {"ada": [dict(meals=[dict(slot="lunch",
                                      serving_model="portioned",
                                      items={"picadillo": 450,
                                             "steak": 200})],
                          notes=[])] + [dict(meals=[], notes=[])] * 6}
    from mealplan.artifacts import build_portioning
    matrix = build_portioning(sp, weeks, {"ada": {}}, meals, COMPS)
    out = render_cook_plan(COMPS, SET, sp, meta=META, methods=methods,
                           techniques=TECHNIQUES, matrix=matrix)
    # steak (active-only, done before the simmer window) is injected
    assert ("Idle hands — the kitchen is cooking itself; portion & pack "
            "what's already done:" in out)
    inject_at = out.index("Idle hands —")
    after_stream_at = out.index("Portion & pack — session")
    injected_block = out[inject_at:after_stream_at]
    assert "**steak** — pack 1 container" in injected_block
    # picadillo is still simmering at the window's start — NOT injected
    assert "**picadillo**" not in injected_block
    # the injection sits inside the Timeline section, timestamped at the
    # idle window (after the stream's entries begin)
    assert inject_at > out.index("Timeline — 0:00")
    # picadillo packs after the stream; steak is not repeated there
    after_block = out[after_stream_at:]
    assert "**picadillo** — pack 1 container" in after_block
    assert "**steak** — pack" not in after_block
    assert "Already packed during the idle-hands window" in after_block


def test_timeline_render_unscheduled_component_keeps_recipe_block():
    out = render_cook_plan(COMPS, SET,
                           _sp({"picadillo": 1, "veg": 2}), meta=META,
                           methods=METHODS, techniques=TECHNIQUES)
    assert "### veg × 2 batches (makes 1000g, need 1000g)" in out
    assert "- [ ] broccoli: 800g" in out
    assert "no method fragment" in out          # the loud NOTE
    # veg never appears on the stream
    assert "veg:" not in out.split("Timeline — 0:00")[1].split("###")[0]


def test_recipe_style_unchanged_by_scheduler_presence():
    """cook_plan_style: recipe must never invoke the scheduler — no
    timeline markers anywhere in its output."""
    st = _settings(style="recipe")
    out = render_cook_plan(COMPS, st, _sp({"picadillo": 2, "sugo": 1}),
                           meta=META, methods=METHODS,
                           techniques=TECHNIQUES)
    for marker in ("Timeline", "⏱", "Interleaved:", "Mise en place",
                   "Meanwhile"):
        assert marker not in out, marker


# =========================================================================== #
#  settings.stations — schema + defaults
# =========================================================================== #
def _people_doc(**settings_extra):
    return {"schema_version": 1,
            "people": {"p1": {"targets": {"protein": 100, "fat": 60,
                                          "carb": 200},
                              "tolerance": 0.05}},
            "settings": {"days": 7, "active_min_budget": 180,
                         "cook_days": [0], "max_days_same_component": 4,
                         **settings_extra}}


def test_stations_defaults_applied_and_partial_override():
    st = _settings(stations=None)
    assert st["stations"] == {"burners": 4, "oven_slots": 2, "grill": False,
                              "prep": 1}
    st2 = _settings(stations={"burners": 2})
    assert st2["stations"] == {"burners": 2, "oven_slots": 2,
                               "grill": False, "prep": 1}


def test_stations_validation_accepts_good_rejects_bad():
    ok = validate_people_doc(_people_doc(stations={"burners": 2,
                                                   "grill": True}))
    assert not [i for i in ok if i.severity == "error"]
    for bad in ({"burners": 0}, {"oven_slots": -1}, {"prep": 1.5},
                {"grill": "yes"}, {"burnners": 3}, "four burners"):
        issues = validate_people_doc(_people_doc(stations=bad))
        assert any(i.code == "bad_stations" for i in issues), bad


def test_stations_absent_is_fine():
    issues = validate_people_doc(_people_doc())
    assert not [i for i in issues if i.severity == "error"]
