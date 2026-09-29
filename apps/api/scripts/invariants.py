"""Invariants every GreenPlan result must hold — checked after each end-to-end
run (scripts/e2e_run.py) and in the tests. Each function returns a list of
violations; empty means the invariant holds.

Report-level (the JSON interpretation file, ТЗ п. 8):
  * no hard violations and the verdict is not NON_COMPLIANT;
  * every planting lies inside the site boundary and the allowed area;
  * every planting has an explanation, no check FAILs, and every PASS check
    names a verified source (document + clause);
  * every rejection reason carries a norm or a description;
  * the lawn, when present, cites its norm.
DXF-level:
  * the result-only export holds AI_GREENING_* layers only;
  * the full export opens in AutoCAD (check_dxf_output);
  * the source drawing is untouched (check_dxf_layers, single-file inputs).
"""
from __future__ import annotations

from pathlib import Path

RESULT_PREFIX = "AI_GREENING_"
_TOL_M = 0.05  # geometry round-trips through the API at 3 decimals


def _polygons(rings: list[dict] | None):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = []
    for r in rings or []:
        shell = r["shell"] if isinstance(r, dict) else r
        holes = r.get("holes", []) if isinstance(r, dict) else []
        pts = list(zip(shell[0::2], shell[1::2]))
        if len(pts) >= 3:
            polys.append(Polygon(pts, [list(zip(h[0::2], h[1::2])) for h in holes]).buffer(0))
    return unary_union(polys) if polys else None


def check_report(report: dict, constraints: dict | None = None) -> list[str]:
    from shapely.geometry import Point
    out: list[str] = []
    hard = (report.get("validation") or {}).get("hardViolations") or []
    if hard:
        out.append(f"{len(hard)} hard violation(s), e.g. {hard[0].get('ruleId')}")
    status = (report.get("regulatory") or {}).get("status")
    if status == "NON_COMPLIANT":
        out.append("regulatory status NON_COMPLIANT")
    placements = report.get("placements") or []

    if constraints:
        site = _polygons([{"shell": s} for s in (constraints.get("siteBoundary") or [])])
        allowed = _polygons((constraints.get("allowedArea") or {}).get("polygons"))
        site_t = site.buffer(_TOL_M) if site is not None else None
        allowed_t = allowed.buffer(_TOL_M) if allowed is not None else None
        outside_site = [p["id"] for p in placements
                        if site_t is not None and not site_t.contains(Point(*p["position"]))]
        outside_allowed = [p["id"] for p in placements
                           if allowed_t is not None and not allowed_t.contains(Point(*p["position"]))]
        if outside_site:
            out.append(f"{len(outside_site)} planting(s) outside the site boundary, e.g. {outside_site[0]}")
        if outside_allowed:
            out.append(f"{len(outside_allowed)} planting(s) outside the allowed area, e.g. {outside_allowed[0]}")

    explained = {x.get("plantingId") for x in report.get("explanations") or []}
    missing = [p["id"] for p in placements if p["id"] not in explained]
    if missing:
        out.append(f"{len(missing)} planting(s) without an explanation, e.g. {missing[0]}")
    # rules whose source is the drawing itself (planting inside the site
    # boundary) are geometry, not a legal citation — no clause to name
    geometric = {r["id"] for r in report.get("rulesApplied") or []
                 if (r.get("source") or {}).get("document") == "project_geometry"}
    fails, unsourced = [], []
    for x in report.get("explanations") or []:
        for ch in x.get("checks") or []:
            if ch.get("result") == "FAIL":
                fails.append(f"{x['plantingId']}:{ch.get('ruleId')}")
            if ch.get("result") == "PASS" and ch.get("ruleId") not in geometric:
                src = ch.get("source") or {}
                if not src.get("document") or not src.get("clause") or ch.get("effectiveSource") != "verified":
                    unsourced.append(f"{x['plantingId']}:{ch.get('ruleId')}")
    if fails:
        out.append(f"{len(fails)} failed check(s) in explanations, e.g. {fails[0]}")
    if unsourced:
        out.append(f"{len(unsourced)} PASS check(s) without a verified source clause, e.g. {unsourced[0]}")

    for r in ((report.get("rejections") or {}).get("byReason") or []):
        if not r.get("source") and not r.get("description"):
            out.append(f"rejection reason {r.get('key')} has neither a norm nor a description")
    lawn = report.get("lawn")
    if lawn and lawn.get("areaM2", 0) > 0 and not (lawn.get("source") or {}).get("document"):
        out.append("lawn without its norm")
    return out


def check_result_layers(path: str | Path) -> list[str]:
    """The result-only export (layers=result) must hold nothing but result layers."""
    import ezdxf
    doc = ezdxf.readfile(str(path))
    foreign = sorted({e.dxf.layer for e in doc.modelspace() if not e.dxf.layer.startswith(RESULT_PREFIX)})
    return [f"non-result layer(s) in the result-only export: {', '.join(foreign[:5])}"] if foreign else []


def check_full_dxf(path: str | Path) -> list[str]:
    from check_dxf_output import check
    return check(path)


def check_source_untouched(source: str | Path, result: str | Path) -> list[str]:
    """Runs scripts/check_dxf_layers.py (source layers and entities unchanged)."""
    import subprocess
    import sys
    script = Path(__file__).with_name("check_dxf_layers.py")
    proc = subprocess.run([sys.executable, str(script), str(source), str(result)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode == 0:
        return []
    return ["source drawing changed by the export: " + " | ".join(proc.stdout.strip().splitlines()[-5:])]
