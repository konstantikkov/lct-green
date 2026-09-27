"""Checks a GreenPlan export against its source drawing.

    python scripts/check_dxf_layers.py <source.dxf> <result_greening.dxf>

Passes (exit 0) when:
  * every model-space entity of the source is present in the result with the
    same handle, type and layer — nothing was moved, retyped or deleted;
  * every source layer still exists with the same color, linetype and
    on/frozen/locked state;
  * every entity added by the export lies on an AI_GREENING_* layer.
Prints per-layer counts of the added entities and how many carry GREENPLAN
XDATA. Exit 1 lists each violation.
"""
from __future__ import annotations

import sys
from collections import Counter

from ezdxf import recover

RESULT_PREFIX = "AI_GREENING_"
APPID = "GREENPLAN"


def _read(path: str):
    doc, auditor = recover.readfile(path)
    if auditor.has_errors:
        print(f"! {path}: {len(auditor.errors)} DXF structure errors (recovered)")
    return doc


def _layer_state(layer) -> tuple:
    return (layer.dxf.color, layer.dxf.linetype, layer.is_on(), layer.is_frozen(), layer.is_locked())


def main(src_path: str, out_path: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    src, out = _read(src_path), _read(out_path)
    problems: list[str] = []

    src_ents = {e.dxf.handle: e for e in src.modelspace()}
    out_ents = {e.dxf.handle: e for e in out.modelspace()}
    for handle, e in src_ents.items():
        o = out_ents.get(handle)
        if o is None:
            problems.append(f"source entity {handle} ({e.dxftype()} on {e.dxf.layer}) is missing")
        elif o.dxftype() != e.dxftype() or o.dxf.layer != e.dxf.layer:
            problems.append(f"source entity {handle} changed: {e.dxftype()}@{e.dxf.layer} -> "
                            f"{o.dxftype()}@{o.dxf.layer}")

    out_layers = {layer.dxf.name: layer for layer in out.layers}
    for layer in src.layers:
        name = layer.dxf.name
        if name not in out_layers:
            problems.append(f"source layer {name!r} is missing")
        elif _layer_state(out_layers[name]) != _layer_state(layer):
            problems.append(f"source layer {name!r} properties changed")

    added = [e for h, e in out_ents.items() if h not in src_ents]
    per_layer = Counter(e.dxf.layer for e in added)
    for layer, n in sorted(per_layer.items()):
        if not layer.startswith(RESULT_PREFIX):
            problems.append(f"{n} added entities on non-result layer {layer!r}")
    with_xdata = sum(1 for e in added if e.has_xdata(APPID))

    print(f"source: {len(src_ents)} entities, {len(src.layers)} layers — "
          f"{'unchanged' if not problems else 'SEE PROBLEMS'}")
    print(f"added:  {len(added)} entities, {with_xdata} with {APPID} XDATA")
    for layer, n in sorted(per_layer.items()):
        print(f"  {layer:28} {n}")
    for p in problems:
        print(f"! {p}")
    print("OK" if not problems else f"FAILED: {len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
