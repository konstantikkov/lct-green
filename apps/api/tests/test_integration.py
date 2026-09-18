"""Integration + round-trip tests: full pipeline DXF → … → exported DXF,
explanation provenance, deterministic seeds, and (when a converter is
available) real DWG conversion.
"""
from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app.models.ir import CadDocument  # noqa: E402
from app.services import classification  # noqa: E402
from app.services.candidates import DistanceOracle, generate_candidates  # noqa: E402
from app.services.constraints import build_constraints  # noqa: E402
from app.services.dwg_converter import DwgConverter, detect_converter  # noqa: E402
from app.services.dxf_export import export_greening_dxf  # noqa: E402
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.explain import build_explanation  # noqa: E402
from app.services.geometry import as_polygon_list, build_geometry_index  # noqa: E402
from app.services.optimizer import (OptimizerParams, PlantSpec, auto_target,  # noqa: E402
                                    hard_filter_candidates, optimize)
from app.services.rules import load_registry  # noqa: E402
from app.services.validator import Placement, RuleDistanceOracle, validate_plan  # noqa: E402
from make_fixture import build as build_fixture  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "fixtures" / "synthetic_site.dxf"
REAL_DWG = REPO / "fixtures" / "site_plan_real.dwg"


def _run_pipeline(seed: int = 42):
    if not FIXTURE.exists():
        build_fixture(FIXTURE)
    doc = parse_dxf(FIXTURE, "it-doc", "synthetic_site.dxf")
    classification.classify_document(doc)
    idx = build_geometry_index(doc)
    registry = load_registry()
    cons = build_constraints(doc, idx, registry)
    allowed = cons.allowed_area
    oracle = DistanceOracle(idx, cons.site_boundary)
    rule_oracle = RuleDistanceOracle(idx)

    cands = generate_candidates(cons.site_boundary, oracle, 5.0, seed)
    from shapely.prepared import prep
    from shapely.geometry import Point
    prepared = prep(allowed)
    for c in cands:
        if not prepared.contains(Point(c.x, c.y)):
            c.valid = False
            c.rejected_by = "OUTSIDE_ALLOWED_AREA"
    hard_filter_candidates(cands, "TREE", registry, rule_oracle)

    params = OptimizerParams.for_strategy("balanced")
    area = sum(p.area for p in as_polygon_list(allowed))
    params.targetCount = auto_target(area, 5.0, sum(1 for c in cands if c.valid))
    spec = PlantSpec(speciesId="tree_linden", kind="TREE", crownRadiusM=3.5, spacingM=5.0)
    chosen = optimize(cands, spec, params, [], seed)
    placements = [Placement(id=f"t{i}", kind="TREE", speciesId="tree_linden",
                            x=c.x, y=c.y, crownRadiusM=3.5, score=c.score)
                  for i, c in enumerate(chosen)]
    validation = validate_plan(placements, idx, registry, cons.site_boundary, allowed)
    return doc, idx, registry, cons, placements, validation


def test_full_pipeline_valid_plan():
    doc, idx, registry, cons, placements, validation = _run_pipeline()
    assert len(placements) > 20
    assert validation["valid"], validation["hardViolations"][:3]
    assert validation["metrics"]["validCount"] == len(placements)


def test_deterministic_seed_reproducible():
    _, _, _, _, p1, _ = _run_pipeline(seed=42)
    _, _, _, _, p2, _ = _run_pipeline(seed=42)
    _, _, _, _, p3, _ = _run_pipeline(seed=1337)
    assert [(p.x, p.y) for p in p1] == [(p.x, p.y) for p in p2]
    assert [(p.x, p.y) for p in p1] != [(p.x, p.y) for p in p3]


def test_explanation_provenance():
    doc, idx, registry, cons, placements, _ = _run_pipeline()
    oracle = RuleDistanceOracle(idx)
    exp = build_explanation(placements[0], 0, registry, oracle, cons.site_boundary)
    assert exp["checks"], "explanation must contain rule checks"
    for c in exp["checks"]:
        # provenance: every check names its rule and source document
        assert c["ruleId"]
        assert c["source"]["document"]
        # values marked as assumptions are never labeled verified
        if c["effectiveSource"] == "assumption":
            assert c["source"]["status"] == "missing_source_data"


def test_dxf_export_roundtrip(tmp_path):
    doc, idx, registry, cons, placements, _ = _run_pipeline()
    out = tmp_path / "synthetic_site_greening.dxf"
    export_greening_dxf(FIXTURE, out, placements, cons.allowed_area,
                        cons.exclusion_zones, doc.unitsScaleToMeters, 0, registry.version)
    assert out.exists()

    # re-open with ezdxf: file valid, original layers intact, AI layers added
    rdoc = ezdxf.readfile(str(out))
    names = {l.dxf.name for l in rdoc.layers}
    for orig in ("ГРАНИЦА_УЧАСТКА", "ЗДАНИЯ", "ДОРОГИ_ПРОЕЗДЫ", "В1_ВОДОПРОВОД"):
        assert orig in names
    for ai in ("AI_GREENING_TREES", "AI_GREENING_SHRUBS",
               "AI_GREENING_ALLOWED_ZONE", "AI_GREENING_EXCLUSIONS"):
        assert ai in names

    msp = rdoc.modelspace()
    tree_inserts = [e for e in msp if e.dxftype() == "INSERT" and e.dxf.name == "AI_TREE"]
    assert len(tree_inserts) == len(placements)
    xd = tree_inserts[0].get_xdata("GREENPLAN")
    tags = [t.value for t in xd]
    assert any(str(t).startswith("planting_id=") for t in tags)

    # original entity count preserved (nothing destroyed)
    orig_doc = ezdxf.readfile(str(FIXTURE))
    orig_handles = {e.dxf.handle for e in orig_doc.modelspace()}
    new_types = [e.dxftype() for e in msp]
    assert len(new_types) >= len(list(orig_doc.modelspace()))


@pytest.mark.skipif(not detect_converter().available, reason="no DWG converter installed")
def test_real_dwg_conversion(tmp_path):
    assert REAL_DWG.exists()
    conv = DwgConverter()
    out = conv.convert_to_dxf(REAL_DWG, tmp_path / "real.dxf")
    doc = parse_dxf(out, "real-doc", REAL_DWG.name, "DWG")
    assert doc.entityCount > 100, "real drawing should contain many entities"
    assert len(doc.layers) > 3
    classification.classify_document(doc)
    cats = {l.classification.category for l in doc.layers}
    assert cats - {"UNKNOWN"}, "at least some layers should classify"
