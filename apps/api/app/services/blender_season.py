"""Seasons for Cycles scenes (standalone Blender Python). Materials, lighting
and leafless copies of existing plant meshes only — no new objects.

summer  unchanged scene (the default)
spring  light young foliage; lilac and mock orange flower via the leaf
        material (noise-masked blossom colour), no blossom geometry
autumn  the «Season palette» ramp on leaf materials, toned per genus
winter  deciduous plants lose their leaf faces (branches stay); lawns and
        open ground turn to snow; no lawn blades; lower, cooler sun
"""
import bpy

SEASONS = ('spring', 'summer', 'autumn', 'winter')
LEAF_PREFIXES = ('Leaves', 'Leaf ')
SNOW_PREFIXES = ('Grass', 'Studio background', 'CAD neutral terrain')

# Illustrative autumn tone per genus: position on the autumn ramp
# (0 yellow · .35 orange · .65 red · .9 dark red · 1 olive, leaves dropped green)
AUTUMN_TONE = {'Tilia': .08, 'Larix': .02, 'Acer': .4, 'Sorbus': .7, 'Syringa': 1.0,
               'Cotoneaster': .62, 'Philadelphus': .14}
AUTUMN_RAMP = ((0, (.42, .30, .03)), (.35, (.50, .17, .02)), (.65, (.36, .045, .015)),
               (.9, (.18, .03, .015)), (1, (.11, .14, .03)))
SPRING_RAMP = ((0, (.16, .36, .045)), (1, (.27, .46, .07)))
# Blossom colour by genus (spring only)
BLOOM = {'Syringa': (.52, .28, .78), 'Philadelphus': (.95, .95, .88)}


def genus(species):
    return ((species or {}).get('latin') or '').split(' ')[0]


def is_leafless(species, season):
    """Winter drops leaves of deciduous species only (larch included)."""
    return season == 'winter' and (species or {}).get('foliage') == 'deciduous'


def leaf_materials():
    return [m for m in bpy.data.materials if m.name.startswith(LEAF_PREFIXES)]


def _ramp(nodes, stops, name):
    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.name = name
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = stops[0][0], (*stops[0][1], 1)
    els[1].position, els[1].color = stops[-1][0], (*stops[-1][1], 1)
    for pos, col in stops[1:-1]:
        els.new(pos).color = (*col, 1)
    return ramp


def _object_value(nodes, prop):
    attr = nodes.new('ShaderNodeAttribute')
    attr.attribute_type = 'OBJECT'
    attr.attribute_name = prop
    return attr


def tone_leaves(season):
    """Spring/autumn foliage colour from the per-object `seasonTone`
    (genus) plus a little per-object random spread."""
    if season not in ('spring', 'autumn'):
        return
    for m in leaf_materials():
        m.use_nodes = True
        nodes, links = m.node_tree.nodes, m.node_tree.links
        bs = nodes.get('Principled BSDF')
        old = nodes.get('Season palette')
        if old:
            nodes.remove(old)
        info = nodes.new('ShaderNodeObjectInfo')
        tone = _object_value(nodes, 'seasonTone')
        jitter = nodes.new('ShaderNodeMath'); jitter.operation = 'MULTIPLY_ADD'
        jitter.inputs[1].default_value = .16
        links.new(info.outputs['Random'], jitter.inputs[0])
        links.new(tone.outputs['Fac'], jitter.inputs[2])
        palette = _ramp(nodes, AUTUMN_RAMP if season == 'autumn' else SPRING_RAMP, 'Season palette')
        links.new(jitter.outputs[0], palette.inputs[0])
        colour = palette.outputs[0]
        if season == 'spring':
            # blossom: noise-masked flower colour on leaves of flowering shrubs
            noise = nodes.new('ShaderNodeTexNoise')
            # panicle-sized patches rather than single tinted leaves
            noise.inputs['Scale'].default_value = 4
            noise.inputs['Detail'].default_value = 2
            coords = nodes.new('ShaderNodeTexCoord')
            links.new(coords.outputs['Object'], noise.inputs['Vector'])
            mask = nodes.new('ShaderNodeMapRange')
            mask.inputs['From Min'].default_value = .52
            mask.inputs['From Max'].default_value = .55
            links.new(noise.outputs['Fac'], mask.inputs['Value'])
            amount = _object_value(nodes, 'bloomAmount')
            fac = nodes.new('ShaderNodeMath'); fac.operation = 'MULTIPLY'
            links.new(mask.outputs['Result'], fac.inputs[0])
            links.new(amount.outputs['Fac'], fac.inputs[1])
            bloom = _object_value(nodes, 'bloomColor')
            mix = nodes.new('ShaderNodeMix'); mix.data_type = 'RGBA'
            links.new(fac.outputs[0], mix.inputs['Factor'])
            links.new(colour, mix.inputs[6])
            links.new(bloom.outputs['Color'], mix.inputs[7])
            colour = mix.outputs[2]
        links.new(colour, bs.inputs['Base Color'])


def mark_instance(obj, species, season):
    """Per-object properties read by the leaf shader."""
    g = genus(species)
    obj['seasonTone'] = AUTUMN_TONE.get(g, .3) if season == 'autumn' else .5
    obj['bloomAmount'] = 1.0 if season == 'spring' and g in BLOOM else 0.0
    obj['bloomColor'] = BLOOM.get(g, (1, 1, 1))


_leafless = {}


def leafless_mesh(mesh):
    """Copy of a plant mesh without its leaf faces (cached per mesh)."""
    if mesh.name in _leafless:
        return _leafless[mesh.name]
    import bmesh
    leaf_idx = {i for i, m in enumerate(mesh.materials) if m and m.name.startswith(LEAF_PREFIXES)}
    copy = mesh.copy()
    copy.name = mesh.name + ' (winter)'
    bm = bmesh.new()
    bm.from_mesh(copy)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in leaf_idx], context='FACES')
    bm.to_mesh(copy)
    bm.free()
    _leafless[mesh.name] = copy
    return copy


def snow_material(m):
    """Replace a ground material by matte, slightly translucent snow."""
    m.use_nodes = True
    nodes, links = m.node_tree.nodes, m.node_tree.links
    nodes.clear()
    out = nodes.new('ShaderNodeOutputMaterial')
    bs = nodes.new('ShaderNodeBsdfPrincipled')
    links.new(bs.outputs[0], out.inputs['Surface'])
    bs.inputs['Base Color'].default_value = (.86, .89, .93, 1)
    bs.inputs['Roughness'].default_value = .62
    sss = bs.inputs.get('Subsurface Weight') or bs.inputs.get('Subsurface')
    if sss is not None:
        sss.default_value = .08
    coords = nodes.new('ShaderNodeTexCoord')
    noise = nodes.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 1.4
    noise.inputs['Detail'].default_value = 6
    links.new(coords.outputs['Object'], noise.inputs['Vector'])
    bump = nodes.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = .18
    bump.inputs['Distance'].default_value = .05
    links.new(noise.outputs['Fac'], bump.inputs['Height'])
    links.new(bump.outputs[0], bs.inputs['Normal'])
    m.diffuse_color = (.86, .89, .93, 1)


def snow_ground(season):
    if season != 'winter':
        return
    for m in bpy.data.materials:
        if m.name.startswith(SNOW_PREFIXES):
            snow_material(m)


def light(season, sun, world_background, environment=None):
    """Season light from the existing sun and HDR: lower, cooler winter sun
    and a greyer sky; a warm low autumn sun."""
    if season == 'winter':
        sun.data.energy, sun.data.color = 1.15, (.93, .96, 1.0)
        sun.rotation_euler[0] = 1.22          # ~20° elevation
        world_background.inputs['Strength'].default_value *= .8
        desaturate(environment, .55)
    elif season == 'autumn':
        sun.data.energy, sun.data.color = 1.6, (1.0, .87, .70)
        sun.rotation_euler[0] = 1.13          # ~25° elevation
        desaturate(environment, .85)


def desaturate(environment, saturation):
    if environment is None:
        return
    tree = bpy.context.scene.world.node_tree
    hsv = tree.nodes.new('ShaderNodeHueSaturation')
    hsv.inputs['Saturation'].default_value = saturation
    link = next(l for l in tree.links if l.from_node == environment)
    target = link.to_socket
    tree.links.remove(link)
    tree.links.new(environment.outputs['Color'], hsv.inputs['Color'])
    tree.links.new(hsv.outputs['Color'], target)
