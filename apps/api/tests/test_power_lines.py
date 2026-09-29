"""Protection zones of power lines, ПП РФ № 160 (ТЗ §2: «охранные зоны ЛЭП»).

Site 100 × 60 m: overhead line ЛЭП along y = 30, 0.4 kV cable along y = 5.
"""
from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import classification  # noqa: E402
from app.services.constraints import build_constraints  # noqa: E402
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.explain import _check_status_and_reason  # noqa: E402
from app.services.geometry import build_geometry_index  # noqa: E402
from app.services.rejections import Rejected, cite, explain_rejections  # noqa: E402
from app.services.rules import load_registry  # noqa: E402
from app.services.validator import Placement  # noqa: E402


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    d = ezdxf.new("R2018")
    d.header["$INSUNITS"] = 6
    msp = d.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True,
                       dxfattribs={"layer": "ГРАНИЦА_УЧАСТКА"})
    msp.add_lwpolyline([(0, 30), (100, 30)], dxfattribs={"layer": "ЛЭП"})
    msp.add_lwpolyline([(0, 5), (100, 5)], dxfattribs={"layer": "КЛ-0.4_КАБЕЛЬ"})
    path = tmp_path_factory.mktemp("lep") / "lep.dxf"
    d.saveas(path)
    doc = parse_dxf(path, "lep", "lep.dxf")
    classification.classify_document(doc)
    idx = build_geometry_index(doc)
    reg = load_registry()
    return doc, build_constraints(doc, idx, reg), reg, idx


def test_overhead_line_is_not_a_cable(site):
    doc = site[0]
    cats = {li.name: li.classification.category for li in doc.layers}
    assert cats["ЛЭП"] == "POWER_LINE_OVERHEAD"
    assert cats["КЛ-0.4_КАБЕЛЬ"] == "UTILITY_ELECTRICITY"


def test_planting_in_the_protection_zone_is_rejected_with_the_norm(site):
    _, cons, reg, idx = site
    out = explain_rejections([
        Rejected(50, 31.5, "tree_rowan", "TREE", "OUTSIDE_ALLOWED_AREA"),        # 1.5 m from the line
        Rejected(50, 5.8, "shrub_mockorange", "SHRUB", "OUTSIDE_ALLOWED_AREA"),  # 0.8 m from the cable
    ], cons, reg, idx)
    tree, shrub = out["samples"]
    assert tree["primary"] == "TREE_POWER_LINE_OVERHEAD_PROTECTION_ZONE"
    assert tree["requiredM"] == 2.0 and abs(tree["actualM"] - 1.5) < 0.05
    # 0.8 m: outside СП 42's 0.7 m, inside ПП 160's 1 m cable zone
    assert "SHRUB_UTILITY_ELECTRICITY_PROTECTION_ZONE" in shrub["reasons"]
    assert "SHRUB_UTILITY_ELECTRICITY_MIN_DISTANCE" not in shrub["reasons"]


def test_protection_zone_cites_pp_160(site):
    reg = site[2]
    c = cite(reg, "TREE_POWER_LINE_OVERHEAD_PROTECTION_ZONE")
    assert c["document"] == "ПП РФ № 160" and "«а»" in c["clause"] and c["requiredM"] == 2.0
    assert any("11 «з»" in a["clause"] for a in c["alsoCited"])


def test_unknown_voltage_keeps_the_check_under_review(site):
    """The drawing does not say the line voltage: 2 m is the zone of a line up
    to 1 kV, inferred from the symbols at lighting supports — so even a
    planting outside it is not a confirmed PASS."""
    reg = site[2]
    rule = next(r for r in reg.rules if r.id == "TREE_POWER_LINE_OVERHEAD_PROTECTION_ZONE")
    p = Placement(id="t1", kind="TREE", speciesId="tree_rowan", x=50, y=45, crownRadiusM=2.0)
    status, reason = _check_status_and_reason(rule, p, True)
    assert status == "UNVERIFIED" and "напряжения" in reason


def _support_symbol(x: float, y: float) -> list:
    """A Mosgeotrest «ЛЭП» support symbol: a 2.5 m stroke and a 1 m arrow along
    the line (here along x)."""
    from shapely.geometry import LineString
    return [LineString([(x - 1.25, y), (x + 1.25, y)]),
            LineString([(x + 0.5, y + 0.5), (x + 1.0, y), (x + 0.5, y - 0.5)])]


def test_support_symbols_are_joined_into_the_wire():
    """Only the symbols at the supports are drawn; the zone must also cover
    the span between them. A symbol across the street is not joined."""
    from shapely.geometry import Point
    from app.models.ir import IREntity
    from app.services.geometry import GeometryIndex, link_power_line_symbols
    idx = GeometryIndex()
    parts = [g for x in (0, 35, 70) for g in _support_symbol(x, 30)] + _support_symbol(35, 10)
    for n, g in enumerate(parts):
        eid = f"e{n}"
        idx.by_entity[eid] = g
        idx.entity_meta[eid] = IREntity(id=eid, type="LINE", layer="ЛЭП", kind="polyline",
                                        geometry={"points": [list(c) for c in g.coords]})
        idx.by_category.setdefault("POWER_LINE_OVERHEAD", []).append(eid)
    assert link_power_line_symbols(idx) == 2
    wire = [idx.by_entity[e] for e in idx.by_category["POWER_LINE_OVERHEAD"] if "~span" in e]
    mid_span = Point(17.5, 31.0)
    assert min(w.distance(mid_span) for w in wire) < 1.5          # under the wire, inside the 2 m zone
    assert min(w.distance(Point(35, 20)) for w in wire) > 9       # no span across the street
