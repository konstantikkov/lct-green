"""Rejected candidates carry their reasons and the norm behind each
(ТЗ п. 8: "если посадка … отклонена … фиксируется причина со ссылкой на норму").

Synthetic site (fixtures/synthetic_site.dxf, 200 × 140 m): building 30..90 ×
80..110, 0.4 kV cable along x = 150, water main (10,30)-(60,60)-(110,70).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import classification  # noqa: E402
from app.services.constraints import build_constraints  # noqa: E402
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.geometry import build_geometry_index  # noqa: E402
from app.services.rejections import Rejected, cite, explain_rejections  # noqa: E402
from app.services.rules import load_registry  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_fixture import build as build_fixture  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic_site.dxf"


@pytest.fixture(scope="module")
def site():
    if not FIXTURE.exists():
        build_fixture(FIXTURE)
    doc = parse_dxf(FIXTURE, "rej", "synthetic_site.dxf")
    classification.classify_document(doc)
    idx = build_geometry_index(doc)
    reg = load_registry()
    return build_constraints(doc, idx, reg), reg, idx


def test_every_rejected_point_gets_reasons_with_norms(site):
    cons, reg, idx = site
    pts = [
        Rejected(250, 50, "tree_rowan", "TREE", "OUTSIDE_ALLOWED_AREA"),          # outside the site
        Rejected(50, 95, "tree_rowan", "TREE", "OUTSIDE_ALLOWED_AREA"),           # inside the building
        Rejected(150.5, 70, "tree_rowan", "TREE", "OUTSIDE_ALLOWED_AREA"),        # 0.5 m from the cable
        Rejected(170, 60, "shrub_cotoneaster", "SHRUB", "HEAT_MAIN_SENSITIVE_SPECIES_4M"),
    ]
    out = explain_rejections(pts, cons, reg, idx)
    assert out["total"] == 4
    reasons = [s["reasons"] for s in out["samples"]]
    assert reasons[0] == ["OUTSIDE_SITE"]
    assert reasons[1][0] == "ON_OBJECT:BUILDING" and "TREE_BUILDING_MIN_DISTANCE" in reasons[1]
    assert reasons[2][0] == "TREE_UTILITY_ELECTRICITY_MIN_DISTANCE"
    cable = out["samples"][2]
    assert cable["requiredM"] == 2.0 and abs(cable["actualM"] - 0.5) < 0.05
    assert "HEAT_MAIN_SENSITIVE_SPECIES_4M" in reasons[3]
    counts = {r["key"]: r["count"] for r in out["byReason"]}
    assert counts["OUTSIDE_SITE"] == 1 and counts["ON_OBJECT:BUILDING"] == 1


def test_rule_reasons_cite_the_norm(site):
    _, reg, _ = site
    c = cite(reg, "TREE_UTILITY_ELECTRICITY_MIN_DISTANCE")
    assert c["document"] == "СП 42.13330.2016" and "9.1" in c["clause"]
    assert any("743-ПП" in a["document"] for a in c["alsoCited"])
    assert cite(reg, "NARROW_STRIP") is None  # geometric reasons have no norm


def test_shrub_is_not_blamed_on_a_tree_rule(site):
    """Zones of tree-only rules (e.g. 5 m from a building) must not be listed
    as the reason for rejecting a shrub."""
    cons, reg, idx = site
    # 3 m from the building wall: inside the tree 5 m zone, outside the shrub 1.5 m one
    out = explain_rejections([Rejected(60, 77, "shrub_mockorange", "SHRUB", "OUTSIDE_ALLOWED_AREA")],
                             cons, reg, idx)
    assert "TREE_BUILDING_MIN_DISTANCE" not in out["samples"][0]["reasons"]
