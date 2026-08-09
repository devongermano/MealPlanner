"""Contract-codegen spike tests — FAST tier (no LP solves here; the full
pipeline round-trip lives in test_contracts_roundtrip.py, slow tier).

What this module proves (Track B, ARCHITECTURE.md wiring rule):
1. structural mirrors validate REAL engine objects for the LP-free layers
   (io_yaml ValidationIssue, purchase rows, structured warnings);
2. the OpenAPI dump is deterministic (two subprocess dumps, identical bytes);
3. the CHECKED-IN openapi.json matches the live schemas (python-side drift
   gate — the npm-side one is packages/contracts/scripts/check.sh in CI);
4. the generated TS contains the contract type names (string check) and
   compiles under tsc --noEmit when npx+typescript are available (skips
   with a note otherwise — e.g. the python-only CI job).

Skips wholesale when the [service] extra is not installed: the engine core
must never depend on fastapi/pydantic.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic", reason="[service] extra not installed")
pytest.importorskip("fastapi", reason="[service] extra not installed")

from mealplan import costing, io_yaml, schemas  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
CONTRACTS = REPO / "packages" / "contracts"
DUMP = CONTRACTS / "scripts" / "dump_openapi.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

EXPECTED_TYPE_NAMES = (
    "WeekPlanResult", "SessionPlan", "CookSession", "ShoppingRow",
    "MenuInfo", "CostBlock", "VolumeBlock", "LibraryIdentity",
    "PlateResult", "DoctorReport", "StructuredWarning",
    "ValidationIssue", "ValidationErrorResponse",
    "SolveRequest", "SolveOptions", "Healthz",
)


# --------------------------------------------------------------------------- #
#  1. LP-free structural mirrors against real engine objects
# --------------------------------------------------------------------------- #
def test_validation_issue_mirror_matches_real_validation_error():
    """CATCHES: the ValidationIssue mirror drifting from io_yaml's dataclass
    (a renamed/added field would fail strict validation here)."""
    with pytest.raises(io_yaml.ValidationError) as ei:
        io_yaml.load(FIXTURES / "does_not_exist")
    for issue in ei.value.issues:
        m = schemas.ValidationIssue.model_validate(vars(issue))
        assert m.code == issue.code and m.severity in ("error", "warning")


def test_shopping_row_mirror_validates_real_purchase_tuples():
    """CATCHES: purchase() changing its row tuple arity/order without the
    contract layer noticing — the positional mapping lives in ShoppingRow."""
    ing, comps, people, settings = io_yaml.load(FIXTURES / "solo_lifter")
    rows, wp, wt = costing.purchase(comps, ing, list(comps))
    assert rows
    for row in rows:
        m = schemas.ShoppingRow.model_validate(row)
        assert m.ingredient == row[0]
        assert m.need_g == row[1]
        assert m.units == row[2]
        assert m.pack_g == row[3]
        assert m.leftover_g == row[4]
        assert m.perishable == row[5]
        assert m.keeps_days == row[6]


def test_structured_warning_mirror_keeps_code_specific_payload():
    """CATCHES: the warning mirror dropping code-specific keys (bounds,
    grams, …) that engine warnings carry beyond code+message."""
    w = dict(code="pin_adjusted", component="rice_pot", pinned_g=100.0,
             adjusted_g=120.0, bounds=[120, 400], unit_g=None,
             message="pin 100.0g on 'rice_pot' adjusted to 120g")
    m = schemas.StructuredWarning.model_validate(w)
    assert m.model_dump() == w


# --------------------------------------------------------------------------- #
#  2. OpenAPI dump: deterministic, and the checked-in copy is current
# --------------------------------------------------------------------------- #
def _dump(tmp_path: Path, name: str) -> bytes:
    out = tmp_path / name
    subprocess.run([sys.executable, str(DUMP), str(out)], check=True,
                   capture_output=True)
    return out.read_bytes()


def test_openapi_dump_is_deterministic_across_processes(tmp_path):
    """CATCHES: nondeterministic schema emission (set ordering, dict-order
    dependence) — two fresh-process dumps must be byte-identical, or the
    CI drift gate would flake."""
    a = _dump(tmp_path, "a.json")
    b = _dump(tmp_path, "b.json")
    assert a == b


def test_checked_in_openapi_matches_live_schemas(tmp_path):
    """CATCHES: schema/service edits without regeneration — the python-side
    twin of packages/contracts/scripts/check.sh."""
    live = _dump(tmp_path, "live.json")
    checked_in = (CONTRACTS / "openapi.json").read_bytes()
    assert json.loads(live) == json.loads(checked_in), (
        "openapi.json is stale — run `make contracts` and commit")


def test_openapi_components_cover_expected_types(tmp_path):
    spec = json.loads(_dump(tmp_path, "spec.json"))
    names = set(spec["components"]["schemas"])
    missing = [n for n in EXPECTED_TYPE_NAMES if n not in names]
    assert not missing, f"missing component schemas: {missing}"


# --------------------------------------------------------------------------- #
#  3. generated TS: names present, never hand-edited header, compiles
# --------------------------------------------------------------------------- #
def test_generated_ts_contains_expected_type_names():
    src = (CONTRACTS / "src" / "index.ts").read_text()
    assert src.startswith("// GENERATED — never hand-edit.")
    missing = [n for n in EXPECTED_TYPE_NAMES if f"{n}:" not in src]
    assert not missing, f"type names missing from generated TS: {missing}"


def test_generated_ts_compiles_under_tsc():
    """tsc --noEmit over the generated file IF npx can resolve the pinned
    typescript (node_modules installed); otherwise skip with a note."""
    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("npx not on PATH — tsc compile check runs in the "
                    "contracts CI workflow instead")
    probe = subprocess.run([npx, "--no-install", "tsc", "--version"],
                           cwd=CONTRACTS, capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("typescript not installed in packages/contracts "
                    "(run `npm install` there) — tsc check runs in the "
                    "contracts CI workflow instead")
    r = subprocess.run([npx, "--no-install", "tsc", "--noEmit",
                        "-p", "tsconfig.json"],
                       cwd=CONTRACTS, capture_output=True, text=True)
    assert r.returncode == 0, f"tsc --noEmit failed:\n{r.stdout}\n{r.stderr}"
