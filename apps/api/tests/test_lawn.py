"""Lawn recommendation (ТЗ §2 п. 3 «травянистые покрытия»).

Synthetic site (fixtures/synthetic_site.dxf): building 30..90 × 80..110,
0.4 kV cable along x = 150.
"""
from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import classification  # noqa: E402
from app.services.constraints import build_constraints  # noqa: E402
from app.services.dxf_export import export_greening_dxf  # noqa: E402
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.geometry import build_geometry_index  # noqa: E402
from app.services.lawn import lawn_geometry, recommend_lawn  # noqa: E402
from app.services.rules import load_registry  # noqa: E402
from app.services.validator import Placement  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_fixture import build as build_fixture  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic_site.dxf"


@pytest.fixture(scope="module")
def site():
    if not FIXTURE.exists():
        build_fixture(FIXTURE)
    doc = parse_dxf(FIXTURE, "lawn", "synthetic_site.dxf")
    classification.classify_document(doc)
    idx = build_geometry_index(doc)
    reg = load_registry()
    return build_constraints(doc, idx, reg), idx, reg


def test_lawn_covers_unpaved_ground_but_not_buildings(site):
    cons, idx, _ = site
    lawn, summary = recommend_lawn(cons, idx, [])
    assert summary["areaM2"] > 1000 and summary["polygons"]
    building = idx.by_entity[idx.by_category["BUILDING"][0]]
    assert lawn.intersection(building).area < 1.0


def test_shrub_bed_is_cut_out_but_a_tree_stands_in_the_lawn(site):
    cons, idx, _ = site
    bare, _ = recommend_lawn(cons, idx, [])
    spot = bare.representative_point()
    shrub = Placement(id="s", kind="SHRUB", speciesId="shrub_lilac", x=spot.x, y=spot.y, crownRadiusM=1.5)
    tree = Placement(id="t", kind="TREE", speciesId="tree_rowan", x=spot.x, y=spot.y, crownRadiusM=2.5)
    with_shrub, _ = recommend_lawn(cons, idx, [shrub])
    with_tree, _ = recommend_lawn(cons, idx, [tree])
    assert not with_shrub.contains(Point(spot.x, spot.y))
    assert with_tree.contains(Point(spot.x, spot.y))


def test_protection_zones_do_not_limit_a_lawn(site):
    """СП 42 табл. 9.1 and ПП РФ № 160 п. 11 «з» concern trees and shrubs:
    the lawn runs over the cable zone that the plantings must avoid."""
    cons, idx, _ = site
    lawn, _ = recommend_lawn(cons, idx, [])
    cable = idx.by_entity[idx.by_category["UTILITY_ELECTRICITY"][0]]
    near = cable.buffer(0.7)
    assert cons.allowed_area.intersection(near).area < 1.0
    assert lawn.intersection(near).area > 10.0


def test_lawn_layer_in_the_export_and_cited(site, tmp_path):
    cons, idx, reg = site
    _, summary = recommend_lawn(cons, idx, [])
    out = export_greening_dxf(FIXTURE, tmp_path / "out.dxf", [], None, [], 1.0, 0, "t",
                              lawn=lawn_geometry(summary))
    layers = {e.dxf.layer for e in ezdxf.readfile(out).modelspace()}
    assert "AI_GREENING_LAWN" in layers
    rule = reg.get("LAWN_RECOMMENDATION")
    assert rule.effective_source == "verified" and "3.8.1" in rule.source.clause
    assert "743-ПП" in summary["explanation"] and "не ограничивают" in summary["explanation"]


def test_closed_curb_ring_is_a_line_not_an_area(tmp_path):
    """Олимпийская деревня: curbs drawn as closed rings around lawns. The ring
    is the curb line — the lawn inside must stay plantable (it was taken as a
    filled curb area and cut out of the planting domain)."""
    d = ezdxf.new("R2018")
    d.header["$INSUNITS"] = 6
    msp = d.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True,
                       dxfattribs={"layer": "ГРАНИЦА_УЧАСТКА"})
    msp.add_lwpolyline([(40, 20), (60, 20), (60, 40), (40, 40)], close=True,
                       dxfattribs={"layer": "Бортовой камень"})
    path = tmp_path / "ring.dxf"
    d.saveas(path)
    doc = parse_dxf(path, "ring", "ring.dxf")
    classification.classify_document(doc)
    idx = build_geometry_index(doc)
    curb = idx.by_entity[idx.by_category["CURB"][0]]
    assert curb.geom_type in ("LineString", "LinearRing", "MultiLineString")
    assert abs(curb.distance(Point(50, 30)) - 10.0) < 1e-6
    cons = build_constraints(doc, idx, load_registry())
    assert cons.allowed_area.contains(Point(50, 30))     # the lawn inside the ring
    assert not cons.allowed_area.contains(Point(40.5, 30))  # but not at the curb


def test_hatch_island_is_a_hole(tmp_path):
    """Олимпийская деревня: a cover-plan asphalt hatch with lawn islands and
    tree pits inside. The islands are holes — a tree there is not on the road."""
    d = ezdxf.new("R2018")
    d.header["$INSUNITS"] = 6
    msp = d.modelspace()
    h = msp.add_hatch(dxfattribs={"layer": "_АБ ПЧ-АБ ПЧ"})
    h.paths.add_polyline_path([(0, 0), (50, 0), (50, 30), (0, 30)], is_closed=True)
    h.paths.add_polyline_path([(20, 10), (30, 10), (30, 20), (20, 20)], is_closed=True)
    path = tmp_path / "hatch.dxf"
    d.saveas(path)
    doc = parse_dxf(path, "hatch", "hatch.dxf")
    classification.classify_document(doc)
    idx = build_geometry_index(doc)
    road = idx.by_entity[idx.by_category["ROAD"][0]]
    assert not road.contains(Point(25, 15))        # the lawn island
    assert road.contains(Point(5, 5))              # the asphalt around it
    assert abs(road.area - (50 * 30 - 10 * 10)) < 1e-6


def test_stored_lawn_with_a_self_touching_ring_is_restored():
    """3-я Парковая: lawn rings rounded to a millimetre in the iteration record
    touched themselves; rebuilding them for the DXF export crashed GEOS."""
    bow_tie = {"shell": [0, 0, 10, 10, 10, 0, 0, 10, 0, 0], "holes": []}
    square = {"shell": [20, 0, 30, 0, 30, 10, 20, 10, 20, 0], "holes": []}
    lawn = lawn_geometry({"polygons": [bow_tie, square]})
    assert lawn is not None and lawn.is_valid and lawn.area > 100
