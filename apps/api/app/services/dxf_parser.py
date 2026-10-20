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

from .source_reconstruction import source_attributes
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
_MAX_INSERT_DEPTH = 32
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


def _entity_color(entity, doc, layer):
    if entity.dxf.hasattr("true_color"):
        return f"#{entity.dxf.true_color:06x}"
    aci = int(entity.dxf.get("color", 256))
    if aci in (0, 256):
        entry = doc.layers.get(layer) if layer in doc.layers else None
        if entry is not None and entry.dxf.hasattr("true_color"):
            return f"#{entry.dxf.true_color:06x}"
        aci = abs(entry.color) if entry is not None else 7
    return _aci_hex(aci)


MIN_SITE_SPAN_M = 5.0  # a site plan narrower than this means a wrong $INSUNITS


def _core_span(points: list[tuple[float, float]]) -> float:
    """Span of the bulk of the drawing (2nd–98th percentile of entity
    anchors): a stray object at the origin (236287.dxf) does not count."""
    if len(points) < 2:
        return 0.0
    import numpy as np
    lo, hi = np.percentile(np.asarray(points, dtype=float), [2, 98], axis=0)
    return float(max(hi - lo))


def _guess_units(code: int, span: float, core_span: float | None = None) -> tuple[str, float, str]:
    """Return (unit label, scale_to_meters, note)."""
    name, scale = _INSUNITS.get(code, ("unknown", None))
    if scale is not None:
        # metric templates default to mm while the plan is drawn in meters
        # (236285.dxf: $INSUNITS=mm, a 580-unit plan — not a 0.6 m site)
        core = span if core_span is None else core_span
        if core * scale < MIN_SITE_SPAN_M <= core <= 20000:
            return ("m (header says " + name + ")", 1.0,
                    f"$INSUNITS={code} ({name}) would make the site {core * scale:.2f} m across; "
                    f"span {core:.0f} → assumed meters")
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
            return None  # precise annotation is available in the source CAD SVG preview
        if t in ("SOLID", "3DFACE", "TRACE"):
            pts = [(e.dxf.vtx0.x, e.dxf.vtx0.y), (e.dxf.vtx1.x, e.dxf.vtx1.y),
                   (e.dxf.vtx2.x, e.dxf.vtx2.y)]
            if e.dxf.hasattr("vtx3"):
                pts.append((e.dxf.vtx3.x, e.dxf.vtx3.y))
            return "polyline", {"points": pts, "closed": True}
    except Exception:
        return None
    return None


def clean_text(s: str | None) -> str | None:
    """Undecodable bytes reach us as lone surrogates (LibreDWG sometimes
    writes cp1251 where DXF R2007+ requires UTF-8). Re-decode them — cp1251
    first — so the IR can always be serialized to JSON."""
    if not s or not any("\udc80" <= ch <= "\udcff" for ch in s):
        return s
    # only the undecodable runs are re-decoded: "<xref>$0$<layer>" may mix a
    # proper UTF-8 prefix with a cp1251 layer name
    out: list[str] = []
    run = bytearray()

    def flush():
        if run:
            for enc in ("utf-8", "cp1251"):
                try:
                    out.append(run.decode(enc))
                    break
                except UnicodeDecodeError:
                    continue
            else:
                out.append(run.decode("utf-8", "replace"))
            run.clear()

    for ch in s:
        o = ord(ch)
        if 0xDC80 <= o <= 0xDCFF:
            run.append(o - 0xDC00)
            continue
        flush()
        out.append(ch)
    flush()
    return "".join(out)


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


def _sanitize_dxf(src: Path, dst: Path) -> int:
    """Repair DXF files whose string values contain raw newlines (seen in
    LibreDWG output for some MTEXT/STYLE data). DXF is strictly alternating
    code/value lines; a non-integer line in code position is a continuation
    of the previous value — re-join it. Returns number of joined lines."""
    data = src.read_bytes()
    lines = data.split(b"\n")
    out: list[bytes] = []
    expect_code = True
    fixes = 0
    for ln in lines:
        s = ln.strip()
        if expect_code:
            if not s:
                continue  # stray blank line in code position (e.g. trailing newline)
            try:
                int(s)
            except ValueError:
                if out:
                    out[-1] = out[-1].rstrip(b"\r") + b" " + ln
                    fixes += 1
                    continue
            out.append(ln)
            expect_code = False
        else:
            out.append(ln)
            expect_code = True
    dst.write_bytes(b"\n".join(out))
    return fixes


_SYMBOL_TABLE_TYPES = {"LAYER", "LTYPE", "STYLE", "VIEW", "UCS", "VPORT", "APPID",
                       "DIMSTYLE", "BLOCK_RECORD"}


def _repair_tags(src: Path, dst: Path) -> dict[str, int]:
    """Tag-level repair of LibreDWG output that even ezdxf's recover mode
    cannot load:
      * symbol-table records without a name (group code 2) — a nameless
        STYLE is a shape-file entry and gets "", others "UNNAMED_<handle>";
      * LAYOUT objects (paper-space sheets) whose BLOCK_RECORD does not exist
        are dropped — only model space matters here;
      * MTEXT column type outside 0..2 → 0 (static): group code 71 of the
        embedded object ("101 / Embedded Object"; at the top level 71 is the
        attachment point) and the legacy code 75."""
    counts = {"nameless table records": 0, "invalid MTEXT column types": 0,
              "layouts without block record": 0}
    block_records: set[str] = set()
    with src.open("r", encoding="utf-8", errors="surrogateescape", newline="") as fin,             dst.open("w", encoding="utf-8", errors="surrogateescape", newline="") as fout:
        lines = iter(fin)
        section = entity = None
        embedded = False  # inside the "Embedded Object" part of an MTEXT
        record: list[tuple[str, str]] | None = None  # buffered table record

        def flush_record():
            nonlocal record
            if record is None:
                return
            codes = [c.strip() for c, _ in record]
            if record[0][1].strip() == "LAYOUT":
                # the block record is the 330 pointer of the AcDbLayout subclass
                # (the first 330 is the owner dictionary)
                br, in_layout = None, False
                for c, v in record:
                    if c.strip() == "100":
                        in_layout = v.strip() == "AcDbLayout"
                    elif in_layout and c.strip() == "330":
                        br = v.strip().upper()
                if br is not None and br not in block_records:
                    counts["layouts without block record"] += 1
                    record = None
                    return
                for c, v in record:
                    fout.write(c)
                    fout.write(v)
                record = None
                return
            if "2" not in codes:
                handle = next((v.strip() for c, v in record if c.strip() == "5"), "0")
                name = "" if record[0][1].strip() == "STYLE" else f"UNNAMED_{handle}"
                at = codes.index("70") if "70" in codes else len(record)
                record.insert(at, ("  2\n", name + "\n"))
                counts["nameless table records"] += 1
            for c, v in record:
                fout.write(c)
                fout.write(v)
            record = None

        for code_line in lines:
            value_line = next(lines, "")
            code, value = code_line.strip(), value_line.strip()
            if code == "0":
                flush_record()
                entity = value
                embedded = False
                if section == "TABLES" and value in _SYMBOL_TABLE_TYPES:
                    record = [(code_line, value_line)]
                    continue
                if section == "OBJECTS" and value == "LAYOUT":
                    record = [(code_line, value_line)]
                    continue
            elif code == "2" and entity == "SECTION":
                section = value
            if record is not None:
                if code == "5" and record[0][1].strip() == "BLOCK_RECORD":
                    block_records.add(value.upper())
                record.append((code_line, value_line))
                continue
            if entity == "MTEXT" and code == "101":
                embedded = True
            if entity == "MTEXT" and (code == "75" or (embedded and code == "71")):
                try:
                    if int(value) not in (0, 1, 2):
                        value_line = "0" + ("\r\n" if value_line.endswith("\r\n") else "\n")
                        counts["invalid MTEXT column types"] += 1
                except ValueError:
                    pass
            fout.write(code_line)
            fout.write(value_line)
        flush_record()
    return counts


def _read_dxf(path: Path, log: list[str]):
    """Strict read first; LibreDWG output that breaks it is retried: stray
    line breaks are sanitized, other defects go through ezdxf's recover mode
    and, if even that fails, a tag-level repair (see _repair_tags)."""
    from ezdxf import recover
    from ezdxf.lldxf.const import DXFStructureError

    def last_resort(p: Path):
        fixed = p.with_name(p.stem + ".tagfix.dxf")
        counts = _repair_tags(p, fixed)
        log.append("DXF repaired at tag level: "
                   + ", ".join(f"{k}: {v}" for k, v in counts.items() if v))
        try:
            return ezdxf.readfile(str(fixed))
        except Exception:  # noqa: BLE001
            doc, _ = recover.readfile(str(fixed))
            return doc

    try:
        return ezdxf.readfile(str(path))
    except DXFStructureError as first_err:
        fixed = path.with_suffix(".repaired.dxf")
        fixes = _sanitize_dxf(path, fixed)
        log.append(f"DXF structure error ({str(first_err)[:120]}); "
                   f"sanitized {fixes} malformed line(s) and retried")
        try:
            return ezdxf.readfile(str(fixed))
        except Exception:  # noqa: BLE001
            try:
                doc, auditor = recover.readfile(str(fixed))
                log.append(f"loaded via ezdxf.recover ({len(auditor.errors)} issues fixed)")
                return doc
            except Exception:  # noqa: BLE001
                return last_resort(fixed)
    except Exception as first_err:  # noqa: BLE001 — not a structure error
        log.append(f"strict DXF read failed ({type(first_err).__name__}: {str(first_err)[:100]})")
        try:
            doc, _ = recover.readfile(str(path))
            log.append("loaded via ezdxf.recover")
            return doc
        except Exception:  # noqa: BLE001
            return last_resort(path)


def parse_dxf_cached(path: Path, document_id: str, file_name: str, source_format: str = "DXF",
                     conversion_log: list[str] | None = None) -> CadDocument:
    """parse_dxf through the ingest cache (app/services/cache.py): the same
    DXF parsed by the same code comes back without re-reading it."""
    import json
    import sys
    from . import cache
    from ..models import ir
    from . import source_reconstruction
    prior = list(conversion_log or [])
    key = None
    if cache.enabled():
        key = cache.make_key("parse", cache.file_sha256(path),
                             cache.code_fingerprint(sys.modules[__name__], ir,source_reconstruction))
        hit = cache.get(key)
        if hit is not None:
            data = json.loads((hit / "document.json").read_text(encoding="utf-8"))
            data.update(documentId=document_id, fileName=file_name, format=source_format,
                        conversionLog=prior + data["conversionLog"]
                        + ["parse: taken from the ingest cache (same file, same code)"])
            return CadDocument.model_validate(data)
    doc = parse_dxf(path, document_id, file_name, source_format, prior)
    if key is not None:
        own_log = doc.conversionLog[len(prior):]
        cache.put(key, lambda d: (d / "document.json").write_text(
            json.dumps({**doc.model_dump(), "conversionLog": own_log}, ensure_ascii=False), encoding="utf-8"))
    return doc


def parse_dxf(path: Path, document_id: str, file_name: str, source_format: str = "DXF",
              conversion_log: list[str] | None = None) -> CadDocument:
    _log = list(conversion_log or [])
    conversion_log = _log
    doc = _read_dxf(path, conversion_log)
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
            raw_entities.append((e, "insert_point", ("point", {"position": (ins.x, ins.y)}), block_name))
            # Expand nested blocks; preserve inherited layer 0 and BYBLOCK color.
            def expand(block, parent_layer, parent_color, depth=0):
                if depth >= _MAX_INSERT_DEPTH:
                    conversion_log.append(f"block recursion limit: {block.dxf.name}")
                    return
                for ve in block.virtual_entities():
                    layer = ve.dxf.layer if ve.dxf.layer != "0" else parent_layer
                    color = int(ve.dxf.get("color", 256))
                    if color == 0:
                        ve.dxf.color = parent_color
                        if block.dxf.hasattr("true_color"):
                            ve.dxf.true_color = block.dxf.true_color
                    if ve.dxftype() == "INSERT":
                        yield from expand(ve, layer, int(ve.dxf.get("color", 256)), depth + 1)
                    else:
                        kg = _entity_points(ve, _FLATTEN_DIST)
                        if kg:
                            yield ((e, ve, layer), "virtual", kg, block_name)
                for attr in getattr(block, "attribs", []):
                    kg = _entity_points(attr, _FLATTEN_DIST)
                    if kg:
                        yield ((e, attr, parent_layer), "virtual", kg, block_name)
            try:
                raw_entities.extend(expand(e, e.dxf.layer, int(e.dxf.get("color", 256))))
            except Exception as exc:
                conversion_log.append(f"block {block_name}: {exc}")
            continue
        if t in {"DIMENSION", "LEADER", "MLEADER"} and hasattr(e, "virtual_entities"):
            try:
                for ve in e.virtual_entities():
                    kg = _entity_points(ve, _FLATTEN_DIST)
                    if kg:
                        layer = ve.dxf.layer if ve.dxf.layer != "0" else e.dxf.layer
                        raw_entities.append(((e, ve, layer), "virtual", kg, None))
            except Exception as exc:
                conversion_log.append(f"{t}: {exc}")
            continue
        kg = _entity_points(e, _FLATTEN_DIST)
        if kg:
            raw_entities.append((e, "direct", kg, None))
        else:
            conversion_log.append(f"Unsupported preview entity: {t} handle={e.dxf.get('handle', '')}; retained in source DXF")

    minx = miny = math.inf
    maxx = maxy = -math.inf
    anchors: list[tuple[float, float]] = []  # one point per entity

    def _feed(pts):
        nonlocal minx, miny, maxx, maxy
        for x, y in pts:
            if x < minx: minx = x
            if y < miny: miny = y
            if x > maxx: maxx = x
            if y > maxy: maxy = y
        if pts:
            anchors.append(tuple(pts[0]))

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
    units, scale, note = _guess_units(insunits, span, _core_span(anchors))
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
            geom = {"position": s(g["position"]), "text": clean_text(g.get("text", "")),
                    "height": g.get("height", 0) * scale}
        else:  # insert point
            geom = {"position": s(g["position"])}
            kind = "point"

        idx += 1
        entities.append(IREntity(
            id=f"e{idx}",
            sourceHandle=str(handle) if handle else None,
            type=etype,
            layer=clean_text(layer),
            kind="polygon" if (kind == "polyline" and geom.get("closed")) else kind,
            geometry=geom,
            blockName=clean_text(block_name),
            attributes={"color": _entity_color(ve if mode == "virtual" else e, doc, layer),
                        **source_attributes(ve if mode == "virtual" else e,scale)},
        ))
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

    layers = []
    doc_layers = {l.dxf.name: l for l in doc.layers}
    for name in sorted(set(layer_counts) | set(doc_layers)):
        count = layer_counts.get(name, 0)
        aci = 7
        if name in doc_layers:
            try:
                aci = doc_layers[name].color
            except Exception:
                aci = 7
        layers.append(LayerInfo(name=clean_text(name), entityCount=count, color=_aci_hex(aci)))

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
