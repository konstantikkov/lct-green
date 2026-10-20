"""Trees not over an underground car park (project decision; shrubs stay)
and the СП 42 табл. 9.1 retaining-wall setbacks (3,0 tree / 1,0 shrub).

Site 100 × 60 m: car-park slab x 10…40, y 10…40 ("ГП паркинг (покрытия)");
retaining wall drawn as a closed outline x 60…80, y 20…22.
"""
from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import classification  # noqa: E402
from app.services.candidates import Candidate  # noqa: E402
from app.services.constraints import build_constraints  # noqa: E402
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.explain import build_explanation  # noqa: E402
from app.services.geometry import build_geometry_index  # noqa: E402
from app.services.optimizer import hard_filter_candidates  # noqa: E402
from app.services.rules import load_registry  # noqa: E402
from app.services.validator import Placement, RuleDistanceOracle, validate_plan  # noqa: E402

PARKING = "TREE_OVER_UNDERGROUND_PARKING"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    d = ezdxf.new("R2018")
    d.header["$INSUNITS"] = 6
    msp = d.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True,
                       dxfattribs={"layer": "ГРАНИЦА_УЧАСТКА"})
    slab = msp.add_hatch(dxfattribs={"layer": "ГП паркинг (покрытия)"})
    slab.paths.add_polyline_path([(10, 10), (40, 10), (40, 40), (10, 40)], is_closed=True)
    msp.add_lwpolyline([(60, 20), (80, 20), (80, 22), (60, 22)], close=True,
                       dxfattribs={"layer": "Подпорная стенка"})
    path = tmp_path_factory.mktemp("slab") / "slab.dxf"
    d.saveas(path)
    doc = parse_dxf(path, "slab", "slab.dxf")
    classification.classify_document(doc)
    idx = build_geometry_index(doc)
    reg = load_registry()
    return doc, idx, reg, build_constraints(doc, idx, reg)


def _plant(kind, x, y, pid="p"):
    sp = "tree_rowan" if kind == "TREE" else "shrub_mockorange"
    return Placement(id=pid, kind=kind, speciesId=sp, x=x, y=y, crownRadiusM=1.5)


def test_layers_are_classified(site):
    cats = {li.name: li.classification.category for li in site[0].layers}
    assert cats["ГП паркинг (покрытия)"] == "UNDERGROUND_PARKING"
    assert cats["Подпорная стенка"] == "RETAINING_WALL"


def test_slab_stays_in_the_allowed_area_but_is_a_tree_zone(site):
    _, _, _, cons = site
    assert cons.allowed_area.covers(Point(25, 25))  # shrubs may go there
    zones = [z for z in cons.exclusion_zones if z.ruleId == PARKING]
    assert len(zones) == 1 and zones[0].perKind and zones[0].to_api()["perKind"]


def test_candidates_over_the_slab_trees_rejected_shrubs_kept(site):
    _, idx, reg, _ = site
    oracle = RuleDistanceOracle(idx)
    tree = [Candidate(0, 25, 25), Candidate(1, 50, 50)]
    hard_filter_candidates(tree, "TREE", reg, oracle, species_id="tree_rowan")
    assert not tree[0].valid and tree[0].rejected_by == PARKING
    assert tree[1].valid
    shrub = [Candidate(0, 25, 25)]
    hard_filter_candidates(shrub, "SHRUB", reg, oracle, species_id="shrub_mockorange")
    assert shrub[0].valid


def test_validator_fails_a_tree_on_the_slab_only(site):
    _, idx, reg, cons = site
    res = validate_plan([_plant("TREE", 25, 25, "t"), _plant("SHRUB", 25, 26, "s"),
                         _plant("TREE", 50, 50, "ok")],
                        idx, reg, cons.site_boundary, cons.allowed_area)
    over = [v for v in res["hardViolations"] if v["ruleId"] == PARKING]
    assert [v["plantingId"] for v in over] == ["t"]


def test_inspector_explains_the_slab(site):
    _, idx, reg, cons = site
    oracle = RuleDistanceOracle(idx)
    exp = build_explanation(_plant("TREE", 25, 25), 0, reg, oracle, cons.site_boundary)
    row = next(c for c in exp["checks"] if c["ruleId"] == PARKING)
    assert row["result"] == "FAIL" and row["comparator"] == "outside"
    exp = build_explanation(_plant("SHRUB", 25, 25), 0, reg, oracle, cons.site_boundary)
    assert not any(c["ruleId"] == PARKING for c in exp["checks"])


def test_retaining_wall_setbacks(site):
    _, idx, reg, cons = site
    # closed outline measured as a wall line: 2 m above it is inside the
    # tree setback (3 m) but outside the shrub one (1 m)
    res = validate_plan([_plant("TREE", 70, 24, "t"), _plant("SHRUB", 70, 24, "s"),
                         _plant("TREE", 70, 26, "far")],
                        idx, reg, cons.site_boundary, cons.allowed_area)
    walls = {v["plantingId"] for v in res["hardViolations"]
             if v["ruleId"].endswith("RETAINING_WALL_MIN_DISTANCE")}
    assert walls == {"t"}
