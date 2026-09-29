"""The end-to-end checks themselves (scripts/invariants.py, check_dxf_output.py)
must pass a healthy result and catch each kind of broken one."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_dxf_output import check as check_dxf  # noqa: E402
from invariants import check_report, check_result_layers  # noqa: E402

SQUARE = [0, 0, 100, 0, 100, 100, 0, 100, 0, 0]
CONSTRAINTS = {"siteBoundary": [SQUARE],
               "allowedArea": {"polygons": [{"shell": [10, 10, 90, 10, 90, 90, 10, 90, 10, 10], "holes": []}]}}
SOURCE = {"document": "СП 42.13330.2016", "clause": "п. 9.6, таблица 9.1"}
HEALTHY = {
    "validation": {"hardViolations": []},
    "regulatory": {"status": "NEEDS_REVIEW"},
    "placements": [{"id": "t1", "kind": "TREE", "position": [50, 50]}],
    "explanations": [{"plantingId": "t1", "checks": [
        {"ruleId": "TREE_BUILDING_MIN_DISTANCE", "result": "PASS", "effectiveSource": "verified", "source": SOURCE},
        {"ruleId": "TREE_EXISTING_TREE_MIN_DISTANCE", "result": "UNVERIFIED", "source": {"document": "tech"}},
        # geometry of the drawing, not a legal citation: no clause expected
        {"ruleId": "PLANTING_INSIDE_SITE_BOUNDARY", "result": "PASS", "effectiveSource": "verified",
         "source": {"document": "Границы участка (геометрия чертежа)", "clause": None}},
    ]}],
    "rulesApplied": [{"id": "PLANTING_INSIDE_SITE_BOUNDARY", "source": {"document": "project_geometry"}}],
    "rejections": {"byReason": [{"key": "NARROW_STRIP", "description": "узкая полоса"},
                                {"key": "TREE_BUILDING_MIN_DISTANCE", "source": SOURCE}]},
    "lawn": {"areaM2": 120.0, "source": {"document": "ППМ № 743-ПП"}},
}


def test_healthy_report_passes():
    assert check_report(HEALTHY, CONSTRAINTS) == []


def test_each_broken_report_is_caught():
    cases = {
        "hard violation": lambda r: r["validation"]["hardViolations"].append({"ruleId": "TREE_ROAD_MIN_DISTANCE"}),
        "outside the allowed area": lambda r: r["placements"][0].update(position=[5, 5]),
        "without an explanation": lambda r: r["explanations"].clear(),
        "without a verified source": lambda r: r["explanations"][0]["checks"][0].update(source={"document": "x"}),
        "failed check": lambda r: r["explanations"][0]["checks"][0].update(result="FAIL"),
        "neither a norm": lambda r: r["rejections"]["byReason"].append({"key": "X"}),
        "lawn without": lambda r: r["lawn"].update(source=None),
        "NON_COMPLIANT": lambda r: r["regulatory"].update(status="NON_COMPLIANT"),
    }
    for expected, break_it in cases.items():
        r = copy.deepcopy(HEALTHY)
        break_it(r)
        problems = check_report(r, CONSTRAINTS)
        assert any(expected in p for p in problems), (expected, problems)


def test_result_only_export_must_hold_result_layers_only(tmp_path):
    d = ezdxf.new("R2018")
    d.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "AI_GREENING_TREES"})
    ok = tmp_path / "ok.dxf"
    d.saveas(ok)
    assert check_result_layers(ok) == []
    d.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "Здания"})
    bad = tmp_path / "bad.dxf"
    d.saveas(bad)
    assert check_result_layers(bad)


def test_autocad_safety_check_catches_a_missing_block(tmp_path):
    d = ezdxf.new("R2018")
    d.blocks.new("TICK").add_line((0, 0), (1, 1))
    d.modelspace().add_blockref("TICK", (0, 0))
    clean = tmp_path / "clean.dxf"
    d.saveas(clean)
    assert check_dxf(clean) == []
    d.blocks.delete_block("TICK", safe=False)
    broken = tmp_path / "broken.dxf"
    d.saveas(broken)
    assert any("INSERT of missing block" in p for p in check_dxf(broken))
