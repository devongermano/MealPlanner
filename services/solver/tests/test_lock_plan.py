"""M1.3 — locked plan artifacts (PRD §4.3 / §8.1 / §8.2). Slow tier: lock
and verify-plan genuinely solve.

Covered here, all under tmp_path (plans/ in a real checkout is git-tracked
data — the artifact IS the record — but tests never write there):

- key: plans/<primary-trip-date>/ where the key is --date advanced by
  sorted(shop_days)[0] days (the fixture uses shop_days [1] so the offset
  is actually exercised);
- plan.yaml content: schema_version, engine version, verbatim inputs
  snapshot (three library docs + pantry + overrides + seed), sha256 over
  the canonical JSON of exactly that snapshot, menu, per-person day
  portions, session plan, warnings/relax tiers, empty veto_history;
- the three M1.1 deliverables rendered alongside;
- immutability: a second lock REFUSES (exit 3) unless --supersede, which
  RENAMES the old file — bytes identical before/after — and records what
  was superseded;
- reproducibility round trip: a fresh lock verifies clean; a tampered
  portion fails the re-solve compare; an altered snapshot fails the hash
  check. Both fail loudly (exit 3).
"""

import hashlib
import json

import pytest
import yaml

from mealplan import cli, lockplan

SEED = 0
DATE = "2026-08-09"
KEY = "2026-08-10"        # DATE advanced by shop_days[0] == 1


def _write_lib(tmp_path):
    ing_doc = {"schema_version": 1, "ingredients": {
        "stuff": {"p": 20.0, "f": 10.0, "c": 12.0, "perishable": False,
                  "pack_g": 500, "keeps_days": 999, "cost": 4.0,
                  "tags": []}}}
    comp_doc = {"schema_version": 1, "components": [
        {"id": "stew", "name": "stew", "cuisine": "test", "role": "main",
         "yield_g": 2000, "serve_g": {"min": 100, "max": 500},
         "keeps_days": 7, "active_min": 30, "ingredients": {"stuff": 2000}},
        {"id": "rice", "name": "rice", "cuisine": "test", "role": "starch",
         "yield_g": 2000, "serve_g": {"min": 50, "max": 500},
         "keeps_days": 7, "active_min": 10, "ingredients": {"stuff": 2000}}]}
    # shop day 1, cook day 1: the primary trip is day 1, so the artifact
    # key is DATE + 1 — the offset is real, not the [0] default
    ppl_doc = {"schema_version": 1,
               "people": {"p1": {"targets": {"protein": 80, "fat": 45,
                                             "carb": 55},
                                 "tolerance": 0.2}},
               "settings": {"days": 3, "active_min_budget": 600,
                            "max_days_same_component": 7, "cook_days": [1],
                            "shop_days": [1]}}
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ingredients.yaml").write_text(yaml.safe_dump(ing_doc))
    (lib / "components.yaml").write_text(yaml.safe_dump(comp_doc))
    (lib / "people.yaml").write_text(yaml.safe_dump(ppl_doc))
    return lib


def _lock(lib, plans, extra=(), capsys=None):
    code = 0
    try:
        cli.main(["lock", "--library", str(lib), "--date", DATE,
                  "--seed", str(SEED), "--n", "2", "--plans", str(plans),
                  "--json", *extra])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    doc = json.loads(capsys.readouterr().out) if capsys else None
    return code, doc


def _verify(path, capsys):
    code = 0
    try:
        cli.main(["verify-plan", str(path), "--json"])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture(scope="module")
def locked(tmp_path_factory):
    """ONE real lock shared by the read-only assertions below."""
    tmp = tmp_path_factory.mktemp("lockplan")
    lib = _write_lib(tmp)
    plans = tmp / "plans"
    code = 0
    try:
        cli.main(["lock", "--library", str(lib), "--date", DATE,
                  "--seed", str(SEED), "--n", "2", "--plans", str(plans)])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    assert code == 0
    return dict(tmp=tmp, lib=lib, plans=plans,
                plan_dir=plans / KEY, plan_path=plans / KEY / "plan.yaml")


def test_lock_writes_under_primary_trip_key(locked):
    """--date 2026-08-09 + shop_days [1] ⇒ plans/2026-08-10/ — the key is
    the plan date advanced by sorted(shop_days)[0] days (PRD §8.2)."""
    assert locked["plan_path"].exists()
    assert locked["plan_dir"].name == KEY


def test_plan_doc_contents(locked):
    doc = yaml.safe_load(locked["plan_path"].read_text())
    assert doc["schema_version"] == 1
    assert doc["schema"] == "mealplan/v2"
    assert doc["engine_version"]
    assert doc["key"] == KEY and doc["plan_date"] == DATE
    # verbatim inputs snapshot: the three library documents + pantry +
    # overrides + seed
    snap = doc["inputs"]
    assert snap["seed"] == SEED
    assert snap["pantry"] is None
    assert snap["overrides"]["n"] == 2
    assert snap["library"]["components"]["components"][0]["id"] == "stew"
    assert (snap["library"]["people"]["settings"]["shop_days"] == [1])
    # the hash is sha256 over the canonical JSON of exactly the snapshot
    assert doc["inputs_sha256"] == lockplan.inputs_sha256(snap)
    # solved outputs: menu, per-person day portions, session plan
    assert sorted(doc["menu"]) == ["rice", "stew"]
    assert list(doc["portions"]) == ["p1"]
    assert len(doc["portions"]["p1"]) == 3            # days=3
    assert "sessions" in doc["session_plan"]
    assert doc["relax_tiers"]["p1"]                   # recorded per day
    # reserved for M2 — present and empty
    assert doc["veto_history"] == []
    assert doc["supersedes"] == []


def test_three_deliverables_rendered_alongside(locked):
    names = {p.name for p in locked["plan_dir"].iterdir()}
    assert {"plan.yaml", "shopping_list.md", "cook_plan.md",
            "eat_p1.md"} <= names


def test_fresh_lock_verifies_clean(locked, capsys):
    code, doc = _verify(locked["plan_path"], capsys)
    assert code == 0, doc
    assert doc["ok"] is True
    r = doc["result"]
    assert (r["hash_ok"] and r["menu_ok"] and r["portions_ok"]
            and r["session_plan_ok"])
    assert r["verified"] is True


def test_second_lock_refuses_exit_3(locked, capsys):
    before = locked["plan_path"].read_bytes()
    code, doc = _lock(locked["lib"], locked["plans"], capsys=capsys)
    assert code == 3
    assert doc["ok"] is False
    assert doc["error"]["code"] == "plan_exists"
    # and the existing artifact is untouched
    assert locked["plan_path"].read_bytes() == before


def test_supersede_renames_never_rewrites(tmp_path, capsys):
    lib = _write_lib(tmp_path)
    plans = tmp_path / "plans"
    code, first = _lock(lib, plans, capsys=capsys)
    assert code == 0
    plan_path = plans / KEY / "plan.yaml"
    old_bytes = plan_path.read_bytes()
    old_sha = hashlib.sha256(old_bytes).hexdigest()

    code, second = _lock(lib, plans, extra=["--supersede", "--seed", "1"],
                         capsys=capsys)
    assert code == 0
    assert second["result"]["supersedes"] == ["plan.superseded-1.yaml"]
    # the old plan was RENAMED — content byte-identical, hash before ==
    # hash after (immutability, PRD §8.1)
    moved = plans / KEY / "plan.superseded-1.yaml"
    assert moved.exists()
    assert hashlib.sha256(moved.read_bytes()).hexdigest() == old_sha
    # and the new plan.yaml names what it superseded
    doc = yaml.safe_load(plan_path.read_text())
    assert doc["supersedes"] == ["plan.superseded-1.yaml"]
    assert doc["inputs"]["seed"] == 1


def test_tampered_portions_fail_verify_loudly(locked, tmp_path, capsys):
    doc = yaml.safe_load(locked["plan_path"].read_text())
    day0 = doc["portions"]["p1"][-1]
    cid = sorted(day0)[0]
    day0[cid] = day0[cid] + 50                 # nudge one solved portion
    bad = tmp_path / "tampered.yaml"
    bad.write_text(yaml.safe_dump(doc, sort_keys=False))
    code, out = _verify(bad, capsys)
    assert code == 3
    assert out["ok"] is False
    assert out["error"]["code"] == "verify_failed"
    assert out["error"]["details"]["hash_ok"] is True     # snapshot intact
    assert out["error"]["details"]["portions_ok"] is False


def test_tampered_session_plan_fails_verify_loudly(locked, tmp_path,
                                                   capsys):
    """Hand-editing session_plan (the batch counts / minutes the household
    acts on) is covered by neither the inputs hash nor the portions check —
    verify-plan must compare it against the re-solve (M1.3 hardening)."""
    doc = yaml.safe_load(locked["plan_path"].read_text())
    s0 = doc["session_plan"]["sessions"][0]
    cid = sorted(s0["batches"])[0]
    s0["batches"][cid] = s0["batches"][cid] + 7    # nudge a batch count
    bad = tmp_path / "tampered_sp.yaml"
    bad.write_text(yaml.safe_dump(doc, sort_keys=False))
    code, out = _verify(bad, capsys)
    assert code == 3
    assert out["ok"] is False
    assert out["error"]["code"] == "verify_failed"
    assert out["error"]["details"]["hash_ok"] is True      # snapshot intact
    assert out["error"]["details"]["portions_ok"] is True  # plates intact
    assert out["error"]["details"]["session_plan_ok"] is False


def test_altered_snapshot_fails_hash_check(locked, tmp_path, capsys):
    doc = yaml.safe_load(locked["plan_path"].read_text())
    doc["inputs"]["seed"] = 99                 # rewrite history
    bad = tmp_path / "altered.yaml"
    bad.write_text(yaml.safe_dump(doc, sort_keys=False))
    code, out = _verify(bad, capsys)
    assert code == 3
    assert out["error"]["code"] == "verify_failed"
    assert out["error"]["details"]["hash_ok"] is False
    assert "hash mismatch" in out["error"]["message"]


def test_lock_without_date_exits_4(locked, capsys):
    code = 0
    try:
        cli.main(["lock", "--library", str(locked["lib"]),
                  "--plans", str(locked["plans"]), "--json", "--n", "2"])
    except SystemExit as e:
        code = e.code
    doc = json.loads(capsys.readouterr().out)
    assert code == 4
    assert doc["error"]["code"] == "date_required"
    # the help/error documents the key precisely
    assert "primary trip date" in doc["error"]["message"]
