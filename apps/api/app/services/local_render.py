"""Portable, offline Cycles render package. Never interprets layer names."""
from __future__ import annotations
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def scene_data(doc, iteration):
    # Only explicit/reviewed semantic assignments affect 3D reconstruction.
    layers = doc.layer_map()
    surfaces = []
    for e in doc.entities:
        li = layers.get(e.layer)
        cat = li.classification.category if li else "UNKNOWN"
        if cat not in {"BUILDING", "ROAD", "SIDEWALK", "PARKING", "GREEN_AREA"}:
            continue
        if e.kind != "polygon":
            continue
        from shapely.geometry import Polygon, GeometryCollection
        from shapely.ops import triangulate
        rings = e.geometry.get("rings") or [e.geometry.get("points", [])]
        if not rings or len(rings[0]) < 3:
            continue
        # Even/odd fill handles hatch islands and multiple disconnected shells.
        poly = GeometryCollection()
        for ring in rings:
            if len(ring) < 3:
                continue
            part = Polygon(ring)
            if not part.is_valid:
                part = part.buffer(0)
            poly = poly.symmetric_difference(part)
        faces = [list(t.exterior.coords)[:3] for t in triangulate(poly) if poly.covers(t)]
        surfaces.append({"category": cat, "layer": e.layer, "triangles": faces,
                         "height": e.attributes.get("heightM", 12)})
    return {"extents": doc.extents.model_dump(), "surfaces": surfaces,
            "placements": iteration["placements"], "seed": 42,
            "assumptions": ["2D building footprints use an illustrative 12 m height unless heightM is supplied.",
                            "Vegetation depicts indicative mature form, not a survey of existing trees.",
                            "Unknown layers remain in the accompanying CAD drawing; they are not invented as 3D objects."]}


def write_bundle(path: Path, doc, iteration, dxf: Path):
    script = Path(__file__).with_name("blender_scene.py")
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr("scene.json", json.dumps(scene_data(doc, iteration), ensure_ascii=False))
        z.write(script, "render.py")
        trees = Path(__file__).parents[1]/'assets'/'trees.blend'
        if trees.is_file():
            z.write(trees, 'trees.blend')
        z.write(dxf, "plan.dxf")
        z.writestr("README.txt", "GreenPlan local render — Blender 4.0+ / Cycles\n\n"
                    "Unzip, open a terminal in this directory, then run:\n"
                    "blender -b -t 8 --python render.py -- --scene scene.json --output render.png --samples 128 --width 1920 --height 1080\n\n"
                    "No internet, paid API, or GPU required. CPU rendering can take several minutes.\n"
                    "For production: --samples 512 --width 3840 --height 2160\n"
                    "Use --blend scene.blend to save an editable scene and choose your own camera/materials in Blender.\n"
                    "Camera: --azimuth 225 --elevation 30 --distance 1.0 (relative to site span).\n"
                    "Optional --assets trees.blend: mesh objects named TREE or SHRUB replace procedural vegetation.\n"
                    "Geometry, coordinates and assumptions are recorded in scene.json; plan.dxf is the exported plan.\n")
