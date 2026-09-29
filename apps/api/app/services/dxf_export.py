"""DXF export: open the ORIGINAL DXF (all source layers/entities preserved),
add AI_GREENING_* result layers, tree/shrub BLOCKs with XDATA metadata, and
write `<original>_greening.dxf` + a JSON provenance report.
"""
from __future__ import annotations

import json
from pathlib import Path

import ezdxf
from ezdxf import colors as ezcolors

APPID = "GREENPLAN"

LAYERS = {
    "AI_GREENING_TREES": 3,          # green
    "AI_GREENING_SHRUBS": 92,        # light green
    "AI_GREENING_ALLOWED_ZONE": 4,   # cyan
    "AI_GREENING_EXCLUSIONS": 1,     # red
    "AI_GREENING_DEBUG": 8,          # grey
    "AI_GREENING_REJECTED": 6,       # magenta: rejected candidates + reason
    "AI_GREENING_LAWN": 82,          # green: recommended lawn (herbaceous cover)
}


def _ensure_setup(doc):
    if APPID not in doc.appids:
        doc.appids.new(APPID)
    for name, color in LAYERS.items():
        if name not in doc.layers:
            doc.layers.new(name, dxfattribs={"color": color})


def _ensure_blocks(doc, scale: float):
    """Reusable plant symbols, drawn in drawing units (1 m nominal radius)."""
    u = 1.0 / scale  # drawing units per meter
    if "AI_TREE" not in doc.blocks:
        blk = doc.blocks.new("AI_TREE")
        blk.add_circle((0, 0), radius=1.0 * u)
        blk.add_circle((0, 0), radius=0.12 * u)
        blk.add_line((-0.3 * u, 0), (0.3 * u, 0))
        blk.add_line((0, -0.3 * u), (0, 0.3 * u))
    if "AI_SHRUB" not in doc.blocks:
        blk = doc.blocks.new("AI_SHRUB")
        blk.add_circle((0, 0), radius=1.0 * u)
        blk.add_circle((0, 0), radius=0.55 * u)


def _add_poly_rings(msp, rings_geom, layer: str, scale: float):
    from .geometry import as_polygon_list
    u = 1.0 / scale
    for poly in as_polygon_list(rings_geom):
        pts = [(x * u, y * u) for x, y in poly.exterior.coords]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
        for hole in poly.interiors:
            hpts = [(x * u, y * u) for x, y in hole.coords]
            msp.add_lwpolyline(hpts, close=True, dxfattribs={"layer": layer})


def export_greening_dxf(original_dxf: Path, out_path: Path, placements: list,
                        allowed_area, exclusion_zones, scale_to_meters: float,
                        iteration_index: int, ruleset_version: str,
                        include_debug_zones: bool = True,
                        rejected_samples: list | None = None,
                        lawn=None) -> Path:
    from .cad_package import make_autocad_safe
    doc = ezdxf.readfile(str(original_dxf))
    # the source is LibreDWG output: repair what AutoCAD would reject as a
    # damaged file although ezdxf reads it (a no-op for merged packages)
    make_autocad_safe(doc)
    _ensure_setup(doc)
    _ensure_blocks(doc, scale_to_meters)
    msp = doc.modelspace()
    u = 1.0 / scale_to_meters

    for p in placements:
        layer = "AI_GREENING_TREES" if p.kind == "TREE" else "AI_GREENING_SHRUBS"
        block = "AI_TREE" if p.kind == "TREE" else "AI_SHRUB"
        ins = msp.add_blockref(block, (p.x * u, p.y * u), dxfattribs={
            "layer": layer,
            "xscale": p.crownRadiusM, "yscale": p.crownRadiusM, "zscale": 1.0,
        })
        ins.set_xdata(APPID, [
            (1000, f"planting_id={p.id}"),
            (1000, f"species_id={p.speciesId}"),
            (1000, f"kind={p.kind}"),
            (1070, iteration_index),
            (1000, f"rule_set_version={ruleset_version}"),
            (1040, float(round(p.score, 4))),
        ])

    if allowed_area is not None:
        _add_poly_rings(msp, allowed_area, "AI_GREENING_ALLOWED_ZONE", scale_to_meters)

    if lawn is not None and not lawn.is_empty:
        _add_poly_rings(msp, lawn, "AI_GREENING_LAWN", scale_to_meters)

    if include_debug_zones and exclusion_zones:
        # merged per rule to keep the file light
        from shapely.ops import unary_union
        by_rule: dict[str, list] = {}
        for z in exclusion_zones:
            by_rule.setdefault(z.ruleId, []).append(z.geometry)
        for rule_id, geoms in by_rule.items():
            merged = unary_union(geoms)
            from .geometry import as_polygon_list
            for poly in as_polygon_list(merged):
                pts = [(x * u, y * u) for x, y in poly.exterior.coords]
                pl = msp.add_lwpolyline(pts, close=True,
                                        dxfattribs={"layer": "AI_GREENING_EXCLUSIONS"})
                pl.set_xdata(APPID, [(1000, f"rule_id={rule_id}")])

    # rejected candidate points (sample) with their reasons: rule ids point to
    # the norm in the JSON report ("rejections.byReason[].source")
    for r in rejected_samples or []:
        x, y = r["position"]
        pt = msp.add_point((x * u, y * u), dxfattribs={"layer": "AI_GREENING_REJECTED"})
        tags = [(1000, f"species_id={r.get('speciesId', '')}"), (1000, f"kind={r.get('kind', '')}")]
        tags += [(1000, f"reason={k}"[:250]) for k in r.get("reasons", [])[:8]]
        pt.set_xdata(APPID, tags)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_path))
    return out_path


def empty_like(scale_to_meters: float, out_path: Path) -> Path:
    """An empty DXF in the source drawing's units (the scale the parser
    settled on) — the base for a result-only export: AI_GREENING_* layers in
    the source coordinates, attachable to the general plan as an xref."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = {1.0: 6, 0.01: 5, 0.001: 4}.get(round(scale_to_meters, 6), 6)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_path))
    return out_path


def export_report(out_path: Path, report: dict) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
