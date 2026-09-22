"""JudgePacket — deterministic, compact, evidence-bearing input for the LLM judge.

Pure construction, no LLM anywhere in this module. Every quality claim the
judge can make must point at evidence IDs minted here (quadrants, clusters,
underused zones, uncertain layers). The packet is kept small (< ~700 tokens
serialized): numbers are rounded, lists capped by salience.

Schema version: "judge-packet-1" (bump on any structural change).
"""
from __future__ import annotations

import hashlib
import json
import math

from shapely.geometry import Point, box
from shapely.ops import unary_union

from .geometry import as_polygon_list
from .optimizer import OptimizerParams

PACKET_SCHEMA_VERSION = "judge-packet-1"

# Whitelisted optimizer parameters the judge may propose changes for.
# min/max are absolute bounds; maxDeltaPerIteration bounds a single step.
PARAMETER_WHITELIST: dict[str, dict[str, float]] = {
    "distributionWeight": {"min": 0.0, "max": 1.0, "maxDeltaPerIteration": 0.25},
    "coverageWeight": {"min": 0.0, "max": 1.0, "maxDeltaPerIteration": 0.25},
    "spacingWeight": {"min": 0.0, "max": 1.0, "maxDeltaPerIteration": 0.25},
    "streetWeight": {"min": 0.0, "max": 1.0, "maxDeltaPerIteration": 0.25},
    "candidateDensity": {"min": 0.3, "max": 3.0, "maxDeltaPerIteration": 0.6},
}

GRID_N = 4                 # 4×4 quadrant grid over the allowed-area bbox
CLUSTER_LINK_FACTOR = 1.5  # plantings closer than 1.5×spacing are linked
CLUSTER_MIN_SIZE = 5       # only report clusters with ≥ 5 members
UNDERUSED_FRACTION = 0.3   # density < 30% of plan mean ⇒ underused
EDGE_BAND_M = 5.0          # "near boundary" band width
MAX_QUADRANTS = 8
MAX_CLUSTERS = 5
MAX_UNDERUSED = 5
MAX_UNCERTAIN_LAYERS = 6


def _round(v: float, nd: int = 3) -> float:
    return round(float(v), nd)


# ---------------------------------------------------------------------------
# spatial diagnostics
# ---------------------------------------------------------------------------

def _quadrant_stats(points: list[tuple[float, float]], allowed_area) -> list[dict]:
    """Full (uncapped) 4×4 quadrant occupancy over the allowed-area bbox.

    Only cells actually intersecting the allowed area are reported; ids are
    stable `q_<col>_<row>` with col/row in [0..3] from the bbox min corner.
    """
    polys = as_polygon_list(allowed_area)
    if not polys:
        return []
    minx = min(p.bounds[0] for p in polys)
    miny = min(p.bounds[1] for p in polys)
    maxx = max(p.bounds[2] for p in polys)
    maxy = max(p.bounds[3] for p in polys)
    w = (maxx - minx) / GRID_N or 1.0
    h = (maxy - miny) / GRID_N or 1.0

    cells: list[dict] = []
    for i in range(GRID_N):
        for j in range(GRID_N):
            cell = box(minx + i * w, miny + j * h, minx + (i + 1) * w, miny + (j + 1) * h)
            allowed_m2 = sum(cell.intersection(p).area for p in polys if cell.intersects(p))
            if allowed_m2 < 1.0:  # cell does not meaningfully intersect allowed area
                continue
            n = sum(1 for (x, y) in points
                    if minx + i * w <= x < minx + (i + 1) * w + 1e-9
                    and miny + j * h <= y < miny + (j + 1) * h + 1e-9)
            cells.append({
                "id": f"q_{i}_{j}",
                "allowedM2": int(round(allowed_m2)),
                "plantings": n,
                "densityPerHa": _round(n / (allowed_m2 / 10000.0), 1),
            })
    return cells


def _clusters(points: list[tuple[float, float]], spacing_m: float) -> list[dict]:
    """Greedy single-linkage clusters: points closer than 1.5×spacing are
    linked (union-find); only components with ≥ CLUSTER_MIN_SIZE reported."""
    n = len(points)
    if n < CLUSTER_MIN_SIZE:
        return []
    thr2 = (CLUSTER_LINK_FACTOR * spacing_m) ** 2
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        xi, yi = points[i]
        for j in range(i + 1, n):
            dx = points[j][0] - xi
            dy = points[j][1] - yi
            if dx * dx + dy * dy < thr2:
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[ra] = rb

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out = []
    for members in sorted(groups.values(), key=len, reverse=True):
        if len(members) < CLUSTER_MIN_SIZE:
            continue
        cx = sum(points[i][0] for i in members) / len(members)
        cy = sum(points[i][1] for i in members) / len(members)
        radius = max(math.dist((cx, cy), points[i]) for i in members)
        out.append({
            "id": f"cluster_{len(out) + 1}",
            "size": len(members),
            "centroid": [_round(cx, 1), _round(cy, 1)],
            "radius": _round(radius, 1),
        })
    return out


def _edge_density(points: list[tuple[float, float]], allowed_area) -> float:
    """Fraction of plantings within EDGE_BAND_M of the allowed-area boundary."""
    polys = as_polygon_list(allowed_area)
    if not polys or not points:
        return 0.0
    boundary = unary_union([p.boundary for p in polys])
    near = sum(1 for (x, y) in points if boundary.distance(Point(x, y)) < EDGE_BAND_M)
    return _round(near / len(points), 3)


def build_spatial_diagnostics(points: list[tuple[float, float]], allowed_area,
                              spacing_m: float) -> tuple[dict, dict]:
    """Returns (diagnostics_capped_for_packet, full_stats_for_components)."""
    quadrants = _quadrant_stats(points, allowed_area)
    total_allowed = sum(q["allowedM2"] for q in quadrants) or 1.0
    mean_density = (len(points) / (total_allowed / 10000.0)) if quadrants else 0.0

    underused_full = [
        {"id": f"under_{q['id']}", "allowedM2": q["allowedM2"], "plantings": q["plantings"]}
        for q in quadrants if q["densityPerHa"] < UNDERUSED_FRACTION * mean_density
    ]
    clusters_full = _clusters(points, spacing_m)

    # salience cap: quadrants deviating most from the mean density first
    quadrants_capped = sorted(
        quadrants, key=lambda q: abs(q["densityPerHa"] - mean_density), reverse=True,
    )[:MAX_QUADRANTS]
    quadrants_capped.sort(key=lambda q: q["id"])

    diagnostics = {
        "quadrants": quadrants_capped,
        "clusters": clusters_full[:MAX_CLUSTERS],
        "underusedZones": sorted(underused_full, key=lambda z: z["allowedM2"],
                                 reverse=True)[:MAX_UNDERUSED],
        "edgeDensity": _edge_density(points, allowed_area),
    }
    full = {
        "quadrants": quadrants,
        "clusters": clusters_full,
        "underusedZones": underused_full,
        "meanDensityPerHa": mean_density,
        "totalAllowedM2": total_allowed,
    }
    return diagnostics, full


# ---------------------------------------------------------------------------
# deterministic quality components (for the UI summary — never from the LLM)
# ---------------------------------------------------------------------------

def quality_components(metrics: dict, full_stats: dict) -> dict:
    """Deterministic component scores for the stored report / UI. No LLM."""
    planting_count = int(metrics.get("plantingCount", 0)) or 1
    underused_m2 = sum(z["allowedM2"] for z in full_stats.get("underusedZones", []))
    total_m2 = full_stats.get("totalAllowedM2", 0) or 1.0
    clustered = sum(c["size"] for c in full_stats.get("clusters", []))
    return {
        "coverage": _round(metrics.get("coverageScore", 0.0)),
        "distribution": _round(metrics.get("distributionScore", 0.0)),
        "utilizationBalance": _round(max(0.0, 1.0 - underused_m2 / total_m2)),
        "clusterPenalty": _round(min(1.0, clustered / planting_count)),
    }


# ---------------------------------------------------------------------------
# packet
# ---------------------------------------------------------------------------

def build_judge_packet(objective: str, iteration: int, max_iterations: int,
                       params: OptimizerParams, validation: dict, allowed_area,
                       placements: list, spacing_m: float,
                       classification_uncertainty: list[dict] | None = None,
                       previous_metrics: dict | None = None) -> dict:
    """Deterministic judge input. `placements` accepts objects with .x/.y
    (validator.Placement) or dicts with "position": [x, y]."""
    metrics = validation.get("metrics", {})
    points: list[tuple[float, float]] = []
    for p in placements:
        if hasattr(p, "x"):
            points.append((float(p.x), float(p.y)))
        else:
            pos = p.get("position") or [p.get("x"), p.get("y")]
            points.append((float(pos[0]), float(pos[1])))

    diagnostics, full_stats = build_spatial_diagnostics(points, allowed_area, spacing_m)

    allowed_m2 = full_stats.get("totalAllowedM2", 0) or 1.0
    canopy_m2 = float(metrics.get("canopyAreaM2", 0.0))

    uncertainty = []
    for i, layer in enumerate((classification_uncertainty or [])[:MAX_UNCERTAIN_LAYERS]):
        uncertainty.append({
            "id": f"layer_{i}",
            "name": str(layer.get("name", ""))[:48],
            "category": layer.get("category", "UNKNOWN"),
            "confidence": _round(layer.get("confidence", 0.0), 2),
        })

    previous = None
    if iteration > 0 and previous_metrics:
        previous = {
            "coverageDelta": _round(metrics.get("coverageScore", 0.0) -
                                    previous_metrics.get("coverageScore", 0.0), 4),
            "distributionDelta": _round(metrics.get("distributionScore", 0.0) -
                                        previous_metrics.get("distributionScore", 0.0), 4),
            "plantingCountDelta": int(metrics.get("plantingCount", 0)) -
                                  int(previous_metrics.get("plantingCount", 0)),
        }

    packet = {
        "schemaVersion": PACKET_SCHEMA_VERSION,
        "objective": objective,
        "iteration": iteration,
        "maxIterations": max_iterations,
        "plan": {
            "treeCount": int(metrics.get("treeCount", 0)),
            "shrubCount": int(metrics.get("shrubCount", 0)),
            "allowedAreaCoverage": _round(min(1.0, canopy_m2 / allowed_m2)),
            "canopyCoverage": _round(metrics.get("coverageScore", 0.0)),
            "distributionUniformity": _round(metrics.get("distributionScore", 0.0)),
        },
        "validation": {
            "hardViolations": len(validation.get("hardViolations", [])),
            "warnings": len(validation.get("warnings", [])),
        },
        "spatialDiagnostics": diagnostics,
        "classificationUncertainty": uncertainty,
        "previousIteration": previous,
        "allowedParameterChanges": {
            name: {**{k: bounds[k] for k in ("min", "max", "maxDeltaPerIteration")},
                   "current": _round(getattr(params, name))}
            for name, bounds in PARAMETER_WHITELIST.items()
        },
        # deterministic UI components; copied to the report as deterministicComponents
        "qualityComponents": quality_components(metrics, full_stats),
    }
    return packet


def packet_json(packet: dict) -> str:
    return json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def packet_hash(packet: dict) -> str:
    """sha256 over the canonical packet JSON — stored with every judgment."""
    return hashlib.sha256(packet_json(packet).encode("utf-8")).hexdigest()


def collect_evidence_ids(packet: dict) -> set[str]:
    """All evidence IDs the judge is allowed to cite. Anything else is stripped."""
    ids: set[str] = set()
    diag = packet.get("spatialDiagnostics", {})
    for key in ("quadrants", "clusters", "underusedZones"):
        for item in diag.get(key, []):
            ids.add(item["id"])
    for item in packet.get("classificationUncertainty", []):
        ids.add(item["id"])
    return ids
