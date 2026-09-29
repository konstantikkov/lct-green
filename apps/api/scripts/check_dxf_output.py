"""Checks that an exported DXF opens in AutoCAD — what ezdxf reads but AutoCAD
reports as a damaged file.

    python scripts/check_dxf_output.py <result.dxf>

  * dangling pointers: every handle reference (pointer group codes) must name
    an object defined in the file;
  * values AutoCAD refuses: NaN / inf, degenerate circles / arcs / ellipses,
    zero text height, DIMENSIONs without their geometry block, INSERTs of
    missing blocks, empty hatches, splines without enough control points —
    in model space and inside block definitions.
Exit 0 when clean, 1 with the list of problems.
"""
from __future__ import annotations

import collections
import math
import re
import sys
from pathlib import Path

POINTER_CODES = set(range(320, 370)) | set(range(390, 400)) | {480, 481, 1005}


def dangling_pointers(path: str | Path) -> dict:
    """Raw tag scan: handles defined (codes 5 / 105) vs pointer references."""
    lines = [ln.rstrip("\r") for ln in Path(path).read_text(encoding="utf-8", errors="replace").split("\n")]
    defined: set[str] = set()
    refs: list[tuple[int, str, str, str]] = []
    section = etype = None
    for i in range(0, len(lines) - 1, 2):
        c, v = lines[i].strip(), lines[i + 1]
        if c == "2" and etype == "SECTION":
            section = v.strip()
        if c == "0":
            etype = v.strip()
            continue
        try:
            code = int(c)
        except ValueError:
            continue
        if code in (5, 105) and section != "HEADER":
            defined.add(v.strip().upper())
        elif code in POINTER_CODES:
            h = v.strip().upper()
            if h and h != "0":
                refs.append((code, h, etype or "", section or ""))
    dangling = [r for r in refs if r[1] not in defined]
    by_kind = collections.Counter((t, c, s) for c, _h, t, s in dangling)
    return {"defined": len(defined), "pointers": len(refs), "dangling": len(dangling),
            "byKind": [f"{t} code {c} in {s}: {n}" for (t, c, s), n in by_kind.most_common(20)]}


def value_problems(path: str | Path) -> dict:
    """Entity values AutoCAD rejects although ezdxf loads them."""
    import ezdxf
    raw = Path(path).read_text(encoding="utf-8", errors="replace").split("\n")
    nan = sum(1 for i in range(1, len(raw), 2)
              if re.fullmatch(r"\s*[-+]?(nan|inf|infinity)\s*", raw[i].strip(), re.I))
    doc = ezdxf.readfile(str(path))
    blocks = {b.name.upper() for b in doc.blocks}  # block names are case-insensitive
    bad: collections.Counter = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)

    def note(key: str, e, where: str) -> None:
        bad[key] += 1
        if len(examples[key]) < 3:
            examples[key].append(f"{where} #{e.dxf.handle} layer={e.dxf.layer}")

    def finite(*vals) -> bool:
        return all(math.isfinite(float(v)) for v in vals)

    def scan(layout, where: str) -> None:
        for e in layout:
            t = e.dxftype()
            try:
                if t == "ELLIPSE":
                    if not (1e-6 <= abs(e.dxf.ratio) <= 1.0) or e.dxf.major_axis.magnitude <= 1e-12:
                        note("ELLIPSE bad ratio/axis", e, where)
                elif t in ("CIRCLE", "ARC"):
                    if not (e.dxf.radius > 0) or not finite(e.dxf.radius):
                        note(f"{t} radius <= 0", e, where)
                elif t in ("TEXT", "ATTRIB", "ATTDEF"):
                    if not (e.dxf.height > 0):
                        note(f"{t} height <= 0", e, where)
                    if e.dxf.hasattr("width") and not (e.dxf.width > 0):
                        note(f"{t} width factor <= 0", e, where)
                elif t == "MTEXT":
                    if not (e.dxf.char_height > 0):
                        note("MTEXT height <= 0", e, where)
                elif t == "DIMENSION":
                    g = e.dxf.get("geometry")
                    if not g or g.upper() not in blocks:
                        note("DIMENSION without geometry block", e, where)
                elif t == "INSERT":
                    if e.dxf.name.upper() not in blocks:
                        note("INSERT of missing block", e, where)
                    if not all(abs(e.dxf.get(k, 1.0)) > 1e-12 for k in ("xscale", "yscale", "zscale")):
                        note("INSERT zero scale", e, where)
                elif t == "HATCH":
                    if len(e.paths) == 0:
                        note("HATCH without paths", e, where)
                    for p in e.paths:
                        if p.type.name == "POLYLINE_PATH" and len(p.vertices) < 2:
                            note("HATCH polyline path < 2 vertices", e, where)
                        if p.type.name == "EDGE_PATH" and len(p.edges) == 0:
                            note("HATCH edge path without edges", e, where)
                    if not e.dxf.solid_fill:
                        pat = e.pattern
                        if pat is None or len(pat.lines) == 0:
                            note("HATCH pattern without pattern lines", e, where)
                        if not (e.dxf.get("pattern_scale", 1.0) > 0):
                            note("HATCH pattern scale <= 0", e, where)
                elif t == "LWPOLYLINE":
                    if len(e) < 2:
                        note("LWPOLYLINE < 2 vertices", e, where)
                    if not all(finite(*p) for p in e.get_points("xyseb")):
                        note("LWPOLYLINE non-finite", e, where)
                elif t == "SPLINE":
                    # a spline given by fit points only is valid: AutoCAD computes it
                    if e.dxf.degree < 1 or (len(e.control_points) < e.dxf.degree + 1
                                              and len(e.fit_points) < 2):
                        note("SPLINE too few control points", e, where)
            except Exception as exc:  # noqa: BLE001
                note(f"{t} unreadable ({type(exc).__name__})", e, where)

    scan(doc.modelspace(), "msp")
    for b in doc.blocks:
        name = b.name.lower()
        if name != "*model_space" and not name.startswith("*paper_space"):
            scan(b, f"block {b.name}")
    return {"nanInf": nan, "problems": dict(bad), "examples": dict(examples)}


def check(path: str | Path) -> list[str]:
    """Problems as readable lines; empty when AutoCAD should open the file."""
    out = []
    d = dangling_pointers(path)
    if d["dangling"]:
        out.append(f"{d['dangling']} dangling pointer(s) of {d['pointers']}: " + "; ".join(d["byKind"][:5]))
    v = value_problems(path)
    if v["nanInf"]:
        out.append(f"{v['nanInf']} NaN/inf value(s)")
    for key, n in v["problems"].items():
        out.append(f"{n} × {key} (e.g. {', '.join(v['examples'].get(key, []))})")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    problems = check(sys.argv[1])
    print("AutoCAD-safe: OK" if not problems else "AutoCAD-safe: PROBLEMS")
    for p in problems:
        print("  " + p)
    sys.exit(1 if problems else 0)
