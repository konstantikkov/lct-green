"""Regulatory verdict separation (iteration 5) tests.

Covers the frozen contract in docs/API_CONTRACT.md: deterministic PASS/FAIL/
UNVERIFIED/NOT_APPLICABLE per-check semantics, the COMPLIANT/NON_COMPLIANT/
NEEDS_REVIEW status matrix, blockers, coverage, rule roles/conditions,
visibility triangles, and the drainage/storm-sewer split.
"""
from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import LineString, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import classification  # noqa: E402
from app.services.constraints import (build_constraints,  # noqa: E402
                                      detect_road_crossings,
                                      visibility_triangle_polygons)
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.explain import build_explanation  # noqa: E402
from app.services.candidates import Candidate  # noqa: E402
from app.services.optimizer import hard_filter_candidates  # noqa: E402
from app.services.geometry import GeometryIndex, build_geometry_index  # noqa: E402
from app.services.rules import (RegulatoryRule, RuleAssumption,  # noqa: E402
                                RuleRegistry, RuleSource, load_registry)
from app.services.validator import (Placement, RuleDistanceOracle,  # noqa: E402
                                    validate_plan)

SITE = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])


def _mini_idx(with_utility: bool = False) -> GeometryIndex:
    idx = GeometryIndex()
    idx.by_entity["bld1"] = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    idx.by_category["BUILDING"] = ["bld1"]
    if with_utility:
        idx.by_entity["util1"] = LineString([(60, 0), (60, 100)])
        idx.by_category["UTILITY"] = ["util1"]
    return idx


def _building_rule(status: str = "verified", role: str = "regulatory",
                   condition: dict | None = None) -> RegulatoryRule:
    verified = status == "verified"
    return RegulatoryRule(
        id="TREE_BUILDING_MIN_DISTANCE",
        appliesTo={"planting_type": ["TREE"], "obstacle_type": ["BUILDING"]},
        constraint={"type": "min_distance",
                    "distance_m": 5.0 if verified else None, "units": "m"},
        source=RuleSource(document="СП 42.13330.2016"),
        status=status, severity="hard", role=role, condition=condition or {},
        assumption=None if verified else RuleAssumption(
            assumedDistanceM=5.0, enabled=True, note="demo"))


def _inside_rule() -> RegulatoryRule:
    return RegulatoryRule(
        id="PLANTING_INSIDE_SITE_BOUNDARY",
        appliesTo={"planting_type": ["TREE", "SHRUB"],
                   "obstacle_type": ["SITE_BOUNDARY"]},
        constraint={"type": "inside", "distance_m": 0.0, "units": "m"},
        source=RuleSource(document="project_geometry"), status="verified")


def _registry(*rules) -> RuleRegistry:
    return RuleRegistry(version="test", rules=list(rules))


def _tree(x, y, crown=2.0, pid="t1") -> Placement:
    return Placement(id=pid, kind="TREE", speciesId="tree_test",
                     x=x, y=y, crownRadiusM=crown)


# ---------------------------------------------------------------------------
# status matrix: verified-pass / assumed-pass / violation
# ---------------------------------------------------------------------------

def test_matrix_verified_pass_is_compliant():
    reg = _registry(_building_rule("verified",
                                   condition={"crown_diameter_max_m": 5.0}),
                    _inside_rule())
    report = validate_plan([_tree(50, 50, crown=2.0)], _mini_idx(), reg, SITE, SITE)
    r = report["regulatory"]
    assert r["status"] == "COMPLIANT"
    assert r["blockers"] == []
    assert r["checkCounts"] == {"pass": 2, "fail": 0, "unverified": 0,
                                "notApplicable": 0}
    assert r["coverage"]["regulatoryCoverage"] == 1.0
    assert r["coverage"]["parser"]["siteBoundaryDetected"] is True


def test_matrix_assumed_pass_is_needs_review():
    """Geometry OK against an ASSUMED demo value must NEVER read as
    compliant — this is the audit's core finding."""
    reg = _registry(_building_rule("missing_source_data"), _inside_rule())
    report = validate_plan([_tree(50, 50)], _mini_idx(), reg, SITE, SITE)
    r = report["regulatory"]
    assert report["valid"] is True           # geometrically fine
    assert r["status"] == "NEEDS_REVIEW"     # but NOT compliant
    assert r["blockers"] == ["UNVERIFIED_HARD_RULES:1"]
    assert r["checkCounts"]["unverified"] == 1
    assert r["checkCounts"]["pass"] == 1     # inside-site (verified, detected)
    assert r["coverage"]["regulatoryCoverage"] == pytest.approx(0.5)


@pytest.mark.parametrize("status", ["verified", "missing_source_data"])
def test_matrix_violation_is_non_compliant(status):
    """FAIL in the safe direction: assumed values also fail geometrically
    (and still populate hardViolations for the repair loop)."""
    reg = _registry(_building_rule(status), _inside_rule())
    report = validate_plan([_tree(12, 12)], _mini_idx(), reg, SITE, SITE)
    r = report["regulatory"]
    assert r["status"] == "NON_COMPLIANT"
    assert report["hardViolations"] and \
        report["hardViolations"][0]["ruleId"] == "TREE_BUILDING_MIN_DISTANCE"
    assert r["checkCounts"]["fail"] == 1


def test_matrix_no_obstacles_not_applicable():
    reg = _registry(_building_rule("verified"), _inside_rule())
    idx = GeometryIndex()  # empty drawing
    report = validate_plan([_tree(50, 50)], idx, reg, SITE, SITE)
    r = report["regulatory"]
    assert r["checkCounts"]["notApplicable"] == 1
    assert r["status"] == "COMPLIANT"  # only the verified inside check applies


# ---------------------------------------------------------------------------
# crown-diameter applicability condition (СП 42 табл. 9.1 ≤ 5 м)
# ---------------------------------------------------------------------------

def test_crown_condition_unconfirmed_makes_verified_check_unverified():
    reg = _registry(_building_rule("verified",
                                   condition={"crown_diameter_max_m": 5.0}),
                    _inside_rule())
    # crown diameter 7 m > 5 m: табл. 9.1 not sufficient even though the
    # value itself is verified and geometry passes
    report = validate_plan([_tree(50, 50, crown=3.5)], _mini_idx(), reg, SITE, SITE)
    r = report["regulatory"]
    assert r["status"] == "NEEDS_REVIEW"
    assert r["checkCounts"]["unverified"] == 1
    assert r["coverage"]["regulatoryCoverage"] == pytest.approx(0.5)
    # small crown confirms the condition → PASS
    report2 = validate_plan([_tree(50, 50, crown=2.0)], _mini_idx(), reg, SITE, SITE)
    assert report2["regulatory"]["status"] == "COMPLIANT"


def test_crown_condition_reason_in_explanation():
    reg = _registry(_building_rule("verified",
                                   condition={"crown_diameter_max_m": 5.0}))
    idx = _mini_idx()
    exp = build_explanation(_tree(50, 50, crown=3.5), 0, reg,
                            RuleDistanceOracle(idx), SITE)
    c = next(c for c in exp["checks"] if c["ruleId"] == "TREE_BUILDING_MIN_DISTANCE")
    assert c["result"] == "UNVERIFIED"
    assert "крона 7.0 м > 5 м" in c["reason"]


# ---------------------------------------------------------------------------
# site-boundary provenance
# ---------------------------------------------------------------------------

def test_inferred_boundary_sets_blocker_and_unverified_inside_check():
    reg = _registry(_building_rule("verified",
                                   condition={"crown_diameter_max_m": 5.0}),
                    _inside_rule())
    report = validate_plan([_tree(50, 50)], _mini_idx(), reg, SITE, SITE,
                           site_boundary_source="inferred_drawing_extent")
    r = report["regulatory"]
    assert "SITE_BOUNDARY_UNKNOWN" in r["blockers"]
    assert r["status"] == "NEEDS_REVIEW"
    assert r["checkCounts"]["unverified"] == 1  # the inside check
    assert r["coverage"]["parser"]["siteBoundaryDetected"] is False
    # explanation carries the human reason
    exp = build_explanation(_tree(50, 50), 0, reg,
                            RuleDistanceOracle(_mini_idx()), SITE,
                            site_boundary_source="inferred_drawing_extent")
    c = next(c for c in exp["checks"]
             if c["ruleId"] == "PLANTING_INSIDE_SITE_BOUNDARY")
    assert c["result"] == "UNVERIFIED"
    assert "INFERRED_DRAWING_EXTENT" in c["reason"]


# ---------------------------------------------------------------------------
# safe_buffer role: never PASS, still enforced geometrically
# ---------------------------------------------------------------------------

def test_safe_buffer_never_passes_but_still_repairs():
    buffer_rule = RegulatoryRule(
        id="TREE_UTILITY_GENERIC_MIN_DISTANCE",
        appliesTo={"planting_type": ["TREE"], "obstacle_type": ["UTILITY"]},
        constraint={"type": "min_distance", "distance_m": 2.0, "units": "m"},
        source=RuleSource(document="tech"), status="verified",
        severity="hard", role="safe_buffer")
    reg = _registry(buffer_rule, _inside_rule())
    idx = _mini_idx(with_utility=True)
    # geometry OK → UNVERIFIED (a technological buffer is not a norm check)
    ok = validate_plan([_tree(30, 50)], idx, reg, SITE, SITE)
    assert ok["regulatory"]["status"] == "NEEDS_REVIEW"
    assert ok["regulatory"]["checkCounts"]["unverified"] == 1
    # geometry violated → FAIL + hardViolation (safe direction, repairable)
    bad = validate_plan([_tree(60.5, 50)], idx, reg, SITE, SITE)
    assert bad["regulatory"]["status"] == "NON_COMPLIANT"
    assert bad["hardViolations"][0]["ruleId"] == "TREE_UTILITY_GENERIC_MIN_DISTANCE"


# ---------------------------------------------------------------------------
# YAML registry changes
# ---------------------------------------------------------------------------

def test_drainage_rule_value_and_split():
    reg = load_registry()
    r = reg.get("TREE_UTILITY_DRAINAGE_MIN_DISTANCE")
    assert r.assumption.assumedDistanceM == 2.0  # СП 42 табл. 9.1: дренаж 2.0
    assert "дренаж" in r.source.title.lower()
    assert "ливнев" not in r.source.title.lower()  # storm sewer split out
    assert reg.get("TREE_UTILITY_SEWER_MIN_DISTANCE").assumption.assumedDistanceM == 1.5


def test_storm_sewer_classified_as_sewer_not_drainage():
    assert classification.classify_layer_name("К2_ЛИВНЕВАЯ_КАНАЛИЗАЦИЯ").category \
        == "UTILITY_SEWER"
    assert classification.classify_layer_name("ДРЕНАЖ").category == "UTILITY_DRAINAGE"


def test_generic_utility_rule_tree_only_safe_buffer():
    reg = load_registry()
    r = reg.get("TREE_UTILITY_GENERIC_MIN_DISTANCE")
    assert r.appliesTo["planting_type"] == ["TREE"]
    assert r.role == "safe_buffer"
    shrub_rules = {x.id for x in reg.rules_for("SHRUB", {"min_distance"})}
    assert "TREE_UTILITY_GENERIC_MIN_DISTANCE" not in shrub_rules


def test_shrub_near_unknown_utility_not_flagged_by_tree_rule():
    reg = load_registry()
    idx = _mini_idx(with_utility=True)
    shrub = Placement(id="s1", kind="SHRUB", speciesId="shrub_lilac",
                      x=60.5, y=50, crownRadiusM=1.5)
    report = validate_plan([shrub], idx, reg, SITE, SITE)
    assert not any(v["ruleId"] == "TREE_UTILITY_GENERIC_MIN_DISTANCE"
                   for v in report["hardViolations"])


def test_spacing_rules_are_design_heuristics():
    reg = load_registry()
    for rid in ("TREE_TREE_MIN_SPACING", "SHRUB_SHRUB_MIN_SPACING"):
        r = reg.get(rid)
        assert r.severity == "recommended"
        assert r.role == "design_heuristic"
        assert "эвристика" in r.note


def test_rule_api_exposes_role_and_condition():
    reg = load_registry()
    api = reg.get("TREE_BUILDING_MIN_DISTANCE").to_api()
    assert api["role"] == "regulatory"
    assert api["condition"] == {"crownDiameterMaxM": 5.0}
    assert reg.get("TREE_UTILITY_GENERIC_MIN_DISTANCE").to_api()["role"] == "safe_buffer"
    assert reg.get("TREE_TREE_MIN_SPACING").to_api()["role"] == "design_heuristic"


def test_visibility_rule_verified_by_623_pp():
    """623-ПП (МГСН 1.02-02) п. 9.4.2: 8 x 40 м at 40 km/h, plants ≤ 0,5 м."""
    reg = load_registry()
    r = reg.get("VISIBILITY_TRIANGLE_NO_TALL_PLANTS")
    assert r is not None
    assert r.constraint["type"] == "custom_geometry"
    assert r.status == "verified" and r.effective_source == "verified"
    assert "623-ПП" in r.source.document and r.source.clause == "п. 9.4.2"
    assert "8 x 40 м" in r.source.excerpt and "0,5 м" in r.source.excerpt
    assert sorted(r.constraint["legs_m"]) == [8.0, 40.0]
    assert r.severity == "hard" and r.role == "regulatory"
    # speed / signalling are not in the drawing — applicability is a condition
    assert set(r.condition) == {"unsignalized_crossing", "speed_limit_kmh_max"}


# ---------------------------------------------------------------------------
# visibility triangles on a synthetic road crossing
# ---------------------------------------------------------------------------

def _crossing_doc(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    for name, color in (("ГРАНИЦА_УЧАСТКА", 1), ("ДОРОГИ_ПРОЕЗДЫ", 252)):
        doc.layers.new(name, dxfattribs={"color": color})
    msp.add_lwpolyline([(0, 0), (200, 0), (200, 200), (0, 200)], close=True,
                       dxfattribs={"layer": "ГРАНИЦА_УЧАСТКА"})
    # two long crossing roads (a real intersection at 100,100)
    msp.add_line((20, 100), (180, 100), dxfattribs={"layer": "ДОРОГИ_ПРОЕЗДЫ"})
    msp.add_line((100, 20), (100, 180), dxfattribs={"layer": "ДОРОГИ_ПРОЕЗДЫ"})
    # exploded-dash noise: short crossing segments must be ignored (< 10 m)
    msp.add_line((148, 150), (152, 150), dxfattribs={"layer": "ДОРОГИ_ПРОЕЗДЫ"})
    msp.add_line((150, 148), (150, 152), dxfattribs={"layer": "ДОРОГИ_ПРОЕЗДЫ"})
    p = tmp_path / "crossing.dxf"
    doc.saveas(str(p))
    parsed = parse_dxf(p, "vis-doc", "crossing.dxf")
    classification.classify_document(parsed)
    return parsed


def test_visibility_triangle_detection_and_validation(tmp_path):
    doc = _crossing_doc(tmp_path)
    idx = build_geometry_index(doc)
    crossings = detect_road_crossings(idx)
    assert len(crossings) == 1, "one clustered crossing, dashes ignored"
    p, da, db = crossings[0]
    assert (round(p.x), round(p.y)) == (100, 100)
    tris = visibility_triangle_polygons(p, da, db)
    assert len(tris) == 4  # (P, P±dA·40, P±dB·8) and (P, P±dA·8, P±dB·40)

    reg = load_registry()
    cons = build_constraints(doc, idx, reg)
    assert cons.site_boundary_kind == "detected"
    vis = cons.visibility_zones()
    assert len(vis) == 1
    assert vis[0].effectiveSource == "verified"
    assert vis[0].ruleId == "VISIBILITY_TRIANGLE_NO_TALL_PLANTS"
    # zones join exclusion zones → the allowed area avoids them
    from shapely.geometry import Point
    assert not cons.allowed_area.covers(Point(110, 102.5))

    inside = Placement(id="in_tri", kind="TREE", speciesId="tree_linden",
                       x=110, y=102.5, crownRadiusM=3.5)
    outside = Placement(id="far", kind="TREE", speciesId="tree_linden",
                        x=30, y=170, crownRadiusM=3.5)
    report = validate_plan([inside, outside], idx, reg, cons.site_boundary,
                           cons.allowed_area,
                           site_boundary_source=cons.site_boundary_kind,
                           visibility_zones=vis)
    vis_viol = [v for v in report["hardViolations"]
                if v["ruleId"] == "VISIBILITY_TRIANGLE_NO_TALL_PLANTS"]
    assert [v["plantingId"] for v in vis_viol] == ["in_tri"]
    r = report["regulatory"]
    assert r["status"] == "NON_COMPLIANT"
    assert r["coverage"]["parser"]["crossingsDetected"] == 1
    assert r["coverage"]["parser"]["roadsClassified"] is True

    # outside the triangle the check is never PASS: the speed limit and
    # signalling are unknown, so the verified norm's applicability is not
    rowan = Placement(id="rowan", kind="TREE", speciesId="tree_rowan",
                      x=30, y=170, crownRadiusM=2.5)
    report = validate_plan([rowan], idx, reg, cons.site_boundary, cons.allowed_area,
                           site_boundary_source=cons.site_boundary_kind,
                           visibility_zones=vis)
    assert report["valid"]
    assert report["regulatory"]["status"] == "NEEDS_REVIEW"
    assert report["regulatory"]["blockers"] == ["UNVERIFIED_HARD_RULES:1"]


def test_visibility_disabled_when_rule_off(tmp_path):
    doc = _crossing_doc(tmp_path)
    idx = build_geometry_index(doc)
    reg = load_registry()
    reg.get("VISIBILITY_TRIANGLE_NO_TALL_PLANTS").enabled = False
    cons = build_constraints(doc, idx, reg)
    assert cons.visibility_zones() == []


# ---------------------------------------------------------------------------
# 623-ПП п. 4.2.8 — species-specific setbacks from heat mains
# ---------------------------------------------------------------------------

def _heat_idx() -> GeometryIndex:
    idx = GeometryIndex()
    idx.by_entity["heat1"] = LineString([(60, 0), (60, 100)])
    idx.by_category["UTILITY_HEAT"] = ["heat1"]
    return idx


def _violations(report, rule_id):
    return [v["plantingId"] for v in report["hardViolations"] if v["ruleId"] == rule_id]


def test_heat_main_species_rules_verified():
    reg = load_registry()
    r2 = reg.get("HEAT_MAIN_SENSITIVE_SPECIES_2M")
    r4 = reg.get("HEAT_MAIN_SENSITIVE_SPECIES_4M")
    for r in (r2, r4):
        assert r.effective_source == "verified" and r.role == "regulatory"
        assert "623-ПП" in r.source.document and r.source.clause == "п. 4.2.8"
        assert "ближе 3-4 м" in r.source.excerpt
        assert not r.condition, "п. 4.2.8 has no crown-diameter limit"
    assert r2.effective_distance_m == 2.0 and r4.effective_distance_m == 4.0
    assert {"Tilia", "Acer", "Syringa", "Lonicera"} == set(r2.species_genera)
    assert {"Populus", "Crataegus", "Cotoneaster", "Larix", "Betula"} <= set(r4.species_genera)


def test_heat_main_setback_depends_on_species():
    """2,5 m from a heat main: fine for mock orange (general 1,0 m), a
    violation for cotoneaster (4 m); lilac at 1,5 m breaks the 2 m rule."""
    reg = load_registry()
    idx = _heat_idx()
    plan = [
        Placement(id="cot", kind="SHRUB", speciesId="shrub_cotoneaster", x=62.5, y=20, crownRadiusM=0.8),
        Placement(id="mock", kind="SHRUB", speciesId="shrub_mockorange", x=62.5, y=40, crownRadiusM=1.2),
        Placement(id="lilac", kind="SHRUB", speciesId="shrub_lilac", x=61.5, y=60, crownRadiusM=1.5),
        Placement(id="larch", kind="TREE", speciesId="tree_larch", x=63.0, y=80, crownRadiusM=3.0),
    ]
    report = validate_plan(plan, idx, reg, SITE, SITE)
    assert _violations(report, "HEAT_MAIN_SENSITIVE_SPECIES_4M") == ["cot", "larch"]
    assert _violations(report, "HEAT_MAIN_SENSITIVE_SPECIES_2M") == ["lilac"]
    assert not _violations(report, "SHRUB_UTILITY_HEAT_MIN_DISTANCE")
    assert "mock" not in {v["plantingId"] for v in report["hardViolations"]}


def test_heat_main_species_check_in_explanation():
    """A large-crown linden stays UNVERIFIED under табл. 9.1 but gets a real
    PASS under 623-ПП п. 4.2.8, which has no crown limit."""
    reg = load_registry()
    idx = _heat_idx()
    linden = Placement(id="lin", kind="TREE", speciesId="tree_linden", x=63.0, y=50, crownRadiusM=3.5)
    e = build_explanation(linden, 0, reg, RuleDistanceOracle(idx), SITE)
    by_id = {c["ruleId"]: c for c in e["checks"]}
    assert by_id["HEAT_MAIN_SENSITIVE_SPECIES_2M"]["result"] == "PASS"
    assert by_id["TREE_UTILITY_HEAT_MIN_DISTANCE"]["result"] == "UNVERIFIED"
    assert "HEAT_MAIN_SENSITIVE_SPECIES_4M" not in by_id  # not a larch/birch/...

    mock = Placement(id="m", kind="SHRUB", speciesId="shrub_mockorange", x=62.5, y=50, crownRadiusM=1.2)
    e = build_explanation(mock, 0, reg, RuleDistanceOracle(idx), SITE)
    assert not any(c["ruleId"].startswith("HEAT_MAIN_") for c in e["checks"])


def test_species_rules_need_a_species():
    """Kind-level callers (exclusion zones) never see a genus rule; an
    unknown species is not exempt (safe direction)."""
    reg = load_registry()
    kind_level = {r.id for r in reg.rules_for("SHRUB", {"min_distance"})}
    assert not any(rid.startswith("HEAT_MAIN_") for rid in kind_level)
    unknown = {r.id for r in reg.rules_for("SHRUB", {"min_distance"}, species_id="not_in_catalog")}
    assert {"HEAT_MAIN_SENSITIVE_SPECIES_2M", "HEAT_MAIN_SENSITIVE_SPECIES_4M"} <= unknown


def test_hard_filter_applies_species_rules():
    reg = load_registry()
    oracle = RuleDistanceOracle(_heat_idx())
    cands = [Candidate(id=0, x=62.5, y=20), Candidate(id=1, x=65.0, y=20)]
    hard_filter_candidates(cands, "SHRUB", reg, oracle, species_id="shrub_cotoneaster")
    assert [c.valid for c in cands] == [False, True]
    assert cands[0].rejected_by == "HEAT_MAIN_SENSITIVE_SPECIES_4M"

    cands = [Candidate(id=0, x=62.5, y=20)]
    hard_filter_candidates(cands, "SHRUB", reg, oracle, species_id="shrub_mockorange")
    assert cands[0].valid


# ---------------------------------------------------------------------------
# full-registry behaviour on the standard synthetic fixture
# ---------------------------------------------------------------------------

def test_synthetic_fixture_regulatory_needs_review():
    """The demo registry has only ASSUMED distance values → a geometrically
    valid plan is NEEDS_REVIEW (never COMPLIANT), with the unverified-rules
    blocker and no site-boundary blocker (the fixture boundary is detected)."""
    from .test_integration import _run_pipeline
    _, _, _, cons, placements, validation = _run_pipeline()
    assert validation["valid"] is True
    r = validation["regulatory"]
    assert r["status"] == "NEEDS_REVIEW"
    assert len(r["blockers"]) == 1
    assert r["blockers"][0].startswith("UNVERIFIED_HARD_RULES:")
    assert r["checkCounts"]["fail"] == 0
    assert r["checkCounts"]["unverified"] > 0
    assert r["checkCounts"]["pass"] >= len(placements)  # inside-site checks
    assert 0.0 < r["coverage"]["regulatoryCoverage"] < 1.0
    parser = r["coverage"]["parser"]
    assert parser["siteBoundaryDetected"] is True
    assert parser["utilitiesTypedShare"] == 1.0
    assert parser["existingVegetationDetected"] is True
