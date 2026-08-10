"""Test tiering (fast iteration — 2026-08-09).

Two tiers:
  make test-fast  ->  -m "not slow"   inner-loop, target < 30s wall
  make test       ->  everything      phase gates and CI only

Whole modules dominated by LP solves, subprocess runs, or full-pipeline
work are auto-marked `slow` here so the fast tier stays fast without
per-test annotation churn. A module belongs on this list when its wall
time is dominated by CBC solves rather than the logic under test.
"""
import pytest

SLOW_MODULES = {
    "test_artifacts_e2e",       # the ONE artifacts test with a real solve
    "test_capabilities",        # full pipelines on fixtures + golden
    "test_determinism",         # subprocess CLI runs
    "test_doctor_diagnostics",  # volume-floor bisection, ablations
    "test_extraction_smoke",    # full build_week on examples corpus
    "test_perf_budget",         # counted full pipelines
    "test_m1_phase1",           # scaling/pantry scenarios run plates + weeks
    "test_sessions_freshness",  # session_plan scenarios solve weeks
    "test_score_scale",         # frontier points run choose_menu
    "test_plate_bounds_replate",  # 20-seed property over plate solves
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.module.__name__ in SLOW_MODULES:
            item.add_marker(pytest.mark.slow)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: LP/subprocess-heavy; excluded from `make test-fast`")
