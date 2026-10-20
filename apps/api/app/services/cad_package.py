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
# ИРД file kinds: "…_03117tp" (Мосгеотрест export) or "tp_Макеева С. ул"
_IRD_SUFFIX = re.compile(r"(tp|up|pp|kl|brd)$", re.IGNORECASE)
_IRD_PREFIX = re.compile(r"^(tp|up|pp|kl|brd)[_\s]", re.IGNORECASE)
# survey (ИРД) drawings by file name: "3_ДЖКХ-25_02294", "…03117up", "tp_…"
_IRD_NAME = re.compile(r"ДЖКХ|^(tp|up|pp|kl|brd)[_\s]|(tp|up|pp|kl|brd)$", re.IGNORECASE)
# the work boundary as a LAYER name, in whichever file it is drawn — but not
# what lies beyond it ("_за границами работ" in the ОДД drawing of Макеева)
WORK_BOUNDARY_LAYER = re.compile(r"(?<!за[\s_])(?<!вне[\s_])границ[а-я]*[\s_]*работ", re.IGNORECASE)
_KIND_PATTERNS = [
    (re.compile(r"(?<!за[\s_])(?<!вне[\s_])границ[а-я]*[\s_]*работ|(^|[_\s])границ[аы](?=$|[_\s])", re.IGNORECASE),
     "work_boundary"),
    (re.compile(r"борт", re.IGNORECASE), "curbs"),
    (re.compile(r"заливк", re.IGNORECASE), "covers"),
]
KIND_TITLES = {
    "base": "основной чертёж", "tp": "топоплан", "up": "существующие сети",
    "pp": "проектируемые сети", "kl": "красные линии", "brd": "рамки и граница съёмки",
    "work_boundary": "границы работ", "curbs": "бортовой камень", "covers": "покрытия (заливки)",
    "other": "прочая ссылка", "unused": "отдельный чертёж",
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
    status: str = "merged"    # merged | missing | failed | empty | nested_skipped | unused
    entities_in: int = 0
    entities_kept: int = 0
    detail: str = ""
    # set when the decoded model space is not where the file header says the
    # drawing is (LibreDWG lost it); internal, not part of the API
    decode_issue: str = ""
    header_box: tuple[float, float, float, float] | None = None

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
    # merged-DXF layers whose outlines form the work boundary (= site boundary)
    boundary_layers: list[str] = field(default_factory=list)

    def to_api(self) -> dict:
        return {"baseFile": self.base_file, "clip": self.clip,
                "boundaryLayers": self.boundary_layers,
                "sources": [s.to_api() for s in self.sources]}


def source_kind(name: str) -> str:
    stem = Path(name).stem if "." in name else name
    m = _IRD_SUFFIX.search(stem) or _IRD_PREFIX.search(stem)
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
    """eTransmit report: 'Базовый чертеж:' (ru) / 'Root Drawing:' (en).

    A zipped "Исходные данные" folder may hold several packages (general
    plan, dendro plan) and copies of the same base drawing in other folders:
    take the report listing the most xrefs, and the base drawing lying next
    to that report."""
    existing = {d.resolve() for d in drawings}
    best: tuple[int, Path] | None = None
    for rep in reports:
        text = _read_text(rep)
        m = re.search(r"(Базовый чертеж|Root Drawing)\s*:\s*\n\s*(.+?)\s*\n", text)
        if not m:
            continue
        cand = (rep.parent / m.group(2).strip()).resolve()
        if cand not in existing:
            continue
        xm = re.search(r"(Внешние ссылки из чертежа AutoCAD|AutoCAD Drawing Xrefs)\s*:\s*\n((?:[ \t]+\S.*\n)+)", text)
        n = len(xm.group(2).strip().splitlines()) if xm else 0
        if best is None or n > best[0]:
            best = (n, cand)
    return best[1] if best else None


def _xref_hint(path: Path) -> int:
    """Cheap pre-ranking of base-drawing candidates without converting them:
    how many '.dwg' path strings the file carries (UTF-16 in AC1021+,
    8-bit before)."""
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    return data.count(".dwg".encode("utf-16-le")) + data.count(b".dwg")


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


def _convert(src: Path, workdir: Path, converter: DwgConverter, extra: tuple[str, ...] = ()) -> Path:
    if src.suffix.lower() == ".dxf":
        return src
    out = workdir / f"{uuid.uuid4().hex}.dxf"
    return converter.convert_to_dxf(src, out, extra)


def _load_source(src_file: Path, dxf: Path, workdir: Path, converter: DwgConverter, msgs: list[str]):
    """Load a converted drawing; when even the repair paths fail on LibreDWG's
    default output (e.g. a layout without its block record), convert the DWG
    again with the R2000 writer and load that."""
    try:
        return _load(dxf, msgs)
    except Exception as first:  # noqa: BLE001
        if src_file.suffix.lower() != ".dwg" or getattr(converter.info, "kind", "") != "libredwg":
            raise
        msgs.append(f"{src_file.name}: unreadable DXF ({type(first).__name__}); reconverted as R2000")
        return _load(_convert(src_file, workdir, converter, ("--as", "r2000")), msgs)


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


def _bulk_delete(doc, layout, entities: list) -> None:
    """Delete many entities at once. Layout.delete_entity() is linear in the
    layout size, so deleting most of a 300k-entity model space one by one
    is quadratic (the 3-я Парковая package ran for 20+ minutes); destroying
    through the entity database and purging the layout once is linear."""
    if not entities:
        return
    for e in entities:
        doc.entitydb.delete_entity(e)
    layout.purge()


def _compose(child: Matrix44 | None, parent: Matrix44 | None) -> Matrix44 | None:
    """child → parent → base transform (None = identity)."""
    if child is None:
        return parent
    if parent is None:
        return child
    return Matrix44.chain(child, parent)


def _drop_dangling_block_refs(doc) -> tuple[int, int]:
    """Delete INSERTs of blocks LibreDWG did not convert (anonymous *U …) and
    DIMENSIONs without their *D geometry block — in model space AND inside
    block definitions, where the Importer would otherwise fail on them."""
    n_ins = n_dim = 0
    layouts = [doc.modelspace()] + [b for b in doc.blocks
                                    if not b.is_any_layout and not b.block.is_xref]
    for lay in layouts:
        ins = [e for e in lay.query("INSERT") if e.dxf.name not in doc.blocks]
        dims = [e for e in lay.query("DIMENSION")
                if not e.dxf.get("geometry") or e.dxf.geometry not in doc.blocks]
        _bulk_delete(doc, lay, ins + dims)
        n_ins += len(ins)
        n_dim += len(dims)
    return n_ins, n_dim


def _header_box(doc, matrix: Matrix44 | None) -> tuple[float, float, float, float] | None:
    """Drawing extents saved in the file header ($EXTMIN/$EXTMAX), in target
    coordinates; None when the header holds no valid extents."""
    lo, hi = doc.header.get("$EXTMIN"), doc.header.get("$EXTMAX")
    if lo is None or hi is None:
        return None
    x0, y0, x1, y1 = float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])
    if not all(abs(v) < 1e19 for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        return None
    if matrix is not None:
        (x0, y0, _), (x1, y1, _) = matrix.transform((x0, y0, 0)), matrix.transform((x1, y1, 0))
        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    return x0, y0, x1, y1


def _decoded_off_extents(doc) -> str:
    """LibreDWG sometimes loses a drawing's model space: the content stays in
    block definitions and the model space gets a few stray objects near the
    origin (Харьковский проезд: the survey plan and the lighting plan). The
    header still records where the drawing really is — compare. Returns a
    reason, or "" when most decoded objects lie within the header extents."""
    hb = _header_box(doc, None)
    if hb is None:
        return ""
    x0, y0, x1, y1 = hb
    pad = 0.05 * max(x1 - x0, y1 - y0)
    n = inside = 0
    for e in doc.modelspace():
        try:
            b = ezbbox.extents([e], fast=True)
        except Exception:  # noqa: BLE001
            continue
        if not b.has_data:
            continue
        n += 1
        cx, cy = (b.extmin.x + b.extmax.x) / 2, (b.extmin.y + b.extmax.y) / 2
        inside += x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad
    if n == 0 or inside >= 0.5 * n:
        return ""
    return (f"the model space was not decoded: by the file header the drawing lies at "
            f"x {x0:.0f}…{x1:.0f}, y {y0:.0f}…{y1:.0f}, but {n - inside} of {n} decoded objects "
            f"lie elsewhere — convert this file with ODA File Converter")


def _work_boundary(doc, layers: set[str] | None = None):
    """Closed polylines plus open pieces stitched end to end (the Moscow work
    boundary is often drawn as two open halves). `layers` limits the search;
    returns (union or None, names of the layers that contributed)."""
    from .geometry import stitch_rings
    polys, lines, used = [], [], set()
    if layers is not None and not layers:
        return None, []
    for e in doc.modelspace().query("LWPOLYLINE POLYLINE"):
        if layers is not None and e.dxf.layer not in layers:
            continue
        pts = [(p[0], p[1]) for p in (e.get_points("xy") if e.dxftype() == "LWPOLYLINE"
                                       else [v.dxf.location for v in e.vertices])]
        if len(pts) < 2:
            continue
        if e.closed and len(pts) >= 3:
            poly = Polygon(pts)
            poly = poly if poly.is_valid else poly.buffer(0)
            if poly.area > 1.0:
                polys.append(poly)
                used.add(e.dxf.layer)
        else:
            lines.append((LineString(pts), e.dxf.layer))
    rings = stitch_rings([ln for ln, _ in lines])
    if rings:
        polys.extend(rings)
        used.update(layer for _, layer in lines)
    return (unary_union(polys) if polys else None), sorted(used)


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
        # rank every drawing by the cheap xref hint, verify the top few by
        # reading their real xref tables (shallower first on ties)
        ranked = sorted(drawings, key=lambda d: (-_xref_hint(d), _rel_depth(d, unpack)))
        cands = ranked[:3]
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
        msgs.append(f"base drawing (most external references): {base.name}")
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
            src.status = "empty"  # may still arrive as a nested xref of another source
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
            wb, _ = _work_boundary(wdoc)
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
        src.decode_issue = _decoded_off_extents(doc)
        src.header_box = _header_box(doc, matrix)
        placeholders = []                      # xref placeholders never merge as blocks
        for e in msp.query("INSERT"):
            blk = doc.blocks.get(e.dxf.name)
            if blk is not None and blk.block is not None and (blk.block.is_xref or blk.block.is_xref_overlay):
                placeholders.append(e)
        _bulk_delete(doc, msp, placeholders)
        dropped, dims = _drop_dangling_block_refs(doc)
        if dropped:
            msgs.append(f"{src.name}: {dropped} block reference(s) without a block definition dropped")
        if dims:
            msgs.append(f"{src.name}: {dims} dimension(s) without a geometry block dropped")
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
        before = {lt.dxf.name for lt in target.linetypes}
        bad = 0
        tmsp = target.modelspace()
        for e in selected:
            try:
                imp.import_entity(e, tmsp)
            except Exception:  # noqa: BLE001 — e.g. MTEXT with invalid column data
                bad += 1
        if bad:
            src.entities_kept -= bad
            msgs.append(f"{src.name}: {bad} unreadable entit(ies) skipped")
        imp.finalize()
        # complex linetypes reference their shape/text STYLE by handle, one
        # reference per element. Every Importer.finalize() re-points only the
        # FIRST reference of ALL target linetypes through its own handle map
        # (others fall back to STANDARD), so fixing them here would be undone
        # by the next import: record what each element referenced and apply
        # it once all sources are merged
        for lt in target.linetypes:
            if lt.dxf.name not in before and lt.pattern_tags.is_complex_type():
                src_lt = doc.linetypes.get(lt.dxf.name) if lt.dxf.name in doc.linetypes else None
                if src_lt is not None:
                    ltype_styles[lt.dxf.name] = _linetype_style_refs(src_lt, doc)

    import time
    t0 = time.time()
    ltype_styles: dict[str, list] = {}
    merge(base_doc, sources[0], None)

    # queue: (source, file, xref ref, converted dxf, error, parent matrix,
    # parent units). Xrefs of xrefs (Макеева: base → xref_ИТП → tp_/up_) are
    # appended as they are discovered, with the parent's transform composed
    by_name = {s.name: s for s in sources}
    seen = {base.resolve()} | {p.resolve() for (_s, p, _r), _d, _e in converted}
    queue = [(src, path, ref, dxf, err, None, base_units)
             for (src, path, ref), dxf, err in converted]
    while queue:
        src, path, ref, dxf, err, parent_m, parent_units = queue.pop(0)
        if err is not None or dxf is None:
            src.status, src.detail = "failed", f"conversion failed: {err}"
            continue
        try:
            doc = loaded.get(src.name) or _load_source(path, dxf, conv_dir, converter, msgs)
            src_units = int(doc.header.get("$INSUNITS", 0) or 0)
            factor = 1.0
            if parent_units and src_units and src_units != parent_units:
                factor = ezdxf.units.conversion_factor(src_units, parent_units)
            m = _compose(_insert_matrix(ref, factor), parent_m)
            nested = [r for r in read_xrefs(doc) if r.inserts]
            merge(doc, src, m)
            src.status = "merged" if src.entities_in else "empty"
            for nref in nested:
                npath = resolve_xref(nref.path or nref.name + ".dwg", path.parent, unpack, index)
                nsrc = by_name.get(nref.name)
                if npath is not None and npath.resolve() in seen:
                    continue
                if nsrc is None or nsrc.status not in ("empty", "missing"):
                    nsrc = PackageSource(name=nref.name, kind=source_kind(nref.name),
                                         prefix=_safe_prefix(nref.name))
                    sources.append(nsrc)
                    by_name[nref.name] = nsrc
                if npath is None:
                    nsrc.status, nsrc.detail = "missing", f"not in package (nested in {src.name}): {nref.path}"
                    continue
                seen.add(npath.resolve())
                nsrc.file, nsrc.detail = str(npath.relative_to(unpack)), f"nested in {src.name}"
                try:
                    ndxf, nerr = _convert(npath, conv_dir, converter), None
                except Exception as exc:  # noqa: BLE001
                    ndxf, nerr = None, exc
                queue.append((nsrc, npath, nref, ndxf, nerr, m, src_units or parent_units))
        except Exception as exc:  # noqa: BLE001
            log.warning("package: merge of %s failed", src.name, exc_info=True)
            src.status, src.detail = "failed", f"merge failed: {exc}"
        loaded.pop(src.name, None)
    for s_ in sources:
        if s_.status == "empty" and not s_.entities_in and not s_.detail:
            s_.detail = "defined but not inserted in model space"

    # --- ИРД drawings shipped in the package but attached to nothing --------
    # (3-я Парковая: the survey underlay lies next to the base drawing, which
    # references only the lighting plan and the dendro plan). Same coordinate
    # system as the rest; copies of an already merged file are skipped.
    merged_names = {Path(s_.file).name.lower() for s_ in sources if s_.file}
    for d in sorted(drawings, key=lambda p: _rel_depth(p, unpack)):
        if d.resolve() in seen or d.name.lower() in merged_names or not _IRD_NAME.search(d.stem):
            continue
        seen.add(d.resolve())
        merged_names.add(d.name.lower())
        osrc = PackageSource(name=d.stem, kind=source_kind(d.stem), prefix=_safe_prefix(d.stem),
                             file=str(d.relative_to(unpack)),
                             detail="ИРД file not attached to the base drawing — merged by name")
        sources.append(osrc)
        try:
            doc = _load_source(d, _convert(d, conv_dir, converter), conv_dir, converter, msgs)
            merge(doc, osrc, None)
            osrc.status = "merged" if osrc.entities_in else "empty"
        except Exception as exc:  # noqa: BLE001
            log.warning("package: merge of %s failed", osrc.name, exc_info=True)
            osrc.status, osrc.detail = "failed", f"{osrc.detail}; merge failed: {exc}"

    # --- drawings nothing refers to: another object, with its own site and
    # coordinates — not merged, but listed, so a ZIP of several objects does
    # not silently become a plan of one of them
    for d in sorted(drawings, key=lambda p: _rel_depth(p, unpack)):
        if d.resolve() in seen or d.name.lower() in merged_names:
            continue
        merged_names.add(d.name.lower())
        sources.append(PackageSource(name=d.stem, kind="unused", file=str(d.relative_to(unpack)),
                                     status="unused",
                                     detail="not referenced by the base drawing — a separate object, "
                                            "upload it on its own"))

    # --- work boundary: from its own file and/or any "Граница работ" layer ---
    wb_prefixes = {s.prefix for s in sources if s.kind == "work_boundary" and s.status == "merged"}
    # layer names of the outlines themselves (a source may lack a table entry)
    outline_layers = {e.dxf.layer for e in target.modelspace().query("LWPOLYLINE POLYLINE")}
    cand_layers = {name for name in outline_layers
                   if WORK_BOUNDARY_LAYER.search(split_layer(name)[1])
                   or split_layer(name)[0] in wb_prefixes}
    boundary, boundary_layers = _work_boundary(target, cand_layers)
    if boundary is not None and region is None:
        # no work-boundary FILE: the boundary is a layer inside some drawing —
        # clip the merged model space now
        post = boundary.buffer(clip_margin_m)
        kept, total = _select(target.modelspace(), post)
        keep_ids = {id(e) for e in kept}
        by_prefix = {s.prefix: s for s in sources}
        drop = []
        for e in target.modelspace():
            if id(e) not in keep_ids:
                src = by_prefix.get(split_layer(e.dxf.layer)[0])
                if src is not None:
                    src.entities_kept -= 1
                drop.append(e)
        _bulk_delete(target, target.modelspace(), drop)
        region = post
        clip = {"mode": "work_boundary_layer", "source": ", ".join(sorted(boundary_layers))[:200],
                "marginM": clip_margin_m, "boundaryAreaM2": round(boundary.area, 1)}
        msgs.append(f"package: work boundary found on layer(s) {sorted(boundary_layers)}; "
                    f"kept {len(kept)} of {total} entities")

    # a source whose decoded objects are not where its header puts the
    # drawing, and of which nothing lies on the site although the header says
    # the drawing does, was not read — say so instead of "merged, 0 kept"
    for s_ in sources:
        if (s_.status == "merged" and s_.entities_in and not s_.entities_kept and s_.decode_issue
                and (region is None or s_.header_box is None or intersects(box(*s_.header_box), region))):
            s_.status = "failed"
            s_.detail = "; ".join(x for x in (s_.detail, s_.decode_issue) if x)

    for name, refs in ltype_styles.items():
        _apply_linetype_styles(target.linetypes.get(name), refs, target)
    fixes = make_autocad_safe(target)
    if fixes:
        msgs.append("package: made AutoCAD-safe — " + ", ".join(f"{k}: {v}" for k, v in fixes.items()))
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
    # names coming from CAD files may carry undecodable bytes (cp1251 inside
    # UTF-8 DXF): clean them exactly as the parser cleans layer names, so the
    # prefixes and boundary layers still match the parsed document
    from .dxf_parser import clean_text
    for s_ in sources:
        s_.name, s_.prefix = clean_text(s_.name), clean_text(s_.prefix)
        s_.file, s_.detail = clean_text(s_.file), clean_text(s_.detail)
    clip = {k: clean_text(v) if isinstance(v, str) else v for k, v in clip.items()}
    return PackageResult(merged_dxf=out, base_file=sources[0].file, sources=sources,
                         clip=clip, log=[clean_text(m) for m in msgs],
                         boundary_layers=sorted(clean_text(x) for x in boundary_layers))


def _target_style(target, font: str, shape: bool) -> str:
    """Handle of a target STYLE of this kind (shape file / text) and font
    file; created when missing."""
    if shape:  # ezdxf's own lookup: its add_shx() refuses what find_shx() finds
        found = target.styles.find_shx(font)
        return (found or target.styles.add_shx(font)).dxf.handle
    for st in target.styles:
        if not st.dxf.flags & 1 and (st.dxf.get("font") or "").lower() == font.lower():
            return st.dxf.handle
    name = "STYLE_" + (font.rsplit(".", 1)[0] or "txt")
    while name in target.styles:
        name += "_"
    return target.styles.add(name, font=font or "txt").dxf.handle


def _linetype_style_refs(src_lt, src_doc) -> list:
    """(font, is_shape_file) of the STYLE each element of a SOURCE complex
    linetype references; None when it does not resolve there either."""
    out = []
    for t in src_lt.pattern_tags.tags:
        if t.code == 340:
            st = src_doc.entitydb.get(t.value)
            out.append((st.dxf.get("font") or "", bool(st.dxf.flags & 1))
                       if st is not None and st.dxftype() == "STYLE" else None)
    return out


def _apply_linetype_styles(lt, refs: list, target) -> None:
    """Point the k-th style reference of the merged linetype at a target
    style with the font / kind the k-th source reference had."""
    from ezdxf.lldxf.tags import Tags
    from ezdxf.lldxf.types import DXFTag
    out, k = [], 0
    for t in lt.pattern_tags.tags:
        if t.code == 340 and k < len(refs):
            ref = refs[k]
            k += 1
            out.append(DXFTag(340, _target_style(target, *ref)) if ref else t)
        else:
            out.append(t)
    lt.pattern_tags.tags = Tags(out)


def _simplify_bad_linetype_elements(lt, db) -> int:
    """Each shape element (74 & 4) must reference a shape-file STYLE, each
    text element (74 & 2) a text STYLE; an element that does not becomes a
    plain dash (the file stays valid, the symbol is lost)."""
    from ezdxf.lldxf.tags import Tags
    from ezdxf.lldxf.types import DXFTag
    tags = list(lt.pattern_tags.tags)
    head = []
    while tags and tags[0].code != 49:
        head.append(tags.pop(0))
    elements, cur = [], None
    for t in tags:
        if t.code == 49:
            cur = [t]
            elements.append(cur)
        elif cur is not None:
            cur.append(t)
    fixed = 0
    out = list(head)
    for el in elements:
        flags = next((t.value for t in el if t.code == 74), 0)
        if flags & 6:
            h = next((t.value for t in el if t.code == 340), None)
            st = db.get(h) if h else None
            ok = (st is not None and st.dxftype() == "STYLE"
                  and bool(st.dxf.flags & 1) == bool(flags & 4))
            if not ok:
                el = [el[0], DXFTag(74, 0)]
                fixed += 1
        out.extend(el)
    if fixed:
        lt.pattern_tags.tags = Tags(out)
    return fixed


def make_autocad_safe(doc) -> dict[str, int]:
    """Repair what merging foreign drawings leaves behind and AutoCAD rejects
    as file damage although ezdxf reads it: pointers to objects of the SOURCE
    document. Returns counts of the repairs made.

    * associative hatches keep handles of their boundary entities, which
      mostly were not merged → drop associativity;
    * layers keep material / plot-style handles of the source → the
      target's defaults (those of layer "0");
    * complex linetype elements whose style is not a STYLE of the right
      kind (shape file for shapes, text style for text) → plain dashes;
    * INSERTs of blocks that do not exist and DIMENSIONs without their
      geometry block — LibreDWG leaves them in the base drawing too (the
      "_ArchTick" arrow block of Харьковский проезд);
    * then ezdxf's own audit (invalid DIMENSIONs, owners, …)."""
    fixes: dict[str, int] = {}
    db = doc.entitydb

    n_ins, n_dim = _drop_dangling_block_refs(doc)
    if n_ins:
        fixes["INSERTs of missing blocks dropped"] = n_ins
    if n_dim:
        fixes["DIMENSIONs without geometry block dropped"] = n_dim

    def resolves(handle, dxftype=None) -> bool:
        e = db.get(handle) if handle else None
        return e is not None and (dxftype is None or e.dxftype() == dxftype)

    layouts = [doc.modelspace()] + [b for b in doc.blocks]
    n = 0
    for layout in layouts:
        for e in layout.query("HATCH"):
            if e.dxf.get("associative", 0) or any(p.source_boundary_objects for p in e.paths):
                e.dxf.associative = 0
                for p in e.paths:
                    p.source_boundary_objects = []
                n += 1
    if n:
        fixes["hatches made non-associative"] = n

    zero = doc.layers.get("0")
    plot, mat = zero.dxf.get("plotstyle_handle"), zero.dxf.get("material_handle")
    n = 0
    for layer in doc.layers:
        changed = False
        h = layer.dxf.get("plotstyle_handle")
        # type-checked: a source handle may collide with an unrelated target object
        if h and not resolves(h, "ACDBPLACEHOLDER"):
            layer.dxf.plotstyle_handle = plot
            changed = True
        h = layer.dxf.get("material_handle")
        if h and not resolves(h, "MATERIAL"):
            layer.dxf.material_handle = mat
            changed = True
        n += changed
    if n:
        fixes["layer material/plot-style pointers reset"] = n

    n = 0
    for lt in doc.linetypes:
        if lt.pattern_tags.is_complex_type():
            n += _simplify_bad_linetype_elements(lt, db)
    if n:
        fixes["complex linetype elements without a valid style simplified to dashes"] = n

    auditor = doc.audit()
    if auditor.fixes:
        fixes["ezdxf audit fixes"] = len(auditor.fixes)
    return fixes


def ingest_package_cached(zip_path: Path, workdir: Path) -> PackageResult:
    """ingest_package through the ingest cache: the same package uploaded
    again with the same code and converter comes back in seconds."""
    import json
    import sys
    from . import cache, dwg_converter, dxf_parser
    key = None
    if cache.enabled():
        info = DwgConverter().info
        key = cache.make_key("package", cache.file_sha256(zip_path), str(info.kind), str(info.fallback),
                             cache.code_fingerprint(sys.modules[__name__], dxf_parser, dwg_converter))
        hit = cache.get(key)
        if hit is not None:
            workdir.mkdir(parents=True, exist_ok=True)
            merged = workdir / "merged.dxf"
            shutil.copyfile(hit / "merged.dxf", merged)
            meta = json.loads((hit / "package.json").read_text(encoding="utf-8"))
            sources = [PackageSource(name=s["name"], kind=s["kind"], file=s["file"], prefix=s["prefix"],
                                     status=s["status"], entities_in=s["entitiesIn"],
                                     entities_kept=s["entitiesKept"], detail=s["detail"])
                       for s in meta["sources"]]
            return PackageResult(merged_dxf=merged, base_file=meta["baseFile"], sources=sources,
                                 clip=meta["clip"], boundary_layers=meta["boundaryLayers"],
                                 log=meta["log"] + ["package: taken from the ingest cache "
                                                    "(same file, same code and converter)"])
    res = ingest_package(zip_path, workdir)
    if key is not None:
        def fill(d: Path) -> None:
            shutil.copyfile(res.merged_dxf, d / "merged.dxf")
            (d / "package.json").write_text(json.dumps({**res.to_api(), "log": res.log},
                                                       ensure_ascii=False), encoding="utf-8")
        cache.put(key, fill)
    return res


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


def apply_source_classification(doc, sources: list[PackageSource],
                                boundary_layers: list[str] | None = None) -> list[str]:
    """Override layer classes by the package source they came from.

    * the work boundary (its layers in `boundary_layers`, else every layer
      of a work-boundary file) is THE site boundary; nothing else may be one;
    * survey frames (brd) and red lines (kl) are annotations;
    * anything in a utilities export (up / pp) is a utility — its specific
      type from the layer name when recognisable, else generic UTILITY;
    * cover fills without a recognised cover transition are annotations
      (frames, helper outlines).
    Returns human-readable notes for the conversion log.
    """
    from ..models.ir import UTILITY_CATEGORIES, LayerClassification

    kinds = {s.prefix: s.kind for s in sources if s.prefix}
    wb_layers = set(boundary_layers or [])
    has_work_boundary = bool(wb_layers) or any(
        s.kind == "work_boundary" and s.status == "merged" for s in sources)
    notes: list[str] = []

    def set_cls(li, cat, conf, why):
        li.classification = LayerClassification(category=cat, confidence=conf, source="package",
                                                requiresReview=False, matchedKeyword=why)

    for li in doc.layers:
        prefix, _ = split_layer(li.name)
        kind = kinds.get(prefix, "base" if not prefix else "other")
        cur = li.classification
        if li.name in wb_layers or (kind == "work_boundary" and not wb_layers):
            set_cls(li, "SITE_BOUNDARY", 0.97, "source:work_boundary")
        elif kind == "work_boundary":
            set_cls(li, "ANNOTATION", 0.85, "source:work_boundary (not an outline)")
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
