"""Spatial + semantic chunking.

Produces the compact LLM-facing IR: an adaptive grid of chunks, each with a
bbox, per-category counts, text labels and entity references. Only selected
chunks (never the raw CAD IR) are ever placed into LLM context.
"""
from __future__ import annotations

import math
from ..models.ir import CadDocument

TARGET_CHUNKS = 144  # ~12x12 grid max; adapts to aspect ratio and density


def build_chunks(doc: CadDocument) -> list[dict]:
    (minx, miny), (maxx, maxy) = doc.extents.min, doc.extents.max
    w, h = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)

    # adaptive cell size: denser drawings → more cells (up to TARGET_CHUNKS)
    n_entities = len(doc.entities)
    cells = min(TARGET_CHUNKS, max(9, n_entities // 40))
    aspect = w / h
    nx = max(2, round(math.sqrt(cells * aspect)))
    ny = max(2, round(cells / nx))
    cw, ch = w / nx, h / ny

    lm = doc.layer_map()
    grid: dict[tuple[int, int], dict] = {}

    def cell_of(x: float, y: float) -> tuple[int, int]:
        i = min(nx - 1, max(0, int((x - minx) / cw)))
        j = min(ny - 1, max(0, int((y - miny) / ch)))
        return (i, j)

    for e in doc.entities:
        g = e.geometry
        if "position" in g:
            cx, cy = g["position"]
        else:
            pts = g.get("points") or []
            if not pts:
                continue
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
        key = cell_of(cx, cy)
        chunk = grid.setdefault(key, {
            "entityIds": [], "counts": {}, "textLabels": [], "layers": set(),
        })
        chunk["entityIds"].append(e.id)
        li = lm.get(e.layer)
        cat = li.classification.category if li else "UNKNOWN"
        chunk["counts"][cat] = chunk["counts"].get(cat, 0) + 1
        chunk["layers"].add(e.layer)
        if e.kind == "text":
            t = (g.get("text") or "").strip()
            if t and len(chunk["textLabels"]) < 12:
                chunk["textLabels"].append(t[:80])

    chunks = []
    for (i, j), c in sorted(grid.items()):
        chunks.append({
            "chunkId": f"chunk_{i}_{j}",
            "bbox": [round(minx + i * cw, 2), round(miny + j * ch, 2),
                     round(minx + (i + 1) * cw, 2), round(miny + (j + 1) * ch, 2)],
            "summary": c["counts"],
            "entityCount": len(c["entityIds"]),
            "entityIds": c["entityIds"][:500],
            "layers": sorted(c["layers"]),
            "textLabels": c["textLabels"],
        })
    return chunks


def chunks_llm_summary(chunks: list[dict], max_chunks: int = 24) -> list[dict]:
    """Compact chunk digest for LLM context (drops entity references)."""
    ranked = sorted(chunks, key=lambda c: -c["entityCount"])[:max_chunks]
    return [{
        "chunkId": c["chunkId"], "bbox": c["bbox"],
        "summary": c["summary"], "textLabels": c["textLabels"][:5],
    } for c in ranked]
