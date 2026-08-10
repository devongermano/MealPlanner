"""schedule.py — the TIMELINE COMPILER v0 (M1.12, PRD §4.0 cook-plan bar).

The cook plan should make it "thoughtless for someone to meal prep in the
fastest way possible". For ``cook_plan_style: timeline`` households — and
ONLY for them (recipe households never pay for this) — each cook session's
method-step fragments are compiled into ONE interleaved stream: start the
long passive work first, do active work while things simmer and roast, and
get told exactly when to set a timer.

Solver posture (ARCHITECTURE.md roadmap): this is the GREEDY list
scheduler — deterministic, RNG-free, zero LP. CP-SAT on calibrated
durations is a later milestone; nothing here may grow an OR-Tools
dependency. Durations are PROVISIONAL single-batch estimates until
cook-day calibration; the renderer says so and no test asserts on wall
accuracy.

Model
-----
- Tasks: one per method step per session component (batch counts from
  costing.session_plan), with the session's shared-prep consolidation
  (methods.consolidate_shared_prep) applied first — merged steps become
  ONE session-level task replacing every member step, wired into each
  member component's precedence chain.
- Precedence: strictly topological per component — a fragment's steps run
  in file order (prep -> cook -> finish is validated at load).
- The cook is a UNARY resource: at most one ACTIVE task at a time.
  Passive tasks (simmers, braises, oven time) hold only their station.
- Stations: capacities from settings.stations (model.STATIONS_DEFAULTS,
  provisional) — prep boards, stove burners, oven slots, grill. Station
  ``none`` (a soak on the counter) holds nothing.
- Oven co-residency is keyed by temperature bucket (PRD §4.0: "steaks
  finish at 425 because the veggies already made it a 425 oven"): tasks
  sharing ``oven_temp_f`` co-reside up to oven_slots; different temps
  sequence.
- Durations: ACTIVE task minutes scale sublinearly with batch count via
  the batch_time_factor convention (first batch full price, each marginal
  batch pays the factor share — the same convention costing.session_plan
  prices). PASSIVE durations are fixed (a 3x braise simmers no longer).
- Seeding: longest-passive-first — among startable tasks the scheduler
  prefers the component with the most passive minutes still ahead of it,
  so the long unattended work gets underway earliest.

Output: ``compile_session`` returns entries
``{t_start, t_end, component, step_text, station, mode, timers,
meanwhile, ...}`` plus the makespan, the naive-sequential total (the
honest "parallelization saved you N minutes" line), the cook's idle-hands
windows (where the renderer injects portioning-matrix work), components
that could not be scheduled (no fragment — they keep recipe blocks), and
structured warnings. Deterministic by construction: sorted iteration
everywhere, no RNG anywhere.
"""

from . import methods as methods_mod
from .model import STATIONS_DEFAULTS

SHARED = "__shared__"           # pseudo-component id for merged prep tasks


# --------------------------------------------------------------------------- #
#  durations — the batch_time_factor convention
# --------------------------------------------------------------------------- #
def scaled_duration_min(duration_min, mode, batches, factor):
    """Active minutes scale sublinearly with batch count (first batch full,
    marginal batches pay ``factor``); passive minutes are FIXED. Rounded to
    whole minutes (floor 1) — the timeline speaks in minutes, and the
    naive-sequential comparison uses the same rounding (apples to
    apples)."""
    if mode == "active" and batches > 1:
        duration_min = duration_min * (1 + factor * (batches - 1))
    return max(1, round(duration_min))


# --------------------------------------------------------------------------- #
#  task graph — fragments + shared prep -> precedence-wired tasks
# --------------------------------------------------------------------------- #
def _build_tasks(batches, comps, methods, factor):
    """Tasks for one session: shared-prep merged steps become one task
    each, substituted into every member component's chain; every other
    step is its own task chained to its predecessor. Returns
    ``(tasks, unscheduled)`` where tasks is ``{key: task}`` (key =
    ``(cid, idx)`` or ``(SHARED, j)``) and unscheduled the sorted cids
    with batches but no fragment."""
    cids = sorted(cid for cid, b in batches.items()
                  if b > 0 and methods.get(cid))
    unscheduled = sorted(cid for cid, b in batches.items()
                         if b > 0 and not methods.get(cid))
    merged, merged_keys = methods_mod.consolidate_shared_prep(
        {c: batches[c] for c in cids}, batches, comps, methods)
    # ownership: each (cid, idx) resolves to the task performing it — a
    # merged member maps to its group by the consolidator's OWN key,
    # (operation, ingredient); everything else performs itself.
    group_ix = {(m["operation"], m["ingredient"]): j
                for j, m in enumerate(merged)}
    owner, members = {}, {}
    for cid, idx in merged_keys:
        st = methods[cid][idx]
        gkey = (st["operation"],
                methods_mod.step_ingredient(st, comps[cid]))
        j = group_ix[gkey]
        owner[(cid, idx)] = (SHARED, j)
        members.setdefault(j, []).append((cid, idx))
    tasks = {}
    for cid in cids:
        for idx, st in enumerate(methods[cid]):
            key = owner.get((cid, idx), (cid, idx))
            if key[0] == SHARED:
                continue                     # materialized below
            iid, grams = methods_mod.scaled_step_grams(
                st, comps[cid], batches[cid])
            tasks[key] = dict(
                key=key, component=cid, step_text=st["text"],
                station=st["station"], mode=st["mode"],
                oven_temp_f=st.get("oven_temp_f"),
                operation=st.get("operation"),
                ingredient=iid, grams=grams,
                duration=scaled_duration_min(
                    st["duration_min"], st["mode"], batches[cid], factor),
                preds=set())
    for j, m in enumerate(merged):
        key = (SHARED, j)
        # duration: each member step's minutes, batch-scaled by ITS
        # component's batch count (active scales, passive fixed)
        dur = sum(scaled_duration_min(methods[cid][idx]["duration_min"],
                                      m["mode"], batches[cid], factor)
                  for cid, idx in sorted(members[j]))
        alloc = ", ".join(f"{p['grams']:g}g {p['component']}"
                          for p in m["parts"])
        tasks[key] = dict(
            key=key, component=SHARED,
            step_text=(f"{m['operation'].capitalize()} {m['total_g']:g}g "
                       f"{m['ingredient']} — {alloc} (shared prep, "
                       "merged once)"),
            station=m["station"], mode=m["mode"], oven_temp_f=None,
            operation=m["operation"], ingredient=m["ingredient"],
            grams=m["total_g"], duration=dur, preds=set(),
            parts=[dict(p) for p in m["parts"]])
    # precedence: each performed step waits for the task performing the
    # component's previous step (identity mapping through shared merges)
    for cid in cids:
        for idx in range(len(methods[cid])):
            key = owner.get((cid, idx), (cid, idx))
            if idx == 0:
                continue
            pred = owner.get((cid, idx - 1), (cid, idx - 1))
            if pred != key:
                tasks[key]["preds"].add(pred)
    return tasks, unscheduled


# --------------------------------------------------------------------------- #
#  the greedy list scheduler
# --------------------------------------------------------------------------- #
def compile_session(batches, comps, methods, settings):
    """Greedy interleaved schedule for one cook session. Deterministic,
    RNG-free, zero LP. See module docstring for rules; returns::

        {"entries": [{t_start, t_end, component, step_text, station,
                      mode, oven_temp_f, operation, ingredient, grams,
                      timers: [{at_min, minutes, label}], meanwhile}],
         "makespan_min": int, "naive_min": int,
         "idle_windows": [(start, end)],   # cook idle, passives running
         "unscheduled": [cid...],          # no fragment -> recipe block
         "warnings": [{code, where, message}]}
    """
    stations = {**STATIONS_DEFAULTS, **(settings.get("stations") or {})}
    factor = settings["batch_time_factor"]
    warnings = []
    tasks, unscheduled = _build_tasks(batches, comps, methods, factor)
    for cid in unscheduled:
        warnings.append(dict(
            code="no_fragment", where=cid,
            message=f"'{cid}' has no method fragment — it keeps its recipe "
                    "block and is not on the timeline"))
    if any(t["station"] == "grill" for t in tasks.values()) \
            and not stations["grill"]:
        warnings.append(dict(
            code="no_grill", where="settings.stations",
            message="station inventory says no grill but the session has "
                    "grill steps — scheduled on one grill anyway (swap the "
                    "dish or fix the inventory)"))
    capacity = {"prep": stations["prep"], "stove": stations["burners"],
                "oven": stations["oven_slots"], "grill": 1, "none": None}
    # longest-passive-first seeding: a component's rank is its total
    # passive minutes (static, deterministic); shared prep unlocks several
    # components at once, so it ranks by the best of its members.
    passive_total = {}
    for t in tasks.values():
        if t["component"] != SHARED and t["mode"] == "passive":
            passive_total[t["component"]] = \
                passive_total.get(t["component"], 0) + t["duration"]

    def _rank(t):
        if t["component"] == SHARED:
            pt = max((passive_total.get(p["component"], 0)
                      for p in t["parts"]), default=0)
            return (0, -pt, t["key"])          # shared-prep merged steps first
        return (1, -passive_total.get(t["component"], 0), t["key"])

    pending = dict(tasks)
    done, running, entries = set(), [], []
    in_use = {"prep": 0, "stove": 0, "oven": 0, "grill": 0}
    oven_temp = None
    active_running = False
    t = 0

    def _can_start(task):
        st = task["station"]
        if capacity[st] is not None and in_use[st] >= capacity[st]:
            return False
        if st == "oven" and oven_temp is not None \
                and task["oven_temp_f"] != oven_temp:
            return False                       # different temps sequence
        return True

    def _start(task, now):
        nonlocal oven_temp, active_running
        st = task["station"]
        if capacity[st] is not None:
            in_use[st] += 1
        if st == "oven":
            oven_temp = task["oven_temp_f"]
        if task["mode"] == "active":
            active_running = True
        end = now + task["duration"]
        timers = []
        if task["mode"] == "passive":
            timers.append(dict(
                at_min=end, minutes=task["duration"],
                label=(f"{task['step_text']}"
                       if task["component"] == SHARED else
                       f"{comps[task['component']]['name']}: "
                       f"{task['step_text']}")))
        entries.append(dict(
            t_start=now, t_end=end,
            component=(task["component"]
                       if task["component"] != SHARED else SHARED),
            step_text=task["step_text"], station=st, mode=task["mode"],
            oven_temp_f=task["oven_temp_f"], operation=task["operation"],
            ingredient=task["ingredient"], grams=task["grams"],
            timers=timers,
            meanwhile=(task["mode"] == "active"
                       and any(r[2]["mode"] == "passive" for r in running))))
        running.append((end, task["key"], task))
        del pending[task["key"]]

    while pending or running:
        started = True
        while started:
            started = False
            ready = sorted((tk for tk in pending.values()
                            if tk["preds"] <= done), key=_rank)
            # passives hold only their station — start every one that fits
            for task in ready:
                if task["mode"] == "passive" and _can_start(task):
                    _start(task, t)
                    started = True
            # the cook is unary: at most ONE active task at a time
            if not active_running:
                ready = sorted((tk for tk in pending.values()
                                if tk["preds"] <= done), key=_rank)
                for task in ready:
                    if task["mode"] == "active" and _can_start(task):
                        _start(task, t)
                        started = True
                        break
        if not running:
            if not pending:
                break                          # all scheduled
            # Nothing runs, nothing can start: a precedence cycle through
            # crossed shared-prep merges (fragment A dices onion then
            # carrot, fragment B the reverse). Deterministic escape: the
            # smallest-key blocked task forgets its unmet preds — the
            # schedule stays complete, and the warning says so (P8: no
            # silent drops).
            key = min(pending)
            pending[key]["preds"] &= done
            warnings.append(dict(
                code="precedence_cycle", where=str(key),
                message="shared-prep merges crossed component step order — "
                        f"scheduled {pending[key]['step_text']!r} despite "
                        "the cycle; verify the prep order by hand"))
            continue
        t = min(end for end, _, _ in running)
        for end, key, task in sorted(running):
            if end > t:
                continue
            done.add(key)
            st = task["station"]
            if capacity[st] is not None:
                in_use[st] -= 1
            if task["mode"] == "active":
                active_running = False
        running = [r for r in running if r[0] > t]
        if in_use["oven"] == 0:
            oven_temp = None
    makespan = max((e["t_end"] for e in entries), default=0)
    naive = sum(tk["duration"] for tk in tasks.values())
    return dict(entries=sorted(entries,
                               key=lambda e: (e["t_start"], e["t_end"],
                                              e["component"])),
                makespan_min=makespan, naive_min=naive,
                idle_windows=_idle_windows(entries, makespan),
                unscheduled=unscheduled, warnings=warnings)


def _idle_windows(entries, makespan):
    """Windows where the cook's hands are free (no active task) but the
    kitchen is still working (>= 1 passive task running) — where the
    renderer injects portioning-matrix work. Sorted, merged, ints."""
    marks = sorted({e["t_start"] for e in entries}
                   | {e["t_end"] for e in entries} | {0, makespan})
    windows = []
    for a, b in zip(marks, marks[1:]):
        active = any(e["mode"] == "active"
                     and e["t_start"] < b and e["t_end"] > a
                     for e in entries)
        passive = any(e["mode"] == "passive"
                      and e["t_start"] < b and e["t_end"] > a
                      for e in entries)
        if passive and not active:
            if windows and windows[-1][1] == a:
                windows[-1] = (windows[-1][0], b)
            else:
                windows.append((a, b))
    return windows


def format_min(m):
    """Relative timestamp — minutes -> ``H:MM`` from session start 0:00."""
    m = int(m)
    return f"{m // 60}:{m % 60:02d}"
