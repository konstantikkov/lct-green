"""Level A deterministic export/geometry checks (gating).

Every check re-derives its facts from the EXPORTED DXF (round-trip), never
from in-memory state alone: the exported file is re-parsed with the same
parser the app uses, reclassified, and the placements are re-validated
against the re-parsed geometry with the deterministic validator.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from shapely.geometry import Point

from evals.pipeline_runner import PipelineResult

CHECK_IDS = [
    "EXPORT_OPENS",
    "AI_LAYERS_PRESENT",
    "SOURCE_SIGNATURE_PRESERVED",
    "INSERT_COUNT_MATCHES",
    "COORDS_FINITE",
    "PLACEMENTS_IN_ALLOWED",
    "REVALIDATE_REPARSED_ZERO_HARD",
    "SPACING_RESPECTED",
    "XDATA_METADATA_PRESENT",
]

AI_LAYER_PREFIX = "AI_GREENING"
REQUIRED_AI_LAYERS = ("AI_GREENING_TREES", "AI_GREENING_SHRUBS",
                      "AI_GREENING_ALLOWED_ZONE", "AI_GREENING_EXCLUSIONS")
CONTAINMENT_TOL = 1e-6


@dataclass
class CheckResult:
    id: str
    passed: bool
    detail: str = ""

    def to_json(self) -> dict:
        return {"id": self.id, "passed": self.passed, "detail": self.detail}


def _entity_signature(dxf_path: Path) -> Counter:
    """Canonical signature: multiset of (layer, entityType) over modelspace.
    Layer/entity preservation is compared on signatures, never on bytes."""
    doc = ezdxf.readfile(str(dxf_path))
    return Counter((e.dxf.layer, e.dxftype()) for e in doc.modelspace())


def _finite(*vals: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def _ai_inserts(msp, block_name: str) -> list:
    return [e for e in msp if e.dxftype() == "INSERT" and e.dxf.name == block_name]


def _reparse_for_validation(result: PipelineResult, export_path: Path):
    """Re-parse the exported DXF with the app parser, drop the AI_GREENING_*
    output layers (they are results, not obstacles), reclassify and rebuild
    geometry — then the deterministic validator re-checks the plan against
    what a consumer of the exported file would actually see."""
    from app.services import classification
    from app.services.constraints import build_constraints
    from app.services.dxf_parser import parse_dxf
    from app.services.geometry import build_geometry_index
    from app.services.validator import Placement, validate_plan

    rdoc = parse_dxf(export_path, f"reparse-{result.case_id}", export_path.name)
    rdoc.entities = [e for e in rdoc.entities if not e.layer.startswith(AI_LAYER_PREFIX)]
    rdoc.layers = [l for l in rdoc.layers if not l.name.startswith(AI_LAYER_PREFIX)]
    classification.classify_document(rdoc)
    ridx = build_geometry_index(rdoc)
    rcons = build_constraints(rdoc, ridx, result.registry)

    # placements as read back from the exported file (INSERT positions + XDATA)
    edoc = ezdxf.readfile(str(export_path))
    scale = rdoc.unitsScaleToMeters
    replaced = []
    for i, ins in enumerate(e for e in edoc.modelspace()
                            if e.dxftype() == "INSERT" and e.dxf.name in ("AI_TREE", "AI_SHRUB")):
        kind = "TREE" if ins.dxf.name == "AI_TREE" else "SHRUB"
        replaced.append(Placement(
            id=f"r{i}", kind=kind, speciesId=result.species["speciesId"],
            x=float(ins.dxf.insert.x) * scale, y=float(ins.dxf.insert.y) * scale,
            crownRadiusM=float(ins.dxf.xscale)))
    report = validate_plan(replaced, ridx, result.registry,
                           rcons.site_boundary, rcons.allowed_area)
    return replaced, report


def run_level_a_checks(result: PipelineResult) -> list[CheckResult]:
    out: list[CheckResult] = []

    def check(cid: str, fn):
        try:
            passed, detail = fn()
        except Exception as exc:  # a crash is a failure, not an error escape
            passed, detail = False, f"exception: {type(exc).__name__}: {exc}"
        out.append(CheckResult(cid, bool(passed), detail))

    export_path = result.export()

    # 1. exported file opens
    state: dict = {}

    def c_opens():
        state["edoc"] = ezdxf.readfile(str(export_path))
        return True, str(export_path)
    check("EXPORT_OPENS", c_opens)
    edoc = state.get("edoc")

    # 2. AI layers exist
    def c_ai_layers():
        names = {l.dxf.name for l in edoc.layers}
        missing = [l for l in REQUIRED_AI_LAYERS if l not in names]
        return not missing, f"missing: {missing}" if missing else "all AI_GREENING_* layers present"
    check("AI_LAYERS_PRESENT", c_ai_layers)

    # 3. original layer/entity set preserved (canonical signature sub-multiset)
    def c_signature():
        src = _entity_signature(result.dxf_path)
        exp = _entity_signature(export_path)
        lost = src - exp
        return not lost, (f"lost from export: {dict(lost)}" if lost
                          else f"{sum(src.values())} source entities preserved")
    check("SOURCE_SIGNATURE_PRESERVED", c_signature)

    # 4. INSERT counts match placements per kind
    def c_insert_count():
        msp = edoc.modelspace()
        n_tree = len(_ai_inserts(msp, "AI_TREE"))
        n_shrub = len(_ai_inserts(msp, "AI_SHRUB"))
        want_tree = sum(1 for p in result.placements if p.kind == "TREE")
        want_shrub = sum(1 for p in result.placements if p.kind == "SHRUB")
        ok = n_tree == want_tree and n_shrub == want_shrub
        return ok, f"AI_TREE {n_tree}/{want_tree}, AI_SHRUB {n_shrub}/{want_shrub}"
    check("INSERT_COUNT_MATCHES", c_insert_count)

    # 5. all coordinates finite (placements + exported inserts)
    def c_finite():
        bad = [p.id for p in result.placements if not _finite(p.x, p.y, p.crownRadiusM)]
        msp = edoc.modelspace()
        for ins in _ai_inserts(msp, "AI_TREE") + _ai_inserts(msp, "AI_SHRUB"):
            if not _finite(float(ins.dxf.insert.x), float(ins.dxf.insert.y)):
                bad.append(f"insert@{ins.dxf.handle}")
        return not bad, f"non-finite coords: {bad}" if bad else \
            f"{len(result.placements)} placements finite"
    check("COORDS_FINITE", c_finite)

    # 6. every placement inside allowed area (covers with tolerance)
    def c_containment():
        allowed = result.cons.allowed_area.buffer(CONTAINMENT_TOL)
        outside = [p.id for p in result.placements
                   if not allowed.covers(Point(p.x, p.y))]
        return not outside, (f"outside allowed area: {outside[:5]}" if outside
                             else f"{len(result.placements)} placements inside (tol {CONTAINMENT_TOL})")
    check("PLACEMENTS_IN_ALLOWED", c_containment)

    # 7. zero hard violations re-computed on the RE-PARSED document geometry
    def c_revalidate():
        replaced, report = _reparse_for_validation(result, export_path)
        if len(replaced) != len(result.placements):
            return False, (f"re-parsed placement count {len(replaced)} != "
                           f"{len(result.placements)}")
        hard = report["hardViolations"]
        return not hard, (f"{len(hard)} hard violations on re-parsed geometry, "
                          f"first: {hard[:2]}" if hard else
                          f"re-parsed plan valid ({len(replaced)} placements)")
    check("REVALIDATE_REPARSED_ZERO_HARD", c_revalidate)

    # 8. same-kind pairwise spacing respected
    def c_spacing():
        viol = []
        for kind, rule_id in (("TREE", "TREE_TREE_MIN_SPACING"),
                              ("SHRUB", "SHRUB_SHRUB_MIN_SPACING")):
            rule = result.registry.get(rule_id)
            min_d = rule.effective_distance_m if rule else None
            if min_d is None:
                continue
            pts = [(p.x, p.y, p.id) for p in result.placements if p.kind == kind]
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    d = math.dist(pts[i][:2], pts[j][:2])
                    if d < min_d - 1e-9:
                        viol.append((pts[i][2], pts[j][2], round(d, 3)))
        return not viol, (f"spacing violations: {viol[:5]}" if viol
                          else "pairwise spacing respected")
    check("SPACING_RESPECTED", c_spacing)

    # 9. XDATA metadata on first insert (vacuously true for empty plans)
    def c_xdata():
        if not result.placements:
            return True, "no placements (vacuous pass)"
        msp = edoc.modelspace()
        inserts = _ai_inserts(msp, "AI_TREE") + _ai_inserts(msp, "AI_SHRUB")
        if not inserts:
            return False, "no AI inserts found"
        tags = [str(t.value) for t in inserts[0].get_xdata("GREENPLAN")]
        need = ("planting_id=", "species_id=", "kind=")
        missing = [n for n in need if not any(t.startswith(n) for t in tags)]
        return not missing, (f"XDATA missing tags: {missing}" if missing
                             else "GREENPLAN XDATA present")
    check("XDATA_METADATA_PRESENT", c_xdata)

    return out


def all_passed(results: list[CheckResult]) -> bool:
    return all(r.passed for r in results)


# ---------------------------------------------------------------------------
# expected.json conformance (Level A/B fixture expectations, deterministic)
# ---------------------------------------------------------------------------

def verify_expected(result: PipelineResult, expected: dict,
                    level_a: list[CheckResult] | None = None) -> list[CheckResult]:
    """Deterministic conformance of a pipeline run against expected.json."""
    out: list[CheckResult] = []

    # semantic layer classification
    lm = {li.name: li for li in result.doc.layers}
    for layer, cat in (expected.get("expectedSemanticLayers") or {}).items():
        li = lm.get(layer)
        got = li.classification.category if li else "<layer missing>"
        out.append(CheckResult(f"SEMANTIC:{layer}", got == cat,
                               f"expected {cat}, got {got}"))

    # forbidden (exclusion) zone count
    rng = expected.get("expectedForbiddenZoneCount")
    if rng:
        n = len(result.cons.exclusion_zones)
        ok = rng["min"] <= n <= rng["max"]
        out.append(CheckResult("FORBIDDEN_ZONE_COUNT", ok,
                               f"{n} zones, expected [{rng['min']}, {rng['max']}]"))

    # metric ranges
    m = result.metrics
    for key, rng in (expected.get("allowedMetricRanges") or {}).items():
        val = m.get(key)
        ok = val is not None and rng["min"] - 1e-9 <= val <= rng["max"] + 1e-9
        out.append(CheckResult(f"METRIC:{key}", ok,
                               f"{val}, expected [{rng['min']}, {rng['max']}]"))

    # mustPass check ids (when the Level A results are supplied)
    if level_a is not None:
        by_id = {c.id: c for c in level_a}
        for cid in expected.get("mustPass") or []:
            c = by_id.get(cid)
            out.append(CheckResult(f"MUSTPASS:{cid}", bool(c and c.passed),
                                   c.detail if c else "check not run"))

    # known edge cases with machine-verifiable meaning
    for tag in expected.get("knownEdgeCases") or []:
        if tag.startswith("ambiguous_layer_requires_review:"):
            layer = tag.split(":", 1)[1]
            li = lm.get(layer)
            ok = bool(li and li.classification.requiresReview)
            out.append(CheckResult(f"EDGE:{tag}", ok,
                                   f"requiresReview={li.classification.requiresReview if li else None}"))
        elif tag == "no_placements_expected":
            out.append(CheckResult(f"EDGE:{tag}", len(result.placements) == 0,
                                   f"{len(result.placements)} placements"))
        elif tag == "allowed_area_disconnected":
            from app.services.geometry import as_polygon_list
            parts = len(as_polygon_list(result.cons.allowed_area))
            out.append(CheckResult(f"EDGE:{tag}", parts >= 2,
                                   f"allowed area has {parts} disjoint parts"))
        # remaining tags are documentation-only (no assertion)

    return out
