"""Level A export round-trip checks over every golden case.

Each case: run pipeline -> export DXF -> re-open + re-parse the EXPORT ->
verify layers/entities preserved (canonical signatures), insert counts,
finite coordinates, containment, re-validated compliance, XDATA metadata.
"""
from __future__ import annotations

import pytest

from evals.checks import CHECK_IDS
from evals.pipeline_runner import list_cases

CASE_DIRS = list_cases()
CASE_IDS = [d.name for d in CASE_DIRS]


def test_dataset_complete():
    assert CASE_IDS == [f"case_{i:03d}" for i in range(1, 11)]
    for d in CASE_DIRS:
        for f in ("input.dxf", "rules.json", "expected.json", "notes.md"):
            assert (d / f).exists(), f"{d.name}/{f} missing"


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
def test_level_a_roundtrip(case_dir, get_level_a):
    checks = get_level_a(case_dir)
    failed = [c for c in checks if not c.passed]
    assert not failed, "\n".join(f"{c.id}: {c.detail}" for c in failed)


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
def test_all_defined_checks_executed(case_dir, get_level_a):
    ran = {c.id for c in get_level_a(case_dir)}
    assert ran == set(CHECK_IDS), f"missing checks: {set(CHECK_IDS) - ran}"


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
def test_export_file_created(case_dir, get_result):
    out = get_result(case_dir).export()
    assert out.exists() and out.stat().st_size > 0
