"""Deterministic golden-dataset generator (mirrors apps/api/scripts/make_fixture.py).

All drawings are in meters ($INSUNITS=6), R2018, Russian layer names matched
by the production classifier. Geometry is fully deterministic; expected.json
metric ranges are DERIVED by running the real pipeline (seed from rules.json)
and widening +/-30% — never guessed.

Usage:
  PYTHONPATH=apps/api python3 -m evals.dataset.generate            # dxf+rules+notes+expected
  PYTHONPATH=apps/api python3 -m evals.dataset.generate --no-expected
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import ezdxf

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from evals.dataset import CASE_IDS, DATASET_DIR  # noqa: E402

LAYER_COLORS = {
    "ГРАНИЦА_УЧАСТКА": 1, "ЗДАНИЯ": 8, "ДОРОГИ_ПРОЕЗДЫ": 252, "ТРОТУАРЫ": 253,
    "В1_ВОДОПРОВОД": 5, "КЛ-0.4_КАБЕЛЬ": 6, "СУЩ_ДЕРЕВЬЯ": 3, "ГАЗОНЫ": 84,
    "СЕТИ_1": 30,
}

ALL_CHECKS = [
    "EXPORT_OPENS", "AI_LAYERS_PRESENT", "SOURCE_SIGNATURE_PRESERVED",
    "INSERT_COUNT_MATCHES", "COORDS_FINITE", "PLACEMENTS_IN_ALLOWED",
    "REVALIDATE_REPARSED_ZERO_HARD", "SPACING_RESPECTED", "XDATA_METADATA_PRESENT",
]


def _new_doc(layers: list[str]):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6  # meters
    for name in layers:
        doc.layers.new(name, dxfattribs={"color": LAYER_COLORS[name]})
    return doc, doc.modelspace()


def _rect(msp, layer: str, x0, y0, x1, y1):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                       dxfattribs={"layer": layer})


# --------------------------------------------------------------------------
# case builders
# --------------------------------------------------------------------------

def _b001(msp):
    """Simple rectangular site 80x60, nothing else."""
    _rect(msp, "ГРАНИЦА_УЧАСТКА", 0, 0, 80, 60)


def _b002(msp):
    """100x70 site with a 30x20 building in the center."""
    _rect(msp, "ГРАНИЦА_УЧАСТКА", 0, 0, 100, 70)
    _rect(msp, "ЗДАНИЯ", 35, 25, 65, 45)


def _b003(msp):
    """100x70 site crossed by a water main."""
    _rect(msp, "ГРАНИЦА_УЧАСТКА", 0, 0, 100, 70)
    msp.add_lwpolyline([(5, 10), (50, 40), (95, 60)],
                       dxfattribs={"layer": "В1_ВОДОПРОВОД"})


def _b004(msp):
    """120x80 site split into two disconnected planting zones by a full-width
    road strip (y 36..44)."""
    _rect(msp, "ГРАНИЦА_УЧАСТКА", 0, 0, 120, 80)
    _rect(msp, "ДОРОГИ_ПРОЕЗДЫ", 0, 36, 120, 44)


def _b005(msp):
    """Narrow 8 m wide L-corridor (60 m east leg, 40 m north leg)."""
    msp.add_lwpolyline([(0, 0), (60, 0), (60, 8), (8, 8), (8, 40), (0, 40)],
                       close=True, dxfattribs={"layer": "ГРАНИЦА_УЧАСТКА"})


def _b006(msp):
    """90x60 site with six existing trees to keep distance from."""
    _rect(msp, "ГРАНИЦА_УЧАСТКА", 0, 0, 90, 60)
    for cx, cy in [(15, 15), (30, 45), (45, 20), (60, 40), (75, 15), (80, 50)]:
        msp.add_circle((cx, cy), radius=2.5, dxfattribs={"layer": "СУЩ_ДЕРЕВЬЯ"})


def _b007(msp):
    """Impossible: 44x44 site fully covered by a 36x36 building + its 5 m
    tree buffer. Pipeline must yield 0 placements without crashing."""
    _rect(msp, "ГРАНИЦА_УЧАСТКА", 0, 0, 44, 44)
    _rect(msp, "ЗДАНИЯ", 4, 4, 40, 40)


def _b008(msp):
    """Ambiguous utility layer 'СЕТИ_1' (generic keyword, low confidence —
    must classify as UTILITY with requiresReview=True)."""
    _rect(msp, "ГРАНИЦА_УЧАСТКА", 0, 0, 100, 60)
    msp.add_lwpolyline([(10, 5), (50, 30), (90, 55)],
                       dxfattribs={"layer": "СЕТИ_1"})


def _b009(msp):
    """Invalid geometry: self-intersecting (bow-tie) boundary polyline.
    Parser/geometry layer must repair (buffer(0)) or fall back to extents —
    never crash."""
    msp.add_lwpolyline([(0, 0), (100, 60), (100, 0), (0, 60)], close=True,
                       dxfattribs={"layer": "ГРАНИЦА_УЧАСТКА"})
    _rect(msp, "ГАЗОНЫ", 10, 5, 40, 20)


def _b010(msp):
    """Open 150x90 site with a south sidewalk; many equally good layouts."""
    _rect(msp, "ГРАНИЦА_УЧАСТКА", 0, 0, 150, 90)
    _rect(msp, "ТРОТУАРЫ", 0, 4, 150, 7)


@dataclass
class CaseSpec:
    title: str
    layers: list[str]
    build: Callable
    notes: str
    rules: dict = field(default_factory=dict)
    semantic_layers: dict = field(default_factory=dict)
    known_edge_cases: list[str] = field(default_factory=list)
    must_pass: list[str] = field(default_factory=lambda: list(ALL_CHECKS))


BOUNDARY = {"ГРАНИЦА_УЧАСТКА": "SITE_BOUNDARY"}

CASES: dict[str, CaseSpec] = {
    "case_001": CaseSpec(
        title="Simple rectangular site",
        layers=["ГРАНИЦА_УЧАСТКА"], build=_b001,
        semantic_layers=dict(BOUNDARY),
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["PLANTING_INSIDE_SITE_BOUNDARY", "TREE_TREE_MIN_SPACING"]},
        notes="Baseline: empty rectangular parcel 80x60 m. Sanity case — the "
              "optimizer should fill the site evenly, all checks trivially "
              "reachable, no obstacles."),
    "case_002": CaseSpec(
        title="Building in the center",
        layers=["ГРАНИЦА_УЧАСТКА", "ЗДАНИЯ"], build=_b002,
        semantic_layers={**BOUNDARY, "ЗДАНИЯ": "BUILDING"},
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["TREE_BUILDING_MIN_DISTANCE",
                                 "PLANTING_INSIDE_SITE_BOUNDARY", "TREE_TREE_MIN_SPACING"]},
        notes="Central 30x20 m building. Stresses the building footprint + "
              "5 m tree buffer (TREE_BUILDING_MIN_DISTANCE assumption): a ring "
              "shaped allowed area."),
    "case_003": CaseSpec(
        title="Utility line crossing the site",
        layers=["ГРАНИЦА_УЧАСТКА", "В1_ВОДОПРОВОД"], build=_b003,
        semantic_layers={**BOUNDARY, "В1_ВОДОПРОВОД": "UTILITY_WATER"},
        rules={"seed": 42, "restrictToRules": None,
               "overrides": {"TREE_UTILITY_WATER_MIN_DISTANCE":
                             {"assumption": {"enabled": True, "assumedDistanceM": 2.5}}},
               "relevantRules": ["TREE_UTILITY_WATER_MIN_DISTANCE",
                                 "PLANTING_INSIDE_SITE_BOUNDARY", "TREE_TREE_MIN_SPACING"]},
        known_edge_cases=["override_changes_assumed_distance"],
        notes="Water main crossing the parcel diagonally. rules.json overrides "
              "the assumed distance to 2.5 m (still an assumption, exercising "
              "the override path). The buffer must cut a corridor out of the "
              "allowed area."),
    "case_004": CaseSpec(
        title="Two disconnected planting zones",
        layers=["ГРАНИЦА_УЧАСТКА", "ДОРОГИ_ПРОЕЗДЫ"], build=_b004,
        semantic_layers={**BOUNDARY, "ДОРОГИ_ПРОЕЗДЫ": "ROAD"},
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["TREE_ROAD_MIN_DISTANCE",
                                 "PLANTING_INSIDE_SITE_BOUNDARY", "TREE_TREE_MIN_SPACING"]},
        known_edge_cases=["allowed_area_disconnected"],
        notes="A full-width road strip splits the site into two disconnected "
              "allowed sub-areas. Optimizer must plant on BOTH sides; "
              "distribution metric should stay reasonable."),
    "case_005": CaseSpec(
        title="Narrow 8 m L-corridor",
        layers=["ГРАНИЦА_УЧАСТКА"], build=_b005,
        semantic_layers=dict(BOUNDARY),
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["PLANTING_INSIDE_SITE_BOUNDARY", "TREE_TREE_MIN_SPACING"]},
        known_edge_cases=["narrow_corridor_single_file_planting"],
        notes="8 m wide L-shaped corridor. After the ~0.5 m sliver erosion the "
              "plantable strip is ~7 m — trees must line up nearly single "
              "file at >=5 m spacing without leaving the corridor."),
    "case_006": CaseSpec(
        title="Existing trees present",
        layers=["ГРАНИЦА_УЧАСТКА", "СУЩ_ДЕРЕВЬЯ"], build=_b006,
        semantic_layers={**BOUNDARY, "СУЩ_ДЕРЕВЬЯ": "EXISTING_TREE"},
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["TREE_EXISTING_TREE_MIN_DISTANCE",
                                 "TREE_TREE_MIN_SPACING", "PLANTING_INSIDE_SITE_BOUNDARY"]},
        notes="Six existing tree crowns (r=2.5 m). New trees must keep the "
              "3 m protection distance (TREE_EXISTING_TREE_MIN_DISTANCE) and "
              "5 m same-kind spacing to existing trunk positions."),
    "case_007": CaseSpec(
        title="Impossible site (fully blocked)",
        layers=["ГРАНИЦА_УЧАСТКА", "ЗДАНИЯ"], build=_b007,
        semantic_layers={**BOUNDARY, "ЗДАНИЯ": "BUILDING"},
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["TREE_BUILDING_MIN_DISTANCE", "PLANTING_INSIDE_SITE_BOUNDARY"]},
        known_edge_cases=["no_placements_expected", "empty_allowed_area"],
        notes="Building footprint + 5 m buffer cover the whole parcel. The "
              "correct output is an EMPTY plan: 0 placements, valid "
              "validation report, export still succeeds. Crash = failure."),
    "case_008": CaseSpec(
        title="Ambiguous layer name (СЕТИ_1)",
        layers=["ГРАНИЦА_УЧАСТКА", "СЕТИ_1"], build=_b008,
        semantic_layers={**BOUNDARY, "СЕТИ_1": "UTILITY"},
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["TREE_UTILITY_GENERIC_MIN_DISTANCE",
                                 "PLANTING_INSIDE_SITE_BOUNDARY", "TREE_TREE_MIN_SPACING"]},
        known_edge_cases=["ambiguous_layer_requires_review:СЕТИ_1"],
        notes="Generic 'СЕТИ_1' layer: classifier must map it to UTILITY at "
              "low confidence with requiresReview=True, and the conservative "
              "generic-utility setback (2 m) must apply."),
    "case_009": CaseSpec(
        title="Invalid geometry (self-intersecting boundary)",
        layers=["ГРАНИЦА_УЧАСТКА", "ГАЗОНЫ"], build=_b009,
        semantic_layers={**BOUNDARY, "ГАЗОНЫ": "GREEN_AREA"},
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["PLANTING_INSIDE_SITE_BOUNDARY", "TREE_TREE_MIN_SPACING"]},
        known_edge_cases=["self_intersecting_boundary_repaired_or_fallback"],
        notes="Bow-tie boundary polyline (self-intersecting). The geometry "
              "layer must repair it (buffer(0)) or fall back to extents; the "
              "repair log should mention it; pipeline must complete."),
    "case_010": CaseSpec(
        title="Open site, several equally valid layouts",
        layers=["ГРАНИЦА_УЧАСТКА", "ТРОТУАРЫ"], build=_b010,
        semantic_layers={**BOUNDARY, "ТРОТУАРЫ": "SIDEWALK"},
        rules={"seed": 42, "restrictToRules": None, "overrides": {},
               "relevantRules": ["TREE_SIDEWALK_MIN_DISTANCE",
                                 "PLANTING_INSIDE_SITE_BOUNDARY", "TREE_TREE_MIN_SPACING"]},
        known_edge_cases=["multiple_equivalent_optima"],
        notes="Large open parcel: many layouts score equally. Determinism "
              "matters here — same seed must reproduce the same layout even "
              "though many optima exist. Metric ranges are deliberately wide."),
}

BUILDERS = {cid: CASES[cid].build for cid in CASE_IDS}


def build_case(case_id: str, out_dir: Path) -> Path:
    spec = CASES[case_id]
    doc, msp = _new_doc(spec.layers)
    spec.build(msp)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "input.dxf"
    doc.saveas(str(path))
    return path


def write_static(case_id: str, out_dir: Path) -> None:
    spec = CASES[case_id]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rules.json").write_text(
        json.dumps(spec.rules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "notes.md").write_text(
        f"# {case_id} — {spec.title}\n\n{spec.notes}\n\n"
        f"Layers: {', '.join(spec.layers)}\n\n"
        f"Known edge cases: {', '.join(spec.known_edge_cases) or 'none'}\n",
        encoding="utf-8")


def _range(value: float, spread: float = 0.30, digits: int = 4,
           floor_zero_max: float = 0.05) -> dict:
    """+/-30% band around an observed metric; zero gets a small tolerance band."""
    if value <= 0:
        return {"min": 0.0, "max": floor_zero_max}
    return {"min": round(value * (1 - spread), digits),
            "max": round(min(1.0, value * (1 + spread)), digits)
            if value <= 1.0 else round(value * (1 + spread), digits)}


def _count_range(n: int, spread: float = 0.30) -> dict:
    if n == 0:
        return {"min": 0, "max": 0}
    return {"min": int(math.floor(n * (1 - spread))),
            "max": int(math.ceil(n * (1 + spread)))}


def derive_expected(case_id: str, out_dir: Path) -> dict:
    """Run the actual pipeline and derive expected.json (ranges widened 30%)."""
    from evals.pipeline_runner import run_case_uncached
    spec = CASES[case_id]
    res = run_case_uncached(out_dir)
    m = res.metrics
    expected = {
        "case": case_id,
        "title": spec.title,
        "derivedFrom": {"seed": res.seed, "spreadPct": 30,
                        "observed": {"treeCount": m["treeCount"],
                                     "coverageScore": m["coverageScore"],
                                     "distributionScore": m["distributionScore"],
                                     "forbiddenZoneCount": len(res.cons.exclusion_zones)}},
        "mustPass": spec.must_pass,
        "expectedSemanticLayers": spec.semantic_layers,
        "expectedForbiddenZoneCount": _count_range(len(res.cons.exclusion_zones)),
        "allowedMetricRanges": {
            "treeCount": _count_range(m["treeCount"]),
            "coverageScore": _range(m["coverageScore"]),
            "distributionScore": _range(m["distributionScore"], floor_zero_max=0.1),
        },
        "knownEdgeCases": spec.known_edge_cases,
    }
    (out_dir / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return expected


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the golden dataset")
    ap.add_argument("--cases", nargs="*", default=CASE_IDS)
    ap.add_argument("--no-expected", action="store_true",
                    help="skip running the pipeline to derive expected.json")
    args = ap.parse_args(argv)
    for cid in args.cases:
        d = DATASET_DIR / cid
        build_case(cid, d)
        write_static(cid, d)
        print(f"{cid}: input.dxf + rules.json + notes.md", flush=True)
        if not args.no_expected:
            exp = derive_expected(cid, d)
            obs = exp["derivedFrom"]["observed"]
            print(f"{cid}: expected.json derived (trees={obs['treeCount']}, "
                  f"coverage={obs['coverageScore']}, distribution={obs['distributionScore']}, "
                  f"zones={obs['forbiddenZoneCount']})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
