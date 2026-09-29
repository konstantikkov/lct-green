"""Repo-root test suites (evals: export round-trip, geometry invariants,
API-level pipeline e2e). Run from repo root:  python3 -m pytest tests/ -q

Shares one pipeline execution + one export per case across all suites via
the process-level cache in evals.pipeline_runner and the session fixtures
below.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from evals.pipeline_runner import list_cases, run_case  # noqa: E402

CASE_DIRS = list_cases()
CASE_IDS = [d.name for d in CASE_DIRS]


@pytest.fixture(scope="session")
def get_result():
    """get_result(case_dir) -> cached PipelineResult."""
    return run_case


@pytest.fixture(scope="session")
def get_level_a():
    """get_level_a(case_dir) -> cached Level A check results (runs export +
    re-parse exactly once per case per test session)."""
    from evals.checks import run_level_a_checks
    cache: dict[str, list] = {}

    def get(case_dir: Path):
        key = str(case_dir)
        if key not in cache:
            cache[key] = run_level_a_checks(run_case(case_dir))
        return cache[key]
    return get
