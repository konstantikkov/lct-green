"""Close-range lawn blades for eye-level panoramas (standalone Blender Python).

Blades are scattered only inside source GREEN_AREA surfaces and never over
paving, roads or buildings. They are a material-level detail of an existing
lawn, not a new object: density fades with distance from the camera and the
textured ground continues beyond the radius.
"""
import math
import random
import bpy
from render_geometry import TriangleIndex, point_in_tri

HARD = ('ROAD', 'PARKING', 'SIDEWALK', 'BUILDING')


def _sock(sockets, name):
    return next(s for s in sockets if s.name == name and s.enabled)


def _blade_material():
    m = bpy.data.materials.new('Lawn blade')
    m.use_nodes = True
    nodes, links = m.node_tree.nodes, m.node_tree.links
    bs = nodes.get('Principled BSDF')
    bs.inputs['Roughness'].default_value = .5
    info = nodes.new('ShaderNodeObjectInfo')
    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color = (.045, .11, .018, 1)
    ramp.color_ramp.elements[1].color = (.16, .25, .045, 1)
    ramp.color_ramp.elements.new(.85).color = (.11, .21, .03, 1)
    links.new(info.outputs['Random'], ramp.inputs[0])
    links.new(ramp.outputs[0], bs.inputs['Base Color'])
    # Thin blades transmit sunlight; a translucent lobe keeps backlit grass bright.
    translucent = nodes.new('ShaderNodeBsdfTranslucent')
    links.new(ramp.outputs[0], translucent.inputs['Color'])
    mix = nodes.new('ShaderNodeMixShader')
    mix.inputs['Fac'].default_value = .3
    out = nodes.get('Material Output')
    links.new(bs.outputs[0], mix.inputs[1])
    links.new(translucent.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs['Surface'])
    return m


def _clumps(rng, material, variants=6):
    collection = bpy.data.collections.new('Lawn blade variants')
    for v in range(variants):
        verts, faces = [], []
        for _ in range(rng.randint(14, 22)):
            bx, by = rng.uniform(-.035, .035), rng.uniform(-.035, .035)
            height = rng.uniform(.05, .12)
            width = rng.uniform(.003, .0055)
            az = rng.uniform(0, math.tau)
            lean = rng.uniform(.15, .55) * height
            dx, dy = math.cos(az), math.sin(az)
            px, py = -dy, dx
            start = len(verts)
            rows = 4
            for r in range(rows + 1):
                t = r / rows
                cx, cy, cz = bx + dx * lean * t * t, by + dy * lean * t * t, height * t * (1 - .15 * t)
                w = width * max(.04, (1 - t) ** .7) / 2
                verts.extend([(cx - px * w, cy - py * w, cz), (cx + px * w, cy + py * w, cz)])
            faces.extend((start + 2 * r, start + 2 * r + 1, start + 2 * r + 3, start + 2 * r + 2) for r in range(rows))
        geo = bpy.data.meshes.new(f'Lawn clump {v}')
        geo.from_pydata(verts, [], faces)
        geo.update()
        geo.materials.append(material)
        obj = bpy.data.objects.new(f'Lawn clump {v}', geo)
        collection.objects.link(obj)
    return collection, variants


def _instancer(collection, variants, seed):
    ng = bpy.data.node_groups.new('Lawn blade instancing', 'GeometryNodeTree')
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeCollectionInfo')
    info.inputs['Collection'].default_value = collection
    info.inputs['Separate Children'].default_value = True
    info.inputs['Reset Children'].default_value = True
    inst = nodes.new('GeometryNodeInstanceOnPoints')
    inst.inputs['Pick Instance'].default_value = True
    pick = nodes.new('FunctionNodeRandomValue'); pick.data_type = 'INT'
    _sock(pick.inputs, 'Min').default_value = 0
    _sock(pick.inputs, 'Max').default_value = variants - 1
    _sock(pick.inputs, 'Seed').default_value = seed
    turn = nodes.new('FunctionNodeRandomValue'); turn.data_type = 'FLOAT_VECTOR'
    _sock(turn.inputs, 'Min').default_value = (-.12, -.12, 0)
    _sock(turn.inputs, 'Max').default_value = (.12, .12, math.tau)
    _sock(turn.inputs, 'Seed').default_value = seed + 1
    size = nodes.new('FunctionNodeRandomValue'); size.data_type = 'FLOAT'
    _sock(size.inputs, 'Min').default_value = .7
    _sock(size.inputs, 'Max').default_value = 1.35
    _sock(size.inputs, 'Seed').default_value = seed + 2
    links.new(gin.outputs[0], inst.inputs['Points'])
    links.new(info.outputs[0], inst.inputs['Instance'])
    links.new(_sock(pick.outputs, 'Value'), inst.inputs['Instance Index'])
    links.new(_sock(turn.outputs, 'Value'), inst.inputs['Rotation'])
    links.new(_sock(size.outputs, 'Value'), inst.inputs['Scale'])
    links.new(inst.outputs[0], gout.inputs[0])
    return ng


def scatter_lawn(data, ox, oy, eye, radius, density=260, seed=42):
    """Instance blade clumps on source lawns within ``radius`` of ``eye``.

    ``eye`` is in scene-local metres (CAD minus the scene offset). Returns the
    number of clumps placed.
    """
    if radius <= 0:
        return 0
    green, hard = [], []
    for s in data.get('surfaces', []):
        cat = s['category']
        z = float(s.get('baseZ', 0)) + (float(s['height']) if cat == 'BUILDING' else float(s.get('height', .02)))
        tris = [[(x - ox, y - oy, z) for x, y in tri] for tri in s['triangles']]
        (green if cat == 'GREEN_AREA' else hard if cat in HARD else []).extend(tris)
    for face in data.get('terrainFaces', []):
        cat = face.get('category')
        pts = [(x - ox, y - oy, z + .001) for x, y, z in face['vertices']]
        tris = [[pts[0], pts[i], pts[i + 1]] for i in range(1, len(pts) - 1)]
        (green if cat == 'GREEN_AREA' else hard if cat in HARD else []).extend(tris)
    if not green:
        return 0
    lawns, blocked = TriangleIndex(green), TriangleIndex(hard)
    rng = random.Random(seed)
    near = min(6., radius * .25)
    points = []
    candidates = int(math.pi * radius * radius * density)
    for _ in range(candidates):
        r = radius * math.sqrt(rng.random())
        # Full density near the viewer, ~10 % at the radius edge.
        if r > near and rng.random() > 1 - .9 * (r - near) / (radius - near):
            continue
        a = rng.random() * math.tau
        p = (eye[0] + r * math.cos(a), eye[1] + r * math.sin(a))
        if blocked.contains(p):
            continue
        cell = (math.floor(p[0] / lawns.step), math.floor(p[1] / lawns.step))
        tri = next((t for t in lawns.bins.get(cell, []) if point_in_tri(p, t)), None)
        if tri is None:
            continue
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = tri
        det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(det) < 1e-12:
            continue
        w0 = ((y1 - y2) * (p[0] - x2) + (x2 - x1) * (p[1] - y2)) / det
        w1 = ((y2 - y0) * (p[0] - x2) + (x0 - x2) * (p[1] - y2)) / det
        points.append((p[0], p[1], w0 * z0 + w1 * z1 + (1 - w0 - w1) * z2))
    if not points:
        return 0
    collection, variants = _clumps(rng, _blade_material())
    geo = bpy.data.meshes.new('Lawn blades (panorama)')
    geo.vertices.add(len(points))
    geo.vertices.foreach_set('co', [c for p in points for c in p])
    geo.update()
    obj = bpy.data.objects.new('Lawn blades (panorama)', geo)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new('Lawn blades', 'NODES').node_group = _instancer(collection, variants, seed)
    return len(points)
