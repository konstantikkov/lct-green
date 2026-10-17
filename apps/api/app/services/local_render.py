"""Portable, offline Cycles render package. Never interprets layer names."""
from __future__ import annotations
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def scene_data(doc, iteration):
    # Only explicit/reviewed semantic assignments affect 3D reconstruction.
    layers = doc.layer_map()
    surfaces = []
    building_lines = {}
    linear_features, point_features = [], []
    for e in doc.entities:
        li = layers.get(e.layer)
        cat = li.classification.category if li else "UNKNOWN"
        if cat == 'FENCE' and e.kind in ('polyline','polygon'):
            pts=e.geometry.get('points',[])
            if e.kind=='polygon' and pts: pts=pts+[pts[0]]
            linear_features.append({'category':cat,'points':pts})
        if cat == 'LIGHTING':
            pt=e.geometry.get('center') or (e.geometry.get('position') if e.kind=='point' else None)
            if pt: point_features.append({'category':cat,'position':pt})
        if cat not in {"BUILDING", "ROAD", "SIDEWALK", "PARKING", "GREEN_AREA"}:
            continue
        if e.kind != "polygon" and not (e.kind=="polyline" and e.geometry.get("closed")):
            if cat == 'BUILDING' and e.kind == 'polyline':
                from shapely.geometry import LineString
                pts = e.geometry.get('points', [])
                if len(pts) >= 2:
                    building_lines.setdefault(e.layer, []).append(LineString(pts))
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
    # Many CAD facades are separate LINE objects, not closed polylines.
    # Reconstruct only closed cycles; do not invent footprints for open lines.
    from shapely.ops import polygonize, unary_union, triangulate
    for layer, lines in building_lines.items():
        for poly in polygonize(unary_union(lines)):
            if poly.area < .5:
                continue
            faces = [list(t.exterior.coords)[:3] for t in triangulate(poly) if poly.covers(t)]
            surfaces.append({'category':'BUILDING', 'layer':layer, 'triangles':faces,'height':12})
    return {"sourceOnly":True,"extents": doc.extents.model_dump(), "surfaces": surfaces,
            "placements": iteration["placements"], "seed": 42,
            "linearFeatures":linear_features,"pointFeatures":point_features,
            "assumptions": ["Building footprints use illustrative 12 m heights and material defaults; heights are not surveyed.",
                            "Vegetation depicts indicative mature form, not a survey of existing trees.",
                            "Only source CAD geometry and selected plantings are rendered; no surrounding buildings, paths or furniture are invented.",
                            "Unknown layers remain in the accompanying CAD drawing; they are not invented as 3D objects."]}


def write_bundle(path: Path, doc, iteration, dxf: Path):
    script = Path(__file__).with_name("blender_scene.py")
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr("scene.json", json.dumps(scene_data(doc, iteration), ensure_ascii=False))
        z.write(script, "render.py")
        for dependency in ("blender_landscape.py","render_geometry.py","preview_materials.py","source_materials.py"):
            z.write(script.with_name(dependency),dependency)
        assets = Path(__file__).parents[1]/'assets'
        if assets.is_dir():
            for asset in assets.iterdir():
                if asset.suffix in {'.blend','.hdr','.jpg'}:
                    z.write(asset,asset.name)
            credits = assets/'README.txt'
            if credits.is_file(): z.write(credits,'ASSET_CREDITS.txt')
        z.write(dxf, "plan.dxf")
        z.writestr("README.txt", "GreenPlan local render — Blender 4.0+ / Cycles\n\n"
                    "Unzip, open a terminal in this directory, then run:\n"
                    "blender -b -t 8 --python render.py -- --scene scene.json --output render.png --samples 128 --width 1920 --height 1080\n\n"
                    "Source-only geometry and photographic materials; --detail cad. Camera: --view aerial or --view pedestrian.\n"
                    "No internet, paid API, or GPU required. CPU rendering can take several minutes.\n"
                    "For production: --samples 512 --width 3840 --height 2160\n"
                    "Use --blend scene.blend to save an editable scene and choose your own camera/materials in Blender.\n"
                    "Camera: --azimuth 225 --elevation 30 --distance 1.0 (relative to site span).\n"
                    "Optional --assets trees.blend: mesh objects named TREE or SHRUB replace procedural vegetation.\n"
                    "Geometry, coordinates and assumptions are recorded in scene.json; plan.dxf is the exported plan.\n")
