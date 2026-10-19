"""Automatic standing points for a walkable 360° panorama tour.

Points are placed on source paths first, then spread over lawns, never inside
buildings, at tree trunks or inside shrubs. Neighbours are linked only with a
clear line of sight past buildings, at most one per 45° sector, so the viewer
shows Street View-like arrows instead of every point on the site.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

PATHS = ('SIDEWALK',)
OPEN_GROUND = ('GREEN_AREA', 'PARKING')


def _union(scene, categories):
    polys = [Polygon(t) for s in scene['surfaces'] if s['category'] in categories for t in s['triangles']]
    polys += [Polygon([(x, y) for x, y, _ in f['vertices']]) for f in scene.get('terrainFaces', [])
              if f.get('category') in categories and len(f['vertices']) >= 3]
    return unary_union([p.buffer(0) for p in polys if p.area > 1e-6])


def _candidates(area, step):
    if area.is_empty:
        return []
    x0, y0, x1, y1 = area.bounds
    inside = prep(area)
    nx, ny = int((x1-x0)/step)+1, int((y1-y0)/step)+1
    return [(x0+i*step, y0+j*step) for i in range(nx) for j in range(ny)
            if inside.contains(Point(x0+i*step, y0+j*step))]


def _farthest(candidates, chosen, spacing, limit):
    """Greedy farthest-point sampling: even coverage with a minimum spacing."""
    best = [min((math.dist(c, p) for p in chosen), default=math.inf) for c in candidates]
    while candidates and len(chosen) < limit:
        i = max(range(len(candidates)), key=best.__getitem__)
        if best[i] < spacing:
            break
        chosen.append(candidates[i])
        best = [min(b, math.dist(c, candidates[i])) for b, c in zip(best, candidates)]
    return chosen


def tour_points(scene, max_points=12, min_spacing=12.):
    """Return ``[{x, y, heading, links}]`` in walking order (CAD metres)."""
    buildings = _union(scene, ('BUILDING',))
    paths = _union(scene, PATHS)
    ground = unary_union([paths, _union(scene, OPEN_GROUND)])
    if ground.is_empty:
        (x0, y0), (x1, y1) = scene['extents']['min'], scene['extents']['max']
        ground = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]).difference(_union(scene, ('ROAD',)))
    obstacles = [buildings.buffer(1.5)]
    for p in scene.get('placements', []):
        radius = .9 if p['kind'] == 'TREE' else float(p.get('crownRadiusM', .8)) + .3
        obstacles.append(Point(p['position']).buffer(radius, 8))
    blocked = unary_union(obstacles)
    # Keep a margin from surface edges so the camera stands on, not beside, a path.
    usable = ground.buffer(-.6).difference(blocked)
    if usable.is_empty:
        usable = ground.difference(blocked)
    if usable.is_empty:
        return []
    spacing = max(min_spacing, math.sqrt(usable.area / max_points))
    step = max(1., spacing / 8)
    walk = usable.intersection(paths) if not paths.is_empty else Polygon()
    # Paths are often 2–4 m wide: sample them finely so they are not skipped.
    on_paths = _candidates(walk, max(min(step, 1.), math.sqrt(walk.area / 20000))) if walk.area else []
    everywhere = _candidates(usable, step)
    if not everywhere and not on_paths:
        c = usable.representative_point()
        everywhere = [(c.x, c.y)]
    centre = usable.centroid
    start_pool = on_paths or everywhere
    chosen = [min(start_pool, key=lambda c: math.dist(c, (centre.x, centre.y)))]
    chosen = _farthest(on_paths, chosen, spacing, max_points)
    from_paths = len(chosen)
    chosen = _farthest(everywhere, chosen, spacing, max_points)
    chosen = _relax(chosen, everywhere or on_paths, [on_paths or everywhere]*from_paths)
    chosen = _walking_order(chosen)

    walls = prep(buildings) if not buildings.is_empty else None
    links = []
    for i, a in enumerate(chosen):
        sectors = {}
        for j, b in enumerate(chosen):
            d = math.dist(a, b)
            if i == j or d > spacing * 2.3:
                continue
            if walls and walls.intersects(LineString([a, b])):
                continue
            sector = int(((math.degrees(math.atan2(b[0]-a[0], b[1]-a[1])) + 360 + 22.5) % 360) // 45)
            if sector not in sectors or d < math.dist(a, chosen[sectors[sector]]):
                sectors[sector] = j
        links.append(sorted(sectors.values()))
    # Symmetric: every arrow has a way back.
    for i, linked in enumerate(links):
        for j in linked:
            if i not in links[j]:
                links[j].append(i)
    # Isolated points (e.g. behind a building) still link to their nearest point.
    for i, a in enumerate(chosen):
        if not links[i] and len(chosen) > 1:
            j = min((j for j in range(len(chosen)) if j != i), key=lambda j: math.dist(a, chosen[j]))
            links[i].append(j)
            if i not in links[j]:
                links[j].append(i)
    result = []
    for i, (x, y) in enumerate(chosen):
        # Face the next point of the walk (or back towards the site centre).
        target = chosen[i+1] if i+1 < len(chosen) else (centre.x, centre.y)
        heading = (math.degrees(math.atan2(target[0]-x, target[1]-y)) + 360) % 360 if target != (x, y) else 0.
        result.append({'x': round(x, 2), 'y': round(y, 2), 'heading': round(heading, 1), 'links': sorted(links[i])})
    return result


def _relax(points, candidates, pools, rounds=6):
    """Lloyd relaxation: move each point to the centre of the area it covers,
    snapped back to a usable candidate (path points stay on paths), so the
    farthest-point seeds leave the site corners for the middle of their zone."""
    for _ in range(rounds):
        cells = [[] for _ in points]
        for c in candidates:
            cells[min(range(len(points)), key=lambda i: math.dist(c, points[i]))].append(c)
        moved = []
        for i, cell in enumerate(cells):
            if not cell:
                moved.append(points[i]); continue
            mean = (sum(c[0] for c in cell)/len(cell), sum(c[1] for c in cell)/len(cell))
            pool = pools[i] if i < len(pools) else candidates
            moved.append(min(pool, key=lambda c: math.dist(c, mean)))
        if moved == points:
            break
        points = moved
    return list(dict.fromkeys(points))


def _walking_order(points):
    """Nearest-neighbour chain from the first point: consecutive points are close."""
    if not points:
        return points
    order, rest = [points[0]], points[1:]
    while rest:
        nxt = min(rest, key=lambda p: math.dist(p, order[-1]))
        order.append(nxt)
        rest.remove(nxt)
    return order
