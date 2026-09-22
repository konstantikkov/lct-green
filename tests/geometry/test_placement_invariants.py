"""Geometry/placement property invariants across the golden dataset:
same-seed determinism, pairwise spacing, containment, finite coordinates.
"""
from __future__ import annotations

import math

import pytest
from shapely.geometry import Point

from evals.pipeline_runner import DATASET_DIR, list_cases, run_case_uncached

CASE_DIRS = list_cases()
CASE_IDS = [d.name for d in CASE_DIRS]

# representative subset for the (expensive) double-run determinism property:
# empty site, obstacles+existing trees, large multi-optima site
DETERMINISM_CASES = ["case_001", "case_006", "case_010"]


@pytest.mark.parametrize("case_id", DETERMINISM_CASES)
def test_same_seed_is_deterministic(case_id):
    d = DATASET_DIR / case_id
    p1 = run_case_uncached(d)
    p2 = run_case_uncached(d)
    assert [(p.x, p.y) for p in p1.placements] == [(p.x, p.y) for p in p2.placements]
    assert p1.metrics == p2.metrics


def test_different_seed_changes_layout():
    d = DATASET_DIR / "case_010"  # many equally-valid optima -> seed must matter
    a = run_case_uncached(d, seed=42)
    b = run_case_uncached(d, seed=1337)
    assert [(p.x, p.y) for p in a.placements] != [(p.x, p.y) for p in b.placements]


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
def test_pairwise_spacing(case_dir, get_result):
    res = get_result(case_dir)
    rule = res.registry.get("TREE_TREE_MIN_SPACING")
    min_d = rule.effective_distance_m if rule else None
    if min_d is None:
        pytest.skip("spacing rule inactive in this case's rule set")
    pts = [(p.x, p.y) for p in res.placements if p.kind == "TREE"]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.dist(pts[i], pts[j])
            assert d >= min_d - 1e-9, \
                f"{case_dir.name}: trees {i},{j} at {d:.3f} m < {min_d} m"


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
def test_placements_inside_allowed_area(case_dir, get_result):
    res = get_result(case_dir)
    allowed = res.cons.allowed_area.buffer(1e-6)
    outside = [p.id for p in res.placements if not allowed.covers(Point(p.x, p.y))]
    assert not outside, f"{case_dir.name}: outside allowed area: {outside}"


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
def test_coordinates_finite(case_dir, get_result):
    res = get_result(case_dir)
    for p in res.placements:
        assert math.isfinite(p.x) and math.isfinite(p.y) and math.isfinite(p.crownRadiusM), \
            f"{case_dir.name}: non-finite coordinates on {p.id}"


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
def test_validator_reports_zero_hard_violations(case_dir, get_result):
    res = get_result(case_dir)
    assert res.validation["valid"], res.validation["hardViolations"][:3]
