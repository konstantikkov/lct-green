"""Unit tests: parsing, units, classification, rules, geometry, candidates."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app.models.ir import CadDocument  # noqa: E402
from app.services import classification  # noqa: E402
from app.services.candidates import (DistanceOracle, generate_candidates,  # noqa: E402
                                     poisson_disc_candidates)
from app.services.constraints import build_constraints  # noqa: E402
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.geometry import (build_geometry_index, detect_site_boundary,  # noqa: E402
                                   entity_to_geometry, polygon_to_rings)
from app.services.rules import load_registry, load_species, blacklist_check  # noqa: E402
from app.services.validator import Placement, validate_plan  # noqa: E402
from make_fixture import build as build_fixture  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic_site.dxf"


@pytest.fixture(scope="session")
def doc() -> CadDocument:
    if not FIXTURE.exists():
        build_fixture(FIXTURE)
    d = parse_dxf(FIXTURE, "test-doc", "synthetic_site.dxf")
    classification.classify_document(d)
    return d


# ---- DXF parsing / units ---------------------------------------------------

def test_parse_units_meters(doc):
    assert doc.units == "m"
    assert doc.unitsScaleToMeters == 1.0
    assert doc.cadVersion == "AC1032"


def test_parse_extents(doc):
    (minx, miny), (maxx, maxy) = doc.extents.min, doc.extents.max
    assert (round(minx), round(miny), round(maxx), round(maxy)) == (0, 0, 200, 140)


def test_parse_entities_present(doc):
    types = {e.type for e in doc.entities}
    assert "LWPOLYLINE" in types
    assert "CIRCLE" in types
    assert "TEXT" in types
    assert doc.entityCount >= 12


def test_unit_conversion_mm():
    """A unitless drawing with a big span is treated as millimeters."""
    from app.services.dxf_parser import _guess_units
    units, scale, note = _guess_units(0, 150000)
    assert scale == 0.001 and "mm" in units
    units, scale, _ = _guess_units(4, 100)
    assert scale == 0.001
    units, scale, _ = _guess_units(6, 100)
    assert scale == 1.0


# ---- classification ---------------------------------------------------------

@pytest.mark.parametrize("layer,expected", [
    ("ГРАНИЦА_УЧАСТКА", "SITE_BOUNDARY"),
    ("ЗДАНИЯ", "BUILDING"),
    ("ДОРОГИ_ПРОЕЗДЫ", "ROAD"),
    ("ТРОТУАРЫ", "SIDEWALK"),
    ("В1_ВОДОПРОВОД", "UTILITY_WATER"),
    ("КЛ-0.4_КАБЕЛЬ", "UTILITY_ELECTRICITY"),
    ("СУЩ_ДЕРЕВЬЯ", "EXISTING_TREE"),
    ("Газоны и озеленение", "GREEN_AREA"),
    ("randomlayer123", "UNKNOWN"),
])
def test_layer_classification(layer, expected):
    assert classification.classify_layer_name(layer).category == expected


def test_uncertainty_not_hidden():
    cls = classification.classify_layer_name("СЕТИ_1")
    assert cls.category in ("UTILITY", "UNKNOWN")
    assert cls.requiresReview is True
    assert cls.confidence < 0.65


# ---- rules ------------------------------------------------------------------

def test_rule_registry_loads():
    reg = load_registry()
    assert len(reg.documents) == 10
    assert len(reg.rules) >= 15


def test_missing_source_data_not_silently_verified():
    reg = load_registry()
    r = reg.get("TREE_BUILDING_MIN_DISTANCE")
    assert r.status == "missing_source_data"
    assert r.constraint.get("distance_m") is None
    # enforced value comes from an explicitly flagged assumption, never "verified"
    assert r.effective_source in ("assumption", "inactive")


def test_disabled_assumption_makes_rule_inactive():
    reg = load_registry()
    r = reg.get("TREE_FENCE_MIN_DISTANCE")
    assert r.effective_distance_m is None
    assert r.effective_source == "inactive"


def test_invasive_blacklist_not_invented():
    reg = load_registry()
    r = reg.get("INVASIVE_SPECIES_BLACKLIST")
    assert r.constraint.get("species") is None  # list absent in source data
    ok, rule = blacklist_check(reg, "tree_linden", "Tilia cordata")
    assert ok  # nothing can be rejected without the verified list


def test_blacklist_enforced_once_list_provided():
    reg = load_registry()
    r = reg.get("INVASIVE_SPECIES_BLACKLIST")
    r.constraint["species"] = ["Acer negundo"]
    ok, rule_id = blacklist_check(reg, "tree_boxelder", "Acer negundo")
    assert not ok and rule_id == "INVASIVE_SPECIES_BLACKLIST"


# ---- geometry ---------------------------------------------------------------

def test_geometry_index_and_site(doc):
    idx = build_geometry_index(doc)
    site, src = detect_site_boundary(doc, idx)
    assert src == "detected"
    assert abs(site.area - 200 * 140) < 1.0


def test_polygon_rings_roundtrip():
    p = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)],
                holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]])
    rings = polygon_to_rings(p)
    assert len(rings) == 1
    assert len(rings[0]["holes"]) == 1


def test_buffer_and_difference(doc):
    idx = build_geometry_index(doc)
    reg = load_registry()
    res = build_constraints(doc, idx, reg)
    allowed_area = sum(pl.area for pl in
                       __import__("app.services.geometry", fromlist=["as_polygon_list"])
                       .as_polygon_list(res.allowed_area))
    site_area = res.site_boundary.area
    assert 0 < allowed_area < site_area
    assert res.exclusion_zones, "expected exclusion zones from utilities/building"
    z = res.exclusion_zones[0]
    assert z.ruleId and z.source["document"]


# ---- candidates -------------------------------------------------------------

def test_poisson_deterministic():
    square = Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])
    a = poisson_disc_candidates(square, 5.0, seed=42)
    b = poisson_disc_candidates(square, 5.0, seed=42)
    c = poisson_disc_candidates(square, 5.0, seed=7)
    assert a == b
    assert a != c
    assert len(a) > 20
    # spacing respected
    for i in range(min(len(a), 60)):
        for j in range(i + 1, min(len(a), 60)):
            dx = a[i][0] - a[j][0]; dy = a[i][1] - a[j][1]
            assert dx * dx + dy * dy >= 25.0 - 1e-6


def test_candidate_features(doc):
    idx = build_geometry_index(doc)
    site, _ = detect_site_boundary(doc, idx)
    oracle = DistanceOracle(idx, site)
    cands = generate_candidates(site, oracle, 5.0, seed=42)
    assert cands
    f = cands[0].features
    assert f["building"] is not None and f["building"] >= 0
    assert f["boundary"] is not None


# ---- validator --------------------------------------------------------------

def test_validator_flags_violation(doc):
    idx = build_geometry_index(doc)
    reg = load_registry()
    res = build_constraints(doc, idx, reg)
    # deliberately place a tree 1 m from the building wall (assumed min is 5 m)
    bad = Placement(id="bad1", kind="TREE", speciesId="tree_linden",
                    x=29.0, y=95.0, crownRadiusM=3.5)
    report = validate_plan([bad], idx, reg, res.site_boundary, res.allowed_area)
    assert report["valid"] is False
    assert any(v["ruleId"] == "TREE_BUILDING_MIN_DISTANCE" for v in report["hardViolations"])


def test_validator_spacing_is_design_heuristic_warning(doc):
    """Tree-tree spacing is a design heuristic (no legal source established):
    violations are structured warnings, never hardViolations."""
    idx = build_geometry_index(doc)
    reg = load_registry()
    res = build_constraints(doc, idx, reg)
    a = Placement(id="a", kind="TREE", speciesId="tree_linden", x=170, y=90, crownRadiusM=3.5)
    b = Placement(id="b", kind="TREE", speciesId="tree_linden", x=172, y=90, crownRadiusM=3.5)
    report = validate_plan([a, b], idx, reg, res.site_boundary, res.allowed_area)
    assert not any(v["ruleId"] == "TREE_TREE_MIN_SPACING" for v in report["hardViolations"])
    w = [w for w in report["warnings"] if w["ruleId"] == "TREE_TREE_MIN_SPACING"]
    assert w and w[0]["kind"] == "design_heuristic"
    assert w[0]["actual"] == pytest.approx(2.0)
    assert w[0]["requiredM"] == pytest.approx(5.0)
    assert report["valid"] is True  # warnings never invalidate the plan


def test_validator_outside_site(doc):
    idx = build_geometry_index(doc)
    reg = load_registry()
    res = build_constraints(doc, idx, reg)
    out = Placement(id="out", kind="TREE", speciesId="tree_linden",
                    x=-50, y=-50, crownRadiusM=3.5)
    report = validate_plan([out], idx, reg, res.site_boundary, res.allowed_area)
    assert any(v["ruleId"] == "PLANTING_INSIDE_SITE_BOUNDARY" for v in report["hardViolations"])


def test_species_catalog():
    species = load_species()
    kinds = {s["kind"] for s in species}
    assert kinds == {"TREE", "SHRUB"}
    assert all(s["parameters"]["crownRadiusM"] > 0 for s in species)


# ---- malformed DXF repair -----------------------------------------------------

def test_dxf_sanitizer_joins_stray_lines(tmp_path):
    """LibreDWG sometimes emits string values containing raw newlines; the
    sanitizer must re-join them so the code/value alternation is restored."""
    from app.services.dxf_parser import _sanitize_dxf
    bad = tmp_path / "bad.dxf"
    bad.write_bytes(b"0\nSECTION\n2\nHEADER\n9\n$STYLE\n3\nPDF\nISOCPEUR\n0\nENDSEC\n0\nEOF\n")
    fixed = tmp_path / "fixed.dxf"
    joined = _sanitize_dxf(bad, fixed)
    assert joined == 1
    lines = fixed.read_bytes().split(b"\n")
    codes = lines[0::2]
    assert all(c.strip().lstrip(b"-").isdigit() for c in codes if c.strip())
    assert b"PDF ISOCPEUR" in fixed.read_bytes()
