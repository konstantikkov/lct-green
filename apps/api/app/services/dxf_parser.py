"""DXF → CAD IR parser built on ezdxf.

Non-destructive: the original DXF file is kept untouched on disk and is later
re-opened for export so original entities/layers survive byte-for-byte at the
ezdxf level. This module produces the normalized (meters) IR used by geometry
algorithms and rendering.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterator

import ezdxf
from ezdxf import colors as ezcolors
from ezdxf import path as ezpath

from .. import config
from ..models.ir import CadDocument, Extents, IREntity, LayerInfo

# $INSUNITS → (name, scale to meters)
_INSUNITS = {
    0: ("unitless", None),
    1: ("in", 0.0254),
    2: ("ft", 0.3048),
    4: ("mm", 0.001),
    5: ("cm", 0.01),
    6: ("m", 1.0),
    7: ("km", 1000.0),
}

_CURVE_TYPES = {"ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
_MAX_INSERT_VIRTUAL = 400
_FLATTEN_DIST = 0.05  # drawing units before scaling; refined by unit scale below


def _aci_hex(aci: int) -> str:
    try:
        r, g, b = ezcolors.aci2rgb(aci if 0 < aci < 256 else 7)
    except Exception:
        r, g, b = (136, 153, 170)
    # pure white/black lines are invisible on dark/light UI — normalize to grey
    if (r, g, b) in ((255, 255, 255), (0, 0, 0)):
        r, g, b = (148, 163, 184)
    return f"#{r:02x}{g:02x}{b:02x}"


def _guess_units(code: int, span: float) -> tuple[str, float, str]:
    """Return (unit label, scale_to_meters, note)."""
    name, scale = _INSUNITS.get(code, ("unknown", None))
    if scale is not None:
        return name, scale, ""
    # Heuristic for unitless drawings: site plans larger than 2 km are implausible,
    # so a >20000-unit span is almost certainly millimeters.
    if span > 20000:
        return "unitless(assumed mm)", 0.001, f"$INSUNITS={code}; span {span:.0f} → assumed millimeters"
    return "unitless(assumed m)", 1.0, f"$INSUNITS={code}; span {span:.0f} → assumed meters"


def _flatten(entity, dist: float) -> list[tuple[float, float]]:
    try:
        p = ezpath.make_path(entity)
        return [(v.x, v.y) for v in p.flattening(dist)]
    except Exception:
        return []


def _entity_points(e, dist: float) -> tuple[str, dict] | None:
    """Extract (kind, geometry-dict) in drawing units. Returns None when unsupported."""
    t = e.dxftype()
    try:
        if t == "LINE":
            return "polyline", {"points": [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)], "closed": False}
        if t in ("LWPOLYLINE", "POLYLINE"):
            pts = _flatten(e, dist)
            if len(pts) < 2:
                return None
            closed = bool(getattr(e, "closed", False) or (e.dxf.hasattr("flags") and e.dxf.flags & 1))
            if len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9:
                closed = True
            return "polyline", {"points": pts, "closed": closed}
        if t in ("CIRCLE",):
            pts = _flatten(e, dist)
            return "polyline", {"points": pts, "closed": True,
                                "center": (e.dxf.center.x, e.dxf.center.y), "radius": float(e.dxf.radius)}
        if t in ("ARC", "ELLIPSE", "SPLINE"):
            pts = _flatten(e, dist)
            if len(pts) < 2:
                return None
            closed = len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9
            return "polyline", {"points": pts, "closed": closed}
        if t == "POINT":
            return "point", {"position": (e.dxf.location.x, e.dxf.location.y)}
        if t in ("TEXT", "ATTRIB"):
            ins = e.dxf.insert
            return "text", {"position": (ins.x, ins.y), "text": e.dxf.text or "", "height": float(e.dxf.height or 0)}
        if t == "MTEXT":
            ins = e.dxf.insert
            return "text", {"position": (ins.x, ins.y), "text": e.plain_text() or "", "height": float(e.dxf.char_height or 0)}
        if t == "HATCH":
            rings: list[list[tuple[float, float]]] = []
            try:
                for p in ezpath.from_hatch(e):
                    pts = [(v.x, v.y) for v in p.flattening(dist)]
                    if len(pts) >= 3:
                        rings.append(pts)
            except Exception:
                pass
            if not rings:
                return None
            outer = max(rings, key=lambda r: abs(_ring_area(r)))
            return "polyline", {"points": outer, "closed": True, "rings": rings}
        if t == "DIMENSION":
            return None  # annotation only; geometry preserved in source file for export
        if t in ("SOLID", "3DFACE", "TRACE"):
            pts = [(e.dxf.vtx0.x, e.dxf.vtx0.y), (e.dxf.vtx1.x, e.dxf.vtx1.y),
                   (e.dxf.vtx2.x, e.dxf.vtx2.y)]
            if e.dxf.hasattr("vtx3"):
                pts.append((e.dxf.vtx3.x, e.dxf.vtx3.y))
            return "polyline", {"points": pts, "closed": True}
    except Exception:
        return None
    return None


def _ring_area(pts: list[tuple[float, float]]) -> float:
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return a / 2


def _iter_entities(msp) -> Iterator:
    for e in msp:
        yield e


def parse_dxf(path: Path, document_id: str, file_name: str, source_format: str = "DXF",
              conversion_log: list[str] | None = None) -> CadDocument:
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()

    header = doc.header
    insunits = int(header.get("$INSUNITS", 0) or 0)
    cad_version = doc.dxfversion

    # First pass to compute raw extents (header EXTMIN/EXTMAX is often stale).
    raw_entities: list[tuple] = []
    n_source = 0
    for e in _iter_entities(msp):
        n_source += 1
        t = e.dxftype()
        if t == "INSERT":
            block_name = e.dxf.name
            ins = e.dxf.insert
            raw_entities.append((e, "insert_point", {"position": (ins.x, ins.y)}, block_name))
            count = 0
            try:
                for ve in e.virtual_entities():
                    if count >= _MAX_INSERT_VIRTUAL:
                        break
                    kg = _entity_points(ve, _FLATTEN_DIST)
                    if kg:
                        layer = ve.dxf.layer if ve.dxf.layer not in ("0",) else e.dxf.layer
                        raw_entities.append(((e, ve, layer), "virtual", kg, block_name))
                        count += 1
            except Exception:
                pass
            continue
        kg = _entity_points(e, _FLATTEN_DIST)
        if kg:
            raw_entities.append((e, "direct", kg, None))

    minx = miny = math.inf
    maxx = maxy = -math.inf

    def _feed(pts):
        nonlocal minx, miny, maxx, maxy
        for x, y in pts:
            if x < minx: minx = x
            if y < miny: miny = y
            if x > maxx: maxx = x
            if y > maxy: maxy = y

    for _, mode, (kind, g), _bn in [(r[0], r[1], r[2], r[3]) for r in raw_entities]:
        if kind == "polyline":
            _feed(g["points"])
        elif kind in ("point", "text"):
            _feed([g["position"]])
        elif mode == "insert_point":
            _feed([g["position"]])

    if not raw_entities or not math.isfinite(minx):
        minx = miny = 0.0
        maxx = maxy = 1.0

    span = max(maxx - minx, maxy - miny)
    units, scale, note = _guess_units(insunits, span)
    log = list(conversion_log or [])
    if note:
        log.append(f"units: {note}")

    def s(pt):
        return (round(pt[0] * scale, 6), round(pt[1] * scale, 6))

    entities: list[IREntity] = []
    layer_counts: dict[str, int] = {}
    idx = 0
    for ref, mode, (kind, g), block_name in raw_entities:
        if mode == "virtual":
            e, ve, layer = ref
            handle = getattr(e.dxf, "handle", None)
            etype = ve.dxftype()
        else:
            e = ref
            layer = e.dxf.layer
            handle = getattr(e.dxf, "handle", None)
            etype = e.dxftype() if mode == "direct" else "INSERT"

        geom: dict = {}
        if kind == "polyline":
            pts = [s(p) for p in g["points"]]
            geom = {"points": pts, "closed": bool(g.get("closed"))}
            if "radius" in g:
                geom["center"] = s(g["center"])
                geom["radius"] = g["radius"] * scale
            if "rings" in g:
                geom["rings"] = [[s(p) for p in ring] for ring in g["rings"]]
        elif kind == "point":
            geom = {"position": s(g["position"])}
        elif kind == "text":
            geom = {"position": s(g["position"]), "text": g.get("text", ""),
                    "height": g.get("height", 0) * scale}
        else:  # insert point
            geom = {"position": s(g["position"])}
            kind = "point"

        idx += 1
        entities.append(IREntity(
            id=f"e{idx}",
            sourceHandle=str(handle) if handle else None,
            type=etype,
            layer=layer,
            kind="polygon" if (kind == "polyline" and geom.get("closed")) else kind,
            geometry=geom,
            blockName=block_name,
        ))
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

    layers = []
    doc_layers = {l.dxf.name: l for l in doc.layers}
    for name, count in sorted(layer_counts.items()):
        aci = 7
        if name in doc_layers:
            try:
                aci = doc_layers[name].color
            except Exception:
                aci = 7
        layers.append(LayerInfo(name=name, entityCount=count, color=_aci_hex(aci)))

    ext = Extents(min=(minx * scale, miny * scale), max=(maxx * scale, maxy * scale))
    return CadDocument(
        documentId=document_id,
        fileName=file_name,
        format=source_format,  # original upload format
        cadVersion=cad_version,
        units=units,
        unitsScaleToMeters=scale,
        extents=ext,
        layers=layers,
        entities=entities,
        entityCount=n_source,
        conversionLog=log,
    )
