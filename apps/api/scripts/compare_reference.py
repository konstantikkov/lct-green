"""Compares a project's result with the designers' planting plan (the
reference, e.g. Олимпийская_посадочный.dwg): which norms the reference
plantings break by our rules, how many lie in our allowed area, how close our
trees come to theirs. Runs inside the API container, where the project and
the DWG converter live:

    docker exec <api> python scripts/compare_reference.py <projectId> <reference.dwg|dxf>
        [--tree-layers PLNT_TREE_,PL_TREES_] [--shrub-layers PLNT_BUSH_]

Plantings of the reference are block references on layers with the given
prefixes. Prints JSON.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def reference_plantings(path: Path, tree_prefixes: tuple[str, ...], shrub_prefixes: tuple[str, ...]):
    import ezdxf
    from app.services.dwg_converter import DwgConverter
    if path.suffix.lower() == ".dwg":
        out = Path(tempfile.mkdtemp()) / "reference.dxf"
        DwgConverter().convert_to_dxf(path, out)
        path = out
    doc = ezdxf.readfile(str(path))
    trees, shrubs = [], []
    for e in doc.modelspace().query("INSERT"):
        p = (e.dxf.insert.x, e.dxf.insert.y)
        if e.dxf.layer.startswith(tree_prefixes):
            trees.append(p)
        elif e.dxf.layer.startswith(shrub_prefixes):
            shrubs.append(p)
    return trees, shrubs


def compare(pid: str, trees: list, shrubs: list) -> dict:
    from shapely import STRtree
    from shapely.geometry import Point
    from shapely.prepared import prep
    from app import pipeline, store
    from app.services.validator import Placement, validate_plan

    rt = pipeline.get_runtime(pid)
    if rt is None:
        raise SystemExit(f"no project {pid}")
    idx, reg, cons = pipeline.ensure_geometry(rt), pipeline.ensure_registry(rt), pipeline.ensure_constraints(rt)
    inside, allowed = prep(cons.site_boundary), prep(cons.allowed_area)
    trees = [t for t in trees if inside.contains(Point(*t))]
    shrubs = [s for s in shrubs if inside.contains(Point(*s))]
    # the reference species are not in our catalogue: a typical small street
    # tree (crown 5 m) and shrub (1.5 m)
    pl = [Placement(id=f"ref_t{i}", kind="TREE", speciesId="ref", x=x, y=y, crownRadiusM=2.5)
          for i, (x, y) in enumerate(trees)]
    pl += [Placement(id=f"ref_s{i}", kind="SHRUB", speciesId="ref", x=x, y=y, crownRadiusM=0.75)
           for i, (x, y) in enumerate(shrubs)]
    v = validate_plan(pl, idx, reg, cons.site_boundary, cons.allowed_area,
                      site_boundary_source=cons.site_boundary_kind,
                      visibility_zones=cons.visibility_zones())
    role = {r.id: r.role for r in reg.rules}
    by_rule: dict[str, set] = collections.defaultdict(set)
    for h in v["hardViolations"]:
        by_rule[h["ruleId"]].add(h["plantingId"])
    norm = set().union(*[ids for rid, ids in by_rule.items() if role.get(rid, "regulatory") == "regulatory"] or [set()])
    buffer_only = set().union(*[ids for rid, ids in by_rule.items() if role.get(rid) == "safe_buffer"] or [set()]) - norm

    def share(ids: set, kind: str) -> dict:
        n = len(trees) if kind == "t" else len(shrubs)
        k = sum(1 for i in ids if i.startswith(f"ref_{kind}"))
        return {"count": k, "percent": round(100 * k / n, 1) if n else 0.0}

    recs = store.list_iterations(pid)
    rec = next((r for r in recs if r.get("selected")), recs[-1]) if recs else {"placements": []}
    ours = rec.get("placements") or []
    our_trees = [p for p in ours if p["kind"] == "TREE"]
    ref_pts = [Point(*t) for t in trees]
    tree_index = STRtree(ref_pts) if ref_pts else None
    near = sum(1 for p in our_trees if tree_index is not None
               and tree_index.query(Point(*p["position"]).buffer(3.0), predicate="intersects").size)
    return {
        "siteAreaM2": round(cons.site_boundary.area),
        "reference": {"trees": len(trees), "shrubs": len(shrubs)},
        "ours": {"trees": len(our_trees), "shrubs": len(ours) - len(our_trees),
                 "lawnM2": (rec.get("lawn") or {}).get("areaM2")},
        "referenceBreakingNorms": {"trees": share(norm, "t"), "shrubs": share(norm, "s")},
        "referenceInProjectBuffersOnly": {"trees": share(buffer_only, "t"), "shrubs": share(buffer_only, "s")},
        "referenceTreesInOurAllowedArea": sum(1 for t in trees if allowed.contains(Point(*t))),
        "ourTreesWithin3mOfReferenceTree": near,
        "byRule": sorted(({"ruleId": rid, "role": role.get(rid),
                           "trees": sum(1 for i in ids if i.startswith("ref_t")),
                           "shrubs": sum(1 for i in ids if i.startswith("ref_s"))}
                          for rid, ids in by_rule.items()), key=lambda r: -(r["trees"] + r["shrubs"])),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("reference", type=Path)
    ap.add_argument("--tree-layers", default="PLNT_TREE_,PL_TREES_")
    ap.add_argument("--shrub-layers", default="PLNT_BUSH_")
    a = ap.parse_args()
    t, s = reference_plantings(a.reference, tuple(a.tree_layers.split(",")), tuple(a.shrub_layers.split(",")))
    print(json.dumps(compare(a.project_id, t, s), ensure_ascii=False, indent=1))
