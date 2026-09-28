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


@pytest.mark.parametrize("layer,expected", [
    # Layer names taken verbatim from the Мосгеотрест ИРД exports of the
    # "Пилотный проект 20 улиц" dataset (files output[..]_3_ДЖКХ-*up/tp/pp).
    # Водосток = ливневая канализация (К2): по табл. 9.1 это «канализация»
    # (1,5 м), а не «дренаж» (2,0 м). Largest layer in the "up" file.
    ("Водосток", "UTILITY_SEWER"),
    ("Водосток проектный", "UTILITY_SEWER"),
    ("Кабель электрический", "UTILITY_ELECTRICITY"),
    ("Кабель связи", "UTILITY_COMMUNICATION"),
    ("Канализация самотёчная", "UTILITY_SEWER"),
    ("Канализация напорная", "UTILITY_SEWER"),
    ("Водопровод", "UTILITY_WATER"),
    ("Газопровод", "UTILITY_GAS"),
    ("Теплосеть", "UTILITY_HEAT"),
    ("Дренаж", "UTILITY_DRAINAGE"),
    ("Общий коллектор", "UTILITY"),
    ("Трубопроводы", "UTILITY"),
    ("Отдельно стоящее дерево", "EXISTING_TREE"),
    ("Полоса деревьев", "EXISTING_TREE"),
    ("Бортовой камень", "CURB"),
    ("Граница улицы", "ROAD"),
    ("Граница растительности и грунта", "GREEN_AREA"),
    ("Красные линии", "SITE_BOUNDARY"),
    ("Граница заказа", "SITE_BOUNDARY"),
    # the rest of the topographic plan vocabulary (was 78 layers to review)
    ("Навесы", "BUILDING"),
    ("Павильоны", "BUILDING"),
    ("Фонтаны", "BUILDING"),
    ("Памятники", "BUILDING"),
    ("Опоры контакной сети", "LIGHTING"),
    ("Светофоры", "LIGHTING"),
    ("Колодцы", "UTILITY"),
    ("Люки", "UTILITY"),
    ("Выгребные ямы", "UTILITY_SEWER"),
    ("Указатель подз коммуникаций", "ANNOTATION"),
    ("Откосы", "ANNOTATION"),
    ("Геодезические пункты", "ANNOTATION"),
    ("ДВ_ГП_П_МАФ", "ANNOTATION"),
    ("_МАФ_вент. кожух", "ANNOTATION"),
    # dendro plans and lighting / demolition layers of the pilot packages
    ("!!!_1. Дендра_сохранить", "EXISTING_TREE"),
    ("!!!_1. Дендра_номера_сохранить", "ANNOTATION"),
    ("!!!_1. Дендра_выноски_вырубка", "ANNOTATION"),
    ("_ЭН_опора НО", "LIGHTING"),
    ("ДВ_ГП_Демонтаж", "ANNOTATION"),
])
def test_real_geotrest_layer_names(layer, expected):
    assert classification.classify_layer_name(layer).category == expected


def test_vodootvod_is_a_drain_not_a_boundary():
    """Regression: bare 'отвод' in the SITE_BOUNDARY pattern matched
    "Водоотводные лотки" mid-word and filed a drain as a site boundary."""
    assert classification.classify_layer_name(
        "ДВ_ГП_П_Водоотводные_лотки").category == "UTILITY_DRAINAGE"
    # the land-allocation sense must still resolve to a boundary
    assert classification.classify_layer_name("Отвод земель").category == "SITE_BOUNDARY"
    assert classification.classify_layer_name("Граница отвода").category == "SITE_BOUNDARY"


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
    """A rule whose clause was never located must never report as verified.

    Checked against every unverified rule in the registry rather than one
    hard-coded id, so verifying more clauses cannot quietly void this test.
    """
    reg = load_registry()
    unverified = [r for r in reg.rules if r.status == "missing_source_data"]
    assert unverified, "registry must keep declaring what is not yet sourced"
    for r in unverified:
        assert r.constraint.get("distance_m") is None
        assert r.effective_source in ("assumption", "inactive")
        assert r.source.clause is None


def test_verified_rules_carry_full_provenance():
    """The flip side: a verified distance is worthless without a citation."""
    reg = load_registry()
    verified = [r for r in reg.rules
                if r.status == "verified" and r.constraint.get("distance_m") is not None
                and r.source.document != "project_geometry"]
    assert len(verified) >= 17
    for r in verified:
        assert r.source.clause, f"{r.id}: verified without a clause"
        assert r.source.excerpt, f"{r.id}: verified without a source excerpt"
        assert r.source.url, f"{r.id}: verified without a document reference"


def test_table_9_1_values():
    """Spot-check transcription of СП 42.13330.2016 table 9.1 (п. 9.6)."""
    reg = load_registry()
    expected = {
        "TREE_BUILDING_MIN_DISTANCE": 5.0,
        "SHRUB_BUILDING_MIN_DISTANCE": 1.5,
        "TREE_ROAD_MIN_DISTANCE": 2.0,
        "TREE_SIDEWALK_MIN_DISTANCE": 0.7,
        "TREE_UTILITY_WATER_MIN_DISTANCE": 2.0,
        # дренаж shares the водопровод row at 2,0 m — regression guard for the
        # 1,5 m value this rule was assumed to have before verification
        "TREE_UTILITY_DRAINAGE_MIN_DISTANCE": 2.0,
        "TREE_UTILITY_GAS_MIN_DISTANCE": 1.5,
        "TREE_UTILITY_SEWER_MIN_DISTANCE": 1.5,
        "TREE_UTILITY_HEAT_MIN_DISTANCE": 2.0,
        "SHRUB_UTILITY_HEAT_MIN_DISTANCE": 1.0,
        "TREE_UTILITY_ELECTRICITY_MIN_DISTANCE": 2.0,
        "SHRUB_UTILITY_ELECTRICITY_MIN_DISTANCE": 0.7,
        "TREE_LIGHTING_MIN_DISTANCE": 4.0,
    }
    for rule_id, distance in expected.items():
        r = reg.get(rule_id)
        assert r is not None, rule_id
        assert r.effective_distance_m == distance, rule_id
        assert r.effective_source == "verified", rule_id
        assert "9.1" in (r.source.clause or ""), rule_id


def test_743_pp_table_3_6_1_repeats_table_9_1():
    """743-ПП п. 3.6.3 табл. 3.6.1 is cited next to СП 42 табл. 9.1 only where
    the values agree — the cited row carries this rule's own number."""
    import re
    reg = load_registry()
    cited = [r for r in reg.rules
             if any("743-ПП" in c.document for c in r.alsoCited)]
    assert len(cited) >= 21
    for r in cited:
        c743 = next(c for c in r.alsoCited if "743-ПП" in c.document)
        assert c743.clause == "п. 3.6.3, таблица 3.6.1" and c743.relation == "same_value"
        assert c743.url and c743.url.endswith("#page=28")
        m = re.search(r"— ([\d,]+|-) \(дерево\) / ([\d,]+|-) \(кустарник\)", c743.excerpt)
        assert m, r.id
        kind = r.appliesTo["planting_type"][0]
        value = m.group(1) if kind == "TREE" else m.group(2)
        assert value.replace(",", ".") == f"{r.constraint['distance_m']:.1f}", r.id
        # 623-ПП requires the same table by reference (табл. 7.6 МГСН 1.01-99)
        c623 = next(c for c in r.alsoCited if "623-ПП" in c.document)
        assert c623.relation == "reference" and "МГСН 1.01-99" in c623.excerpt
    # ЛКС ТМК is a СП 42 row only
    assert not reg.get("TREE_LKS_TMK_MIN_DISTANCE").alsoCited


def test_all_three_tz_acts_are_cited():
    """The ТЗ names СП 42.13330.2016, 743-ПП and 623-ПП/МГСН: each must back at
    least one enforced, verified rule with a concrete clause."""
    reg = load_registry()
    cited: set[str] = set()
    for r in reg.rules:
        if not r.enabled or r.effective_source != "verified":
            continue
        for s in [r.source, *r.alsoCited]:
            if s.clause:
                cited.add(s.document)
    for act in ("СП 42.13330.2016", "ППМ № 743-ПП", "ППМ № 623-ПП"):
        assert act in cited, act


def test_spacing_rules_sourced_from_743_table_3_6_2():
    reg = load_registry()
    for rid, d in (("TREE_TREE_MIN_SPACING", 5.0), ("SHRUB_SHRUB_MIN_SPACING", 1.0)):
        r = reg.get(rid)
        assert r.effective_source == "verified" and r.effective_distance_m == d, rid
        assert r.source.clause == "п. 3.6.4, таблица 3.6.2", rid
        # "ориентировочные … определяются проектом" — still a heuristic
        assert r.role == "design_heuristic" and r.severity == "recommended", rid


def test_existing_tree_buffer_is_not_a_citation():
    """743-ПП has no new-planting-to-existing-tree distance: the buffer must
    read as a technological safe buffer, never as a norm."""
    reg = load_registry()
    r = reg.get("TREE_EXISTING_TREE_MIN_DISTANCE")
    assert r.role == "safe_buffer"
    assert r.status == "missing_source_data" and r.source.clause is None
    assert r.effective_source == "assumption"


def test_unmodelled_categories_are_documented_but_disabled():
    """Table rows we cannot classify yet are cited, disabled, and never silent."""
    reg = load_registry()
    for rule_id in ("TREE_TRAM_TRACK_MIN_DISTANCE", "TREE_SLOPE_TOE_MIN_DISTANCE",
                    "TREE_RETAINING_WALL_MIN_DISTANCE", "TREE_LKS_TMK_MIN_DISTANCE",
                    "TREE_SCHOOL_BUILDING_MIN_DISTANCE"):
        r = reg.get(rule_id)
        assert r is not None, rule_id
        assert r.enabled is False, rule_id
        assert r.effective_distance_m is None, rule_id
        assert r.source.clause and r.source.excerpt, rule_id


def test_disabled_assumption_makes_rule_inactive():
    reg = load_registry()
    r = reg.get("TREE_FENCE_MIN_DISTANCE")
    assert r.effective_distance_m is None
    assert r.effective_source == "inactive"


def test_invasive_blacklist_is_sourced_not_invented():
    """The list is now transcribed from ППМ № 369-ПП приложение 1.

    Whatever its contents, it may only be present together with a citation —
    a populated blacklist without provenance is the failure mode to catch.
    """
    reg = load_registry()
    r = reg.get("INVASIVE_SPECIES_BLACKLIST")
    species = r.constraint.get("species")
    if species:
        assert r.status == "verified"
        assert r.source.clause and r.source.excerpt and r.source.url
        assert len(species) >= 30
    else:
        assert r.status == "missing_source_data"


def test_blacklist_rejects_invasive_and_passes_natives():
    reg = load_registry()
    for latin in ("Acer negundo", "Quercus rubra", "Cornus albus",
                  "Heracleum sosnowskyi", "Physocarpus opulifolius"):
        ok, rule_id = blacklist_check(reg, "x", latin)
        assert not ok and rule_id == "INVASIVE_SPECIES_BLACKLIST", latin
    # Acer platanoides is native; only Acer negundo is listed — no genus-wide ban
    for latin in ("Tilia cordata", "Acer platanoides", "Sorbus aucuparia"):
        ok, _ = blacklist_check(reg, "x", latin)
        assert ok, latin


def test_no_project_species_is_invasive():
    """Guards the catalog itself: shipping an invasive default would be a defect."""
    reg = load_registry()
    for s in load_species():
        ok, rule_id = blacklist_check(reg, s["id"], s["latin"])
        assert ok, f"{s['id']} ({s['latin']}) is on the ППМ 369-ПП list"


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
