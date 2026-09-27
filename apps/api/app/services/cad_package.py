"""CAD package ingestion: an AutoCAD eTransmit package (zip) → one merged DXF.

Moscow ИРД deliveries come as a base general-plan DWG that holds almost no
geometry of its own and pulls everything else in as external references
(xrefs): `tp` topographic plan, `up` / `pp` existing / projected utilities,
`kl` red lines, `brd` survey frames, plus project references (work boundary,
curbs, cover fills). Uploading the base drawing alone loses all of that.

This module:
  1. extracts the zip safely (zip-slip, size and member limits, cp866 names),
  2. finds the base drawing (eTransmit report, else the drawing with xrefs),
  3. reads its xref table and resolves every path inside the package,
  4. converts referenced DWGs and merges all model spaces into one DXF,
     renaming xref layers the way AutoCAD BIND does: "<xref>$0$<layer>",
  5. clips everything to the work boundary + margin (the full topographic plan
     is ~200k entities, the site needs a fraction of them).

Source-aware layer classification (the work boundary is THE site boundary,
survey frames and red lines are annotations, anything in a utilities export
is a utility) is applied after parsing by `apply_source_classification`.
"""
from __future__ import annotations

import logging
import re
import shutil
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

import ezdxf
from ezdxf import bbox as ezbbox
from ezdxf.addons import Importer
from ezdxf.math import Matrix44
from shapely import box, intersects
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .dwg_converter import DwgConversionError, DwgConverter

log = logging.getLogger("greenplan.package")

BIND_SEP = "$0$"                      # AutoCAD BIND layer naming: <xref>$0$<layer>
DRAWING_EXT = {".dwg", ".dxf"}
MAX_MEMBERS = 5000
MAX_UNPACKED_BYTES = 3 * 1024 ** 3
CLIP_MARGIN_M = 15.0                  # > the largest setback in the registry (5 m)

# kind of an xref by its file / block name
_IRD_SUFFIX = re.compile(r"(tp|up|pp|kl|brd)$", re.IGNORECASE)
_KIND_PATTERNS = [
    (re.compile(r"границ[а-я]*\s*работ", re.IGNORECASE), "work_boundary"),
    (re.compile(r"борт", re.IGNORECASE), "curbs"),
    (re.compile(r"заливк", re.IGNORECASE), "covers"),
]
KIND_TITLES = {
    "base": "основной чертёж", "tp": "топоплан", "up": "существующие сети",
    "pp": "проектируемые сети", "kl": "красные линии", "brd": "рамки и граница съёмки",
    "work_boundary": "границы работ", "curbs": "бортовой камень", "covers": "покрытия (заливки)",
    "other": "прочая ссылка",
}


class PackageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class PackageSource:
    name: str                 # xref block name, or the base file stem
    kind: str
    file: str = ""            # path inside the package ("" when not found)
    prefix: str = ""          # layer prefix in the merged DXF ("" for the base)
    status: str = "merged"    # merged | missing | failed | empty | nested_skipped
    entities_in: int = 0
    entities_kept: int = 0
    detail: str = ""

    def to_api(self) -> dict:
        return {"name": self.name, "kind": self.kind, "title": KIND_TITLES.get(self.kind, self.kind),
                "file": self.file, "prefix": self.prefix, "status": self.status,
                "entitiesIn": self.entities_in, "entitiesKept": self.entities_kept,
                "detail": self.detail}


@dataclass
class PackageResult:
    merged_dxf: Path
    base_file: str
    sources: list[PackageSource]
    clip: dict = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def to_api(self) -> dict:
        return {"baseFile": self.base_file, "clip": self.clip,
                "sources": [s.to_api() for s in self.sources]}


def source_kind(name: str) -> str:
    stem = Path(name).stem if "." in name else name
    m = _IRD_SUFFIX.search(stem)
    if m:
        return m.group(1).lower()
    for rx, kind in _KIND_PATTERNS:
        if rx.search(stem):
            return kind
    return "other"


# --------------------------------------------------------------------------- zip

def _member_name(info: zipfile.ZipInfo) -> str:
    """Windows Explorer zips store Cyrillic names in cp866 without the UTF-8
    flag; Python then decodes them as cp437."""
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            name = name.encode("cp437").decode("cp866")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def safe_extract(zip_path: Path, dest: Path) -> list[Path]:
    """Extract drawings and eTransmit reports; refuse traversal and bombs."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise PackageError("INVALID_FILE", f"Not a valid ZIP archive: {exc}") from exc
    out: list[Path] = []
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise PackageError("PACKAGE_TOO_LARGE", f"ZIP has {len(infos)} members (limit {MAX_MEMBERS}).")
        total = sum(i.file_size for i in infos)
        if total > MAX_UNPACKED_BYTES:
            raise PackageError("PACKAGE_TOO_LARGE", "ZIP unpacks to more than 3 GB.")
        root = dest.resolve()
        for info in infos:
            if info.is_dir():
                continue
            name = _member_name(info).replace("\\", "/")
            parts = PurePosixPath(name).parts
            if (name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in parts):
                raise PackageError("INVALID_FILE", f"Unsafe path in ZIP: {name!r}")
            if any(p in ("PaxHeader", "__MACOSX") for p in parts):
                continue
            if PurePosixPath(name).suffix.lower() not in DRAWING_EXT | {".txt"}:
                continue
            target = (root / name).resolve()
            if root not in target.parents:
                raise PackageError("INVALID_FILE", f"Unsafe path in ZIP: {name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            out.append(target)
    return out


# --------------------------------------------------------------------------- base

def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-16", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1251", errors="replace")


def _base_from_report(reports: list[Path], drawings: list[Path]) -> Path | None:
    """eTransmit report: 'Базовый чертеж:' (ru) / 'Root Drawing:' (en)."""
    by_name = {d.name.lower(): d for d in drawings}
    for rep in reports:
        text = _read_text(rep)
        m = re.search(r"(Базовый чертеж|Root Drawing)\s*:\s*\n\s*(.+?)\s*\n", text)
        if m and m.group(2).strip().lower() in by_name:
            return by_name[m.group(2).strip().lower()]
    return None


def _rel_depth(p: Path, root: Path) -> int:
    return len(p.relative_to(root).parts)


# --------------------------------------------------------------------------- xrefs

@dataclass
class XrefRef:
    name: str
    path: str
    base_point: tuple
    inserts: list


def read_xrefs(doc) -> list[XrefRef]:
    names = {}
    for blk in doc.blocks:
        b = blk.block
        if b is not None and (b.is_xref or b.is_xref_overlay):
            names[blk.name] = XrefRef(blk.name, b.dxf.get("xref_path", ""),
                                      tuple(b.dxf.get("base_point", (0, 0, 0))), [])
    for e in doc.modelspace().query("INSERT"):
        ref = names.get(e.dxf.name)
        if ref is not None:
            ref.inserts.append((tuple(e.dxf.insert), e.dxf.get("xscale", 1.0),
                                e.dxf.get("yscale", 1.0), e.dxf.get("rotation", 0.0)))
    return list(names.values())


def resolve_xref(path_str: str, base_dir: Path, root: Path, index: dict[str, list[Path]]) -> Path | None:
    """xref paths are Windows-style and relative to the base drawing
    (".\\3ДЖКХ-25_03117\\output…up.dwg"); case differs between copies."""
    parts = [p for p in PureWindowsPath(path_str).parts if p not in (".", "\\")]
    if parts and re.match(r"^[A-Za-z]:\\?$", parts[0]):
        parts = parts[1:]
    candidates = index.get(parts[-1].lower(), []) if parts else []
    if not candidates:
        return None
    want = [p.lower() for p in parts]

    def score(c: Path) -> tuple:
        rel = [p.lower() for p in c.relative_to(root).parts]
        common = 0
        for a, b in zip(reversed(rel), reversed(want)):
            if a != b:
                break
            common += 1
        return (common, -abs(len(c.parents) - len(base_dir.parents)))
    return max(candidates, key=score)


# --------------------------------------------------------------------------- merge

def _load(path: Path, log_: list[str]):
    from .dxf_parser import _read_dxf
    return _read_dxf(path, log_)


def _convert(src: Path, workdir: Path, converter: DwgConverter) -> Path:
    if src.suffix.lower() == ".dxf":
        return src
    out = workdir / f"{uuid.uuid4().hex}.dxf"
    return converter.convert_to_dxf(src, out)


def _insert_matrix(ref: XrefRef, unit_factor: float) -> Matrix44 | None:
    if not ref.inserts:
        return None
    (ix, iy, iz), sx, sy, rot = ref.inserts[0]
    bx, by, bz = (ref.base_point + (0, 0, 0))[:3]
    identity = (abs(ix) + abs(iy) + abs(bx) + abs(by) < 1e-9 and abs(sx - 1) < 1e-12
                and abs(sy - 1) < 1e-12 and abs(rot) < 1e-12 and abs(unit_factor - 1) < 1e-12)
    if identity:
        return None
    import math
    return Matrix44.chain(
        Matrix44.translate(-bx, -by, -bz),
        Matrix44.scale(sx * unit_factor, sy * unit_factor, unit_factor),
        Matrix44.z_rotate(math.radians(rot)),
        Matrix44.translate(ix, iy, iz),
    )


def _work_boundary(doc) -> Polygon | None:
    """Closed polylines plus open pieces stitched end to end (the Moscow work
    boundary is often drawn as two open halves)."""
    from .geometry import stitch_rings
    polys, lines = [], []
    for e in doc.modelspace().query("LWPOLYLINE POLYLINE"):
        pts = [(p[0], p[1]) for p in (e.get_points("xy") if e.dxftype() == "LWPOLYLINE"
                                       else [v.dxf.location for v in e.vertices])]
        if len(pts) < 2:
            continue
        if e.closed and len(pts) >= 3:
            poly = Polygon(pts)
            poly = poly if poly.is_valid else poly.buffer(0)
            if poly.area > 1.0:
                polys.append(poly)
        else:
            lines.append(LineString(pts))
    polys.extend(stitch_rings(lines))
    return unary_union(polys) if polys else None


def _entity_box(e) -> tuple[float, float, float, float] | None:
    """Cheap 2D bounding box. Text and block references count by their
    insertion point (no font metrics, no block explosion — LibreDWG output
    can reference anonymous blocks it did not convert)."""
    t = e.dxftype()
    try:
        if t == "LINE":
            pts = [e.dxf.start, e.dxf.end]
        elif t == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
        elif t == "POLYLINE":
            pts = [v.dxf.location for v in e.vertices]
        elif t in ("CIRCLE", "ARC"):
            c, r = e.dxf.center, e.dxf.radius
            return (c[0] - r, c[1] - r, c[0] + r, c[1] + r)
        elif t == "ELLIPSE":
            c, r = e.dxf.center, e.dxf.major_axis.magnitude
            return (c[0] - r, c[1] - r, c[0] + r, c[1] + r)
        elif t in ("TEXT", "MTEXT", "INSERT", "ATTRIB"):
            pts = [e.dxf.insert]
        elif t == "POINT":
            pts = [e.dxf.location]
        elif t == "DIMENSION":
            pts = [e.dxf.defpoint]
        elif t in ("SOLID", "3DFACE", "TRACE"):
            pts = [e.dxf.get(k) for k in ("vtx0", "vtx1", "vtx2", "vtx3") if e.dxf.hasattr(k)]
        elif t == "SPLINE":
            pts = list(e.control_points) or list(e.fit_points)
        else:
            bb = ezbbox.extents([e], fast=True)
            if not bb.has_data:
                return None
            return (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y)
    except Exception:  # noqa: BLE001
        return None
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _select(msp, region) -> tuple[list, int]:
    """Entities whose bounding box touches the clip region (all when None).
    Entities without a computable box are kept (safe direction)."""
    ents = list(msp)
    if region is None:
        return ents, len(ents)
    keep, boxes, idx = [], [], []
    for i, e in enumerate(ents):
        bb = _entity_box(e)
        if bb is None:
            keep.append(e)
            continue
        boxes.append(box(*bb))
        idx.append(i)
    if boxes:
        hit = intersects(region, boxes)
        keep.extend(ents[i] for i, h in zip(idx, hit) if h)
    return keep, len(ents)


def _prefix_layers(doc, entities: list, prefix: str) -> None:
    """Move entities to "<prefix>$0$<layer>" copies of their layers."""
    if not prefix:
        return
    made: dict[str, str] = {}
    for e in entities:
        old = e.dxf.layer
        new = made.get(old)
        if new is None:
            new = f"{prefix}{BIND_SEP}{old}"
            if new not in doc.layers:
                src = doc.layers.get(old) if old in doc.layers else None
                attribs = {}
                if src is not None:
                    for key in ("color", "linetype", "lineweight", "true_color"):
                        if src.dxf.hasattr(key):
                            attribs[key] = src.dxf.get(key)
                doc.layers.add(new, dxfattribs=attribs)
            made[old] = new
        e.dxf.layer = new


def ingest_package(zip_path: Path, workdir: Path, converter: DwgConverter | None = None,
                   clip_margin_m: float = CLIP_MARGIN_M) -> PackageResult:
    workdir.mkdir(parents=True, exist_ok=True)
    unpack = workdir / "unpacked"
    files = safe_extract(zip_path, unpack)
    drawings = [f for f in files if f.suffix.lower() in DRAWING_EXT]
    reports = [f for f in files if f.suffix.lower() == ".txt"]
    if not drawings:
        raise PackageError("PACKAGE_NO_DRAWINGS", "ZIP contains no DWG/DXF drawings.")
    msgs: list[str] = [f"package: {len(drawings)} drawing(s) unpacked"]
    converter = converter or DwgConverter()
    conv_dir = workdir / "converted"
    conv_dir.mkdir(exist_ok=True)

    index: dict[str, list[Path]] = {}
    for d in drawings:
        index.setdefault(d.name.lower(), []).append(d)

    # --- base drawing -------------------------------------------------------
    base = _base_from_report(reports, drawings)
    if base is not None:
        msgs.append(f"base drawing from eTransmit report: {base.name}")
    base_doc = None
    base_xrefs: list[XrefRef] = []
    if base is None:
        shallow = min(_rel_depth(d, unpack) for d in drawings)
        cands = [d for d in drawings if _rel_depth(d, unpack) == shallow]
        best = None
        for c in cands:
            try:
                cdoc = _load(_convert(c, conv_dir, converter), msgs)
            except Exception as exc:  # noqa: BLE001
                msgs.append(f"skip base candidate {c.name}: {exc}")
                continue
            xr = read_xrefs(cdoc)
            if best is None or len(xr) > len(best[2]):
                best = (c, cdoc, xr)
        if best is None:
            raise PackageError("PACKAGE_NO_BASE", "Could not read any top-level drawing.")
        base, base_doc, base_xrefs = best
        msgs.append(f"base drawing (most xrefs at top level): {base.name}")
    if base_doc is None:
        base_doc = _load(_convert(base, conv_dir, converter), msgs)
        base_xrefs = read_xrefs(base_doc)
    base_units = int(base_doc.header.get("$INSUNITS", 0) or 0)

    sources = [PackageSource(name=base.stem, kind="base", file=str(base.relative_to(unpack)))]
    plan: list[tuple[PackageSource, Path, XrefRef]] = []
    for ref in base_xrefs:
        kind = source_kind(ref.name)
        src = PackageSource(name=ref.name, kind=kind, prefix=_safe_prefix(ref.name))
        path = resolve_xref(ref.path or ref.name + ".dwg", base.parent, unpack, index)
        if path is None:
            src.status, src.detail = "missing", f"not in package: {ref.path}"
        elif not ref.inserts:
            src.status, src.detail = "empty", "defined but not inserted in model space"
        else:
            src.file = str(path.relative_to(unpack))
            plan.append((src, path, ref))
        sources.append(src)

    # --- convert referenced drawings in parallel ------------------------------
    def conv(item):
        src, path, ref = item
        try:
            return item, _convert(path, conv_dir, converter), None
        except (DwgConversionError, Exception) as exc:  # noqa: BLE001
            return item, None, exc
    with ThreadPoolExecutor(max_workers=3) as pool:
        converted = list(pool.map(conv, plan))

    # --- clip region from the work boundary ----------------------------------
    region = None
    loaded: dict[str, object] = {}
    clip = {"mode": "none"}
    for (src, _path, _ref), dxf, err in converted:
        if src.kind == "work_boundary" and dxf is not None:
            wdoc = _load(dxf, msgs)
            loaded[src.name] = wdoc
            wb = _work_boundary(wdoc)
            if wb is not None and not wb.is_empty:
                region = wb.buffer(clip_margin_m)
                clip = {"mode": "work_boundary", "source": src.name, "marginM": clip_margin_m,
                        "boundaryAreaM2": round(wb.area, 1)}
                break

    # --- merge ---------------------------------------------------------------
    target = ezdxf.new("R2018", setup=True)
    if base_units:
        target.header["$INSUNITS"] = base_units

    def merge(doc, src: PackageSource, matrix: Matrix44 | None):
        msp = doc.modelspace()
        dropped = 0
        for e in list(msp.query("INSERT")):
            blk = doc.blocks.get(e.dxf.name)
            if blk is None:                    # block not converted (e.g. anonymous *U)
                msp.delete_entity(e)
                dropped += 1
            elif blk.block is not None and (blk.block.is_xref or blk.block.is_xref_overlay):
                msp.delete_entity(e)           # xref placeholders never merge as blocks
        if dropped:
            msgs.append(f"{src.name}: {dropped} block reference(s) without a block definition dropped")
        if matrix is not None:
            for e in msp:
                try:
                    e.transform(matrix)
                except Exception:  # noqa: BLE001 — non-uniform scale of arcs etc.
                    pass
        selected, total = _select(msp, region)
        src.entities_in, src.entities_kept = total, len(selected)
        _prefix_layers(doc, selected, src.prefix)
        imp = Importer(doc, target)
        imp.import_entities(selected, target.modelspace())
        imp.finalize()

    import time
    t0 = time.time()
    merge(base_doc, sources[0], None)
    for (src, path, ref), dxf, err in converted:
        if err is not None or dxf is None:
            src.status, src.detail = "failed", f"conversion failed: {err}"
            continue
        try:
            doc = loaded.get(src.name) or _load(dxf, msgs)
            nested = read_xrefs(doc)
            if nested:
                msgs.append(f"{src.name}: {len(nested)} nested xref(s) not merged")
            src_units = int(doc.header.get("$INSUNITS", 0) or 0)
            factor = 1.0
            if base_units and src_units and src_units != base_units:
                factor = ezdxf.units.conversion_factor(src_units, base_units)
            merge(doc, src, _insert_matrix(ref, factor))
            if src.entities_in == 0:
                src.status = "empty"
        except Exception as exc:  # noqa: BLE001
            src.status, src.detail = "failed", f"merge failed: {exc}"
        loaded.pop(src.name, None)

    out = workdir / "merged.dxf"
    target.saveas(str(out))
    msgs.append(f"package: merge took {time.time() - t0:.0f} s")
    merged = [s for s in sources if s.status == "merged"]
    msgs.append(f"package: merged {len(merged)} of {len(sources)} source(s) into one DXF"
                + (f", clipped to work boundary + {clip_margin_m:g} m" if region is not None else
                   " (no work boundary found — nothing clipped)"))
    for s in sources:
        if s.status != "merged":
            msgs.append(f"package: {s.name} — {s.status}: {s.detail}")
    return PackageResult(merged_dxf=out, base_file=sources[0].file, sources=sources,
                         clip=clip, log=msgs)


def _safe_prefix(name: str) -> str:
    """Layer-name-safe xref prefix (DXF forbids <>/\\":;?*|=`)."""
    return re.sub(r'[<>/\\":;?*|=`]', "_", name).strip() or "xref"


# --------------------------------------------------------------------------- classification

def split_layer(name: str) -> tuple[str, str]:
    """"<xref>$0$<layer>" → (xref, layer); plain names → ("", name)."""
    if BIND_SEP in name:
        pre, _, rest = name.partition(BIND_SEP)
        return pre, rest
    return "", name


def apply_source_classification(doc, sources: list[PackageSource]) -> list[str]:
    """Override layer classes by the package source they came from.

    * the work boundary is THE site boundary; nothing else may be one;
    * survey frames (brd) and red lines (kl) are annotations;
    * anything in a utilities export (up / pp) is a utility — its specific
      type from the layer name when recognisable, else generic UTILITY;
    * cover fills without a recognised cover transition are annotations
      (frames, helper outlines).
    Returns human-readable notes for the conversion log.
    """
    from ..models.ir import UTILITY_CATEGORIES, LayerClassification

    kinds = {s.prefix: s.kind for s in sources if s.prefix}
    has_work_boundary = any(s.kind == "work_boundary" and s.status == "merged" for s in sources)
    notes: list[str] = []

    def set_cls(li, cat, conf, why):
        li.classification = LayerClassification(category=cat, confidence=conf, source="package",
                                                requiresReview=False, matchedKeyword=why)

    for li in doc.layers:
        prefix, _ = split_layer(li.name)
        kind = kinds.get(prefix, "base" if not prefix else "other")
        cur = li.classification
        if kind == "work_boundary":
            set_cls(li, "SITE_BOUNDARY", 0.97, "source:work_boundary")
        elif kind in ("brd", "kl"):
            set_cls(li, "ANNOTATION", 0.95, f"source:{kind}")
        elif kind in ("up", "pp"):
            if cur.category in UTILITY_CATEGORIES:
                cur.requiresReview = False
            else:
                set_cls(li, "UTILITY", 0.8, f"source:{kind}")
        elif kind == "covers" and not (cur.matchedKeyword or "").startswith("cover:"):
            if cur.category != "GREEN_AREA":
                set_cls(li, "ANNOTATION", 0.8, "source:covers")
        elif has_work_boundary and cur.category == "SITE_BOUNDARY":
            set_cls(li, "ANNOTATION", 0.85, "site boundary taken from the work boundary")
    if has_work_boundary:
        notes.append("package: site boundary = work boundary (other boundary-like layers → annotations)")
    return notes
