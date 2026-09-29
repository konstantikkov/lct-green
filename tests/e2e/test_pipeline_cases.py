"""API-level pipeline e2e: every golden case runs end-to-end (parse ->
classify -> constraints -> candidates -> optimize -> validate -> export)
and satisfies its expected.json contract.
"""
from __future__ import annotations

import pytest

from evals.checks import verify_expected
from evals.pipeline_runner import DATASET_DIR, list_cases, load_case_files

CASE_DIRS = list_cases()
CASE_IDS = [d.name for d in CASE_DIRS]


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
def test_case_satisfies_expected_json(case_dir, get_result, get_level_a):
    _, _, expected = load_case_files(case_dir)
    assert expected, f"{case_dir.name}: expected.json missing/empty"
    result = get_result(case_dir)
    checks = verify_expected(result, expected, level_a=get_level_a(case_dir))
    failed = [c for c in checks if not c.passed]
    assert not failed, "\n".join(f"{c.id}: {c.detail}" for c in failed)


def test_impossible_case_yields_empty_valid_plan(get_result):
    res = get_result(DATASET_DIR / "case_007")
    assert res.placements == []
    assert res.validation["valid"]
    assert res.metrics["treeCount"] == 0
    assert res.export().exists()  # export of an empty plan must still work


def test_ambiguous_layer_flagged_for_review(get_result):
    res = get_result(DATASET_DIR / "case_008")
    li = next(l for l in res.doc.layers if l.name == "СЕТИ_1")
    assert li.classification.category == "UTILITY"
    assert li.classification.requiresReview
    assert li.classification.confidence < 0.65


def test_invalid_geometry_repaired_or_fallback(get_result):
    res = get_result(DATASET_DIR / "case_009")
    # pipeline completed; boundary was either repaired or extents-fallback
    assert res.validation["valid"]
    assert res.cons.site_boundary_source in ("detected", "extents_fallback")


def test_disconnected_zones_planted_on_both_sides(get_result):
    res = get_result(DATASET_DIR / "case_004")
    south = [p for p in res.placements if p.y < 36]
    north = [p for p in res.placements if p.y > 44]
    assert south and north, "both disconnected zones must receive plantings"
