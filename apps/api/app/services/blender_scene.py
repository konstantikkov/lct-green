"""Standalone Blender script; only Blender's bundled Python is required."""
import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def use_gpu(scene):
    """Select the first available GPU backend; CPU when none is usable."""
    prefs = bpy.context.preferences.addons['cycles'].preferences
    for kind in ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI'):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        prefs.refresh_devices()
        if any(d.type == kind for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == kind
            scene.cycles.device = 'GPU'
            return kind
    return 'CPU'


def place_panorama(scene, cam, a, ox, oy):
    """Equirectangular camera at eye height above the ground under the point.

    The downward ray skips plantings and fence/light rods; passing through a
    building roof marks the point as inside a building.
    """
    x = a.target_x-ox if a.target_x is not None else 0
    y = a.target_y-oy if a.target_y is not None else 0
    depsgraph = bpy.context.evaluated_depsgraph_get()
    origin, ground, inside = Vector((x, y, 10000)), 0., False
    for _ in range(256):
        hit, loc, _, _, obj, _ = scene.ray_cast(depsgraph, origin, Vector((0, 0, -1)))
        if not hit:
            break
        obj = obj.original
        if obj.get('cadCategory') == 'BUILDING':
            inside = True
        elif not obj.get('plantingId') and not obj.name.startswith(('CAD fence', 'CAD light', 'Lawn blades')):
            ground = loc.z
            break
        origin = loc - Vector((0, 0, 1e-3))
    cam.location = (x, y, ground+a.eye_height)
    # Rotating the default -Z view by 90° about X faces +Y; Z turns clockwise.
    cam.rotation_euler = (math.radians(90), 0, -math.radians(a.heading))
    cam.data.type = 'PANO'
    if hasattr(cam.data, 'panorama_type'):
        cam.data.panorama_type = 'EQUIRECTANGULAR'
    else:
        cam.data.cycles.panorama_type = 'EQUIRECTANGULAR'
    cam.data.dof.use_dof = False
    cam.data.clip_start = .02
    # The neutral ground continues towards the horizon instead of ending
    # at a visible edge; it adds no source objects.
    background = scene.objects.get('Studio background')
    if background:
        background.scale = (12, 12, 1)
        # Seen edge-on, the pale studio grey reads as water and its specular
        # lobe mirrors the sky; a dark matte earth tone reads as distant ground.
        for m in background.data.materials:
            nodes = m.node_tree.nodes if m and m.node_tree else []
            for node in nodes:
                if node.type == 'VALTORGB':
                    node.color_ramp.elements[0].color = (.07, .075, .05, 1)
                    node.color_ramp.elements[-1].color = (.13, .14, .09, 1)
                elif node.type == 'BSDF_PRINCIPLED' and 'Specular IOR Level' in node.inputs:
                    node.inputs['Specular IOR Level'].default_value = 0
    return {'eye': [x+ox, y+oy, ground+a.eye_height], 'groundZ': ground, 'heading': a.heading,
            'eyeHeight': a.eye_height, 'insideBuilding': inside}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--scene', default='scene.json')
    p.add_argument('--output', default='render.png')
    p.add_argument('--samples', type=int, default=128)
    p.add_argument('--width', type=int, default=1920)
    p.add_argument('--height', type=int, default=1080)
    p.add_argument('--azimuth', type=float, default=315)
    p.add_argument('--target-x', type=float)
    p.add_argument('--target-y', type=float)
    p.add_argument('--target-z', type=float, default=None)
    p.add_argument('--exposure', type=float, default=-.2)
    p.add_argument('--elevation', type=float, default=48)
    p.add_argument('--distance', type=float, default=1.25)
    p.add_argument('--blend')
    p.add_argument('--gltf')
    p.add_argument('--preview-only', action='store_true')
    p.add_argument('--assets')
    p.add_argument('--detail', choices=['presentation','cad'], default='cad')
    p.add_argument('--view', choices=['aerial','pedestrian','panorama'], default='aerial')
    # Panorama: --target-x/--target-y is the standing point (CAD metres),
    # --heading the compass bearing at the image centre (0 = +Y, clockwise).
    p.add_argument('--heading', type=float, default=0)
    p.add_argument('--eye-height', type=float, default=1.6)
    p.add_argument('--grass-radius', type=float, default=0)
    # Years after planting (growth.py); omitted = mature, as checked and exported.
    p.add_argument('--age-years', type=float, default=None)
    p.add_argument('--device', choices=['CPU','AUTO'], default='CPU')
    # Tour: JSON list of {x, y, heading, output}; one scene, one render per point.
    p.add_argument('--points')
    a = p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    data = json.loads(Path(a.scene).read_text())
    rng = random.Random(data['seed'])
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    device = use_gpu(scene) if a.device == 'AUTO' else 'CPU'
    scene.cycles.samples = a.samples
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 8
    scene.cycles.diffuse_bounces = 4
    scene.cycles.glossy_bounces = 3
    scene.cycles.adaptive_threshold = .03
    scene.render.resolution_x, scene.render.resolution_y = a.width, a.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'JPEG' if Path(a.output).suffix.lower() in ('.jpg','.jpeg') else 'PNG'
    scene.render.image_settings.quality = 92
    scene.render.filepath = str(Path(a.output).resolve())
    scene.view_settings.exposure = a.exposure
    scene.view_settings.view_transform = 'AgX' if bpy.app.version >= (4, 0, 0) else 'Filmic'
    lo, hi = data['extents']['min'], data['extents']['max']
    ox, oy = (lo[0]+hi[0])/2, (lo[1]+hi[1])/2
    span = max(hi[0]-lo[0], hi[1]-lo[1], 20)

    def material(name, color, scale=1, roughness=.85):
        m = bpy.data.materials.new(name)
        m.diffuse_color = (*color, 1)
        m.use_nodes = True
        n, links = m.node_tree.nodes, m.node_tree.links
        bs = n.get('Principled BSDF')
        bs.inputs['Roughness'].default_value = roughness
        noise = n.new('ShaderNodeTexNoise')
        noise.inputs['Scale'].default_value = scale
        noise.inputs['Detail'].default_value = 3
        coords = n.new('ShaderNodeTexCoord')
        links.new(coords.outputs['Object'], noise.inputs['Vector'])
        ramp = n.new('ShaderNodeValToRGB')
        ramp.color_ramp.elements[0].color = (*[c*.55 for c in color], 1)
        ramp.color_ramp.elements[1].color = (*color, 1)
        links.new(noise.outputs['Fac'], ramp.inputs[0])
        links.new(ramp.outputs[0], bs.inputs['Base Color'])
        bump = n.new('ShaderNodeBump')
        bump.inputs['Strength'].default_value = .23
        bump.inputs['Distance'].default_value = .035
        links.new(noise.outputs['Fac'], bump.inputs['Height'])
        links.new(bump.outputs[0], bs.inputs['Normal'])
        return m

    grass = material('Grass / fine ground texture', (.10,.21,.035), 65)
    asphalt = material('Asphalt', (.12,.135,.15), 110)
    paving = material('Warm stone', (.55,.52,.44), 65)
    wall = material('Mineral facade', (.67,.64,.56), 20)
    rubber = material('Warm ochre play surface',(.39,.20,.10),70)
    bark = material('Bark', (.22,.12,.055), 8)
    leaves = [material('Leaf '+str(i), c, 5, .55) for i,c in enumerate([
        (.13,.29,.045),(.22,.38,.065),(.30,.40,.09),(.095,.23,.055)])]
    for m in leaves:
        bs = m.node_tree.nodes.get('Principled BSDF')
        socket = bs.inputs.get('Subsurface Weight') or bs.inputs.get('Subsurface')
        if socket is not None: socket.default_value = .08

    # Photographic, CC0 ground maps are bundled and loaded entirely offline.
    asset_root = Path(a.scene).resolve().parent
    ground_image = asset_root/'ground_diff.jpg'
    if ground_image.is_file():
        nodes, links = grass.node_tree.nodes, grass.node_tree.links
        bs = nodes.get('Principled BSDF')
        coords = nodes.new('ShaderNodeTexCoord')
        mapping = nodes.new('ShaderNodeVectorMath'); mapping.operation = 'SCALE'
        mapping.inputs[3].default_value = .2
        links.new(coords.outputs['Object'],mapping.inputs[0])
        for filename, socket in [('ground_diff.jpg','Base Color'),('ground_rough.jpg','Roughness')]:
            path = asset_root/filename
            if path.is_file():
                tex = nodes.new('ShaderNodeTexImage'); tex.image = bpy.data.images.load(str(path))
                if socket == 'Roughness': tex.image.colorspace_settings.name = 'Non-Color'
                links.new(mapping.outputs[0],tex.inputs['Vector'])
                if socket == 'Base Color':
                    tint = nodes.new('ShaderNodeHueSaturation')
                    tint.inputs['Hue'].default_value=.62
                    tint.inputs['Saturation'].default_value=1.2
                    tint.inputs['Value'].default_value=1.15
                    links.new(tex.outputs['Color'],tint.inputs['Color'])
                    links.new(tint.outputs['Color'],bs.inputs[socket])
                else:
                    links.new(tex.outputs['Color'],bs.inputs[socket])
        path = asset_root/'ground_normal.jpg'
        if path.is_file():
            tex = nodes.new('ShaderNodeTexImage'); tex.image = bpy.data.images.load(str(path))
            tex.image.colorspace_settings.name = 'Non-Color'
            normal = nodes.new('ShaderNodeNormalMap');normal.inputs['Strength'].default_value = .65
            links.new(mapping.outputs[0],tex.inputs['Vector'])
            links.new(tex.outputs['Color'],normal.inputs['Color'])
            links.new(normal.outputs[0],bs.inputs['Normal'])

    nodes, links = wall.node_tree.nodes, wall.node_tree.links
    bs = nodes.get('Principled BSDF')
    brick=nodes.new('ShaderNodeTexBrick')
    brick.inputs['Color1'].default_value=(.32,.22,.14,1)
    brick.inputs['Color2'].default_value=(.53,.43,.29,1)
    brick.inputs['Mortar'].default_value=(.29,.27,.23,1)
    brick.inputs['Scale'].default_value=2.0
    brick.inputs['Mortar Size'].default_value=.012
    brick.inputs['Brick Width'].default_value=.5
    brick.inputs['Row Height'].default_value=.23
    coords=nodes.new('ShaderNodeTexCoord')
    xyz=nodes.new('ShaderNodeSeparateXYZ');links.new(coords.outputs['Object'],xyz.inputs[0])
    geometry=nodes.new('ShaderNodeNewGeometry');normal=nodes.new('ShaderNodeSeparateXYZ')
    links.new(geometry.outputs['Normal'],normal.inputs[0])
    absolute=nodes.new('ShaderNodeMath');absolute.operation='ABSOLUTE';links.new(normal.outputs['X'],absolute.inputs[0])
    uv=nodes.new('ShaderNodeMixRGB');links.new(absolute.outputs[0],uv.inputs[0])
    links.new(xyz.outputs['X'],uv.inputs[1]);links.new(xyz.outputs['Y'],uv.inputs[2])
    combine=nodes.new('ShaderNodeCombineXYZ');links.new(uv.outputs[0],combine.inputs[0]);links.new(xyz.outputs['Z'],combine.inputs[1])
    links.new(combine.outputs[0],brick.inputs['Vector'])
    links.new(brick.outputs['Color'],bs.inputs['Base Color'])
    bump=nodes.new('ShaderNodeBump');bump.inputs['Distance'].default_value=.018
    links.new(brick.outputs['Fac'],bump.inputs['Height']);links.new(bump.outputs[0],bs.inputs['Normal'])

    def mesh(name, verts, faces, mat):
        geo = bpy.data.meshes.new(name)
        geo.from_pydata(verts, [], faces)
        geo.update()
        obj = bpy.data.objects.new(name, geo)
        scene.collection.objects.link(obj)
        obj.data.materials.append(mat)
        return obj

    mesh('Studio background', [(-span*2,-span*2,-.08),(span*2,-span*2,-.08),
        (span*2,span*2,-.08),(-span*2,span*2,-.08)], [(0,1,2,3)], material('Studio background',(.30,.32,.30),80))
    for surface_index,surface in enumerate(data['surfaces']):
        cat = surface['category']
        verts, faces = [], []
        z = float(surface.get('baseZ',0)) + (float(surface.get('height',.02)) if cat!='BUILDING' else float(surface['height']))
        for tri in surface['triangles']:
            start = len(verts)
            verts.extend((x-ox,y-oy,z) for x,y in tri)
            faces.append((start,start+1,start+2))
        # Boundary walls preserve concave footprints and courtyard holes; do
        # not extrude each triangle or fabricate windows from floor guesses.
        base=float(surface.get('baseZ',0))
        if cat=='BUILDING':
            for tri in surface['triangles']:
                start=len(verts);verts.extend((x-ox,y-oy,base) for x,y in tri);faces.append((start+2,start+1,start))
        for ring in surface.get('outlines',[]):
            for a0,b0 in zip(ring,ring[1:]):
                start=len(verts);verts.extend([(a0[0]-ox,a0[1]-oy,base),(b0[0]-ox,b0[1]-oy,base),(b0[0]-ox,b0[1]-oy,z),(a0[0]-ox,a0[1]-oy,z)])
                faces.append((start,start+1,start+2,start+3))
        obj = mesh(surface['layer'], verts, faces, rubber if surface.get('style')=='play' else wall if cat=='BUILDING' else
                   asphalt if cat in ('ROAD','PARKING') else grass if cat=='GREEN_AREA' else paving)
        obj['cadLayer']=surface['layer'];obj['cadCategory']=cat
        if cat == 'BUILDING':
            facade=wall.copy()
            palette=[((.48,.42,.32,1),(.65,.59,.47,1)),((.30,.095,.055,1),(.47,.19,.10,1)),((.25,.29,.28,1),(.41,.43,.39,1))]
            colors=palette[surface_index%len(palette)]
            brick_node=next(n for n in facade.node_tree.nodes if n.type=='TEX_BRICK')
            brick_node.inputs['Color1'].default_value=colors[0];brick_node.inputs['Color2'].default_value=colors[1]
            obj.data.materials.clear();obj.data.materials.append(facade)
            import bmesh
            bm=bmesh.new();bm.from_mesh(obj.data);bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.00001);bm.to_mesh(obj.data);bm.free()
            roof=material('Source roof',(.38,.39,.37),45);obj.data.materials.append(roof)
            for face in obj.data.polygons:
                if face.normal.z>.5:face.material_index=1
            obj['cadLayer']=surface['layer'];obj['cadCategory']=cat
            obj['heightSource']=surface.get('heightSource','unknown')
            bevel = obj.modifiers.new('Facade edges', 'BEVEL')
            bevel.width = .015
            bevel.segments = 3

    native={}
    for terrain in data.get('terrainFaces',[]):
        cat=terrain.get('category','UNKNOWN');key=(terrain['layer'],cat)
        verts,faces=native.setdefault(key,([],[]));start=len(verts)
        verts.extend((x-ox,y-oy,z+.001) for x,y,z in terrain['vertices']);faces.append(tuple(range(start,len(verts))))
    for (layer,cat),(verts,faces) in native.items():
        mat=wall if cat=='BUILDING' else asphalt if cat in ('ROAD','PARKING') else paving if cat=='SIDEWALK' else grass if cat=='GREEN_AREA' else material('CAD neutral terrain',(.38,.34,.25),2)
        obj=mesh('CAD source face '+layer,verts,faces,mat)
        obj['cadCategory']=cat if cat in ('BUILDING','ROAD','PARKING','SIDEWALK','GREEN_AREA') else 'UNKNOWN';obj['cadLayer']=layer;obj['sourceTerrain']=cat!='BUILDING'

    # Four linked, deterministic botanical meshes: visible branches and thousands
    # of separate leaf blades, rather than opaque spheres representing crowns.
    prototypes = {}
    bundled = Path(a.scene).resolve().parent/'trees.blend'
    a.assets = a.assets or (str(bundled) if bundled.is_file() else None)
    if a.assets:
        with bpy.data.libraries.load(str(Path(a.assets).resolve()), link=False) as (src, dst):
            dst.objects = [n for n in src.objects if n in ('TREE','SHRUB') or n.startswith(('TREE_', 'SHRUB_'))]
        for obj in dst.objects:
            if obj:
                parts = obj.name.split('_')
                prototypes[(parts[0], int(parts[1]) if len(parts)>1 and parts[1].isdigit() else 0)] = obj
                for m in obj.data.materials:
                    if m and m.name.startswith('Leaves'):
                        m.use_nodes = True
                        bs = m.node_tree.nodes.get('Principled BSDF')
                        bs.inputs['Base Color'].default_value = (.065,.18,.02,1)
                        bs.inputs['Roughness'].default_value = .48
                        if a.detail=='presentation' and not m.node_tree.nodes.get('Season palette'):
                            nodes=m.node_tree.nodes;links=m.node_tree.links
                            info=nodes.new('ShaderNodeObjectInfo');ramp=nodes.new('ShaderNodeValToRGB');ramp.name='Season palette'
                            ramp.color_ramp.elements[0].color=(.035,.12,.015,1)
                            ramp.color_ramp.elements[1].color=(.32,.075,.015,1)
                            ramp.color_ramp.elements.new(.70).color=(.13,.24,.035,1)
                            ramp.color_ramp.elements.new(.9).color=(.42,.25,.035,1)
                            links.new(info.outputs['Random'],ramp.inputs[0]);links.new(ramp.outputs[0],bs.inputs['Base Color'])
                    elif m:
                        m.use_nodes = True
                        m.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = (.16,.08,.03,1)
    def plant(kind, variant):
        key = (kind,variant)
        if key in prototypes: return prototypes[key]
        if (kind,0) in prototypes and a.assets: return prototypes[(kind,0)]
        verts, faces, mids = [], [], []
        def tube(start,end,r):
            axis = Vector(end)-Vector(start)
            u = axis.cross(Vector((0,0,1)))
            if u.length < .01: u = axis.cross(Vector((0,1,0)))
            u.normalize(); v = axis.normalized().cross(u)
            base = len(verts)
            for pt, radius in ((start,r),(end,r*.45)):
                for j in range(6):
                    q=Vector(pt)+radius*(u*math.cos(j*math.tau/6)+v*math.sin(j*math.tau/6))
                    verts.append(tuple(q))
            for j in range(6):
                faces.append((base+j,base+(j+1)%6,base+(j+1)%6+6,base+j+6)); mids.append(0)
        tree = kind == 'TREE'
        tube((0,0,0),(0,0,1.6 if tree else .3),.065 if tree else .025)
        for j in range(34 if tree else 16):
            az = rng.random()*math.tau
            radius = rng.uniform(.35,.95)
            z = rng.uniform(1.0,2.5) if tree else rng.uniform(.2,.8)
            end=(math.cos(az)*radius,math.sin(az)*radius,z)
            tube((0,0,.8 if tree else .15),end,.025 if tree else .012)
            for k in range(300 if tree else 140):
                # Dense leaf clusters retain gaps for physically traced sunlight.
                theta=rng.random()*math.tau; zz=rng.uniform(-1,1)
                rr=rng.random()**(1/3)*.5
                center=Vector(end)+Vector((rr*math.sqrt(1-zz*zz)*math.cos(theta),rr*math.sqrt(1-zz*zz)*math.sin(theta),rr*zz))
                u=Vector((rng.uniform(-1,1),rng.uniform(-1,1),rng.uniform(-.4,.8))).normalized()*.105
                v=u.cross(Vector((.3,.1,1))).normalized()*.045
                base=len(verts)
                verts.extend(tuple(q) for q in (center-u,center+v,center+u,center-v))
                faces.append((base,base+1,base+2,base+3));mids.append(1+rng.randrange(len(leaves)))
        obj=mesh('Botanical '+kind+str(variant),verts,faces,bark)
        for m in leaves: obj.data.materials.append(m)
        for poly,mid in zip(obj.data.polygons,mids): poly.material_index=mid
        # Prototypes are detached; only linked instances enter the scene.
        scene.collection.objects.unlink(obj)
        prototypes[key]=obj
        return obj

    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from growth import scale_at
    for i,item in enumerate([] if a.preview_only else data['placements']):
        kind=item['kind']; proto=plant(kind,i%4)
        obj=bpy.data.objects.new(item['id'],proto.data)
        obj['plantingId']=item['id']
        scene.collection.objects.link(obj)
        x,y=item['position']; obj.location=(x-ox,y-oy,.06)
        r=float(item['crownRadiusM'])
        # crown (XY) and height (Z) ratios of the illustrative growth model
        crown,height=scale_at(data.get('species',{}).get(item.get('speciesId')),a.age_years)
        obj.scale=(r/1.45*crown,r/1.45*crown,r/1.45*height)
        obj.rotation_euler.z=rng.random()*math.tau
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from blender_landscape import enrich
    enrich({**data,'placements':[]} if a.preview_only else data,ox,oy,presentation=False)
    from source_materials import apply_source_materials
    apply_source_materials(asset_root)
    world=bpy.data.worlds.new('Physical sky');scene.world=world;world.use_nodes=True
    nodes=world.node_tree.nodes
    sky=nodes.new('ShaderNodeTexSky');sky.sky_type='NISHITA'
    sky.sun_elevation=math.radians(25);sky.sun_rotation=math.radians(130)
    sky.altitude=.15
    nodes.get('Background').inputs['Strength'].default_value=.18
    world.node_tree.links.new(sky.outputs[0],nodes.get('Background').inputs['Color'])
    hdr=asset_root/'environment.hdr'
    if hdr.is_file():
        environment=nodes.new('ShaderNodeTexEnvironment')
        environment.image=bpy.data.images.load(str(hdr))
        world.node_tree.links.new(environment.outputs['Color'],nodes.get('Background').inputs['Color'])
        nodes.get('Background').inputs['Strength'].default_value=.65
    bpy.ops.object.light_add(type='SUN', location=(0,0,50))
    sun=bpy.context.object;sun.data.energy=1.8;sun.data.angle=math.radians(2.5)
    sun.rotation_euler=(math.radians(55),math.radians(-15),math.radians(-35))
    az,el=math.radians(a.azimuth),math.radians(a.elevation)
    bpy.context.view_layer.update()
    corners=[obj.matrix_world@Vector(c) for obj in scene.objects if obj.type=='MESH' and obj.name!='Studio background' for c in obj.bound_box]
    low=min((p.z for p in corners),default=0);high=max((p.z for p in corners),default=4)
    target=Vector(((a.target_x-ox) if a.target_x is not None else 0,
                   (a.target_y-oy) if a.target_y is not None else 0,a.target_z if a.target_z is not None else (low+high)/2))
    direction=Vector((math.cos(el)*math.cos(az),math.cos(el)*math.sin(az),math.sin(el)))
    right=Vector((-direction.y,direction.x,0)).normalized();up=right.cross(-direction).normalized()
    tanh=36/(2*46);tanv=tanh/(a.width/a.height)
    fit=max((max(abs((p-target).dot(right))/tanh,abs((p-target).dot(up))/tanv)+(p-target).dot(direction) for p in corners),default=span)
    dist=max(span*.5,fit)*max(a.distance,.25)
    bpy.ops.object.camera_add(location=target+Vector((dist*math.cos(el)*math.cos(az),dist*math.cos(el)*math.sin(az),dist*math.sin(el))))
    cam=bpy.context.object;cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler()
    if a.view == 'pedestrian':
        eye_distance=max(12,span*max(.85,a.distance*.8))
        cam.location=target+Vector((eye_distance*math.cos(az),eye_distance*math.sin(az),1.7-target.z))
        cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler()
    cam.data.lens=46;cam.data.clip_end=max(span*10,1000);scene.camera=cam
    cam.data.dof.use_dof=True;cam.data.dof.focus_distance=(target-cam.location).length;cam.data.dof.aperture_fstop=11
    panorama=place_panorama(scene,cam,a,ox,oy) if a.view=='panorama' and not a.points else None
    if panorama and a.grass_radius>0:
        from blender_grass import scatter_lawn
        panorama['lawnClumps']=scatter_lawn(data,ox,oy,tuple(cam.location)[:2],a.grass_radius,seed=data.get('seed',42))
    try:scene.view_settings.look='AgX - Medium High Contrast'
    except (TypeError,ValueError):pass
    import json as _json
    manifest={'sourceOnly':True,'sourceBuildingCount':sum(s['category']=='BUILDING' for s in data['surfaces']),
              'sourceSurfaceCount':len(data['surfaces']),
              'texturedMaterials':sum(m.use_nodes and any(n.type=='TEX_IMAGE' for n in m.node_tree.nodes) for m in bpy.data.materials),
              'detail':a.detail,'view':a.view,'sourcePlantingCount':len(data['placements']),
              'assumptions':data.get('assumptions',[]),'sourcePlantingCoordinates':[p['position'] for p in data['placements']],
              'illustrativeObjects':[o.name for o in scene.objects if o.name.startswith('Illustrative')],
              'cameraPosition':list(cam.location),'meshObjects':sum(o.type=='MESH' for o in scene.objects),
              'device':device,'panorama':panorama,'ageYears':a.age_years}
    if a.points:
        render_tour(scene,cam,a,ox,oy,data,manifest)
        return
    Path(a.output).with_suffix('.manifest.json').write_text(_json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    if a.gltf:
        from preview_materials import prepare_materials
        prepare_materials(asset_root)
        # The studio shadow receiver is a render helper, never a CAD object
        # in the downloaded 3D scene.
        for obj in scene.objects:obj.select_set(obj.name!='Studio background')
        bpy.ops.export_scene.gltf(filepath=str(Path(a.gltf).resolve()),export_format='GLB',use_selection=True,
                                 export_extras=True,export_lights=False,export_cameras=False)
        if a.preview_only:return
    if a.blend:
        bpy.ops.file.pack_all()
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(a.blend).resolve()))
    render_still(scene)


def render_still(scene):
    try:
        bpy.ops.render.render(write_still=True)
    except RuntimeError as exc:
        if 'denois' not in str(exc).lower():
            raise
        # Some Linux distribution builds omit OpenImageDenoise.
        scene.cycles.use_denoising = False
        bpy.ops.render.render(write_still=True)


def render_tour(scene, cam, a, ox, oy, data, manifest):
    """Render every tour point from the already built scene.

    Each image appears atomically (rendered under a temporary name, then
    renamed) so the API can serve finished points while later ones render.
    """
    from blender_grass import scatter_lawn
    points = json.loads(Path(a.points).read_text(encoding='utf-8'))
    for i, point in enumerate(points):
        print(f'PANORAMA {i+1}/{len(points)}', flush=True)
        Path(a.points).with_name('tour.progress').write_text(f'{i}/{len(points)}')
        a.target_x, a.target_y, a.heading = point['x'], point['y'], point.get('heading', 0)
        info = place_panorama(scene, cam, a, ox, oy)
        if a.grass_radius > 0:
            info['lawnClumps'] = scatter_lawn(data, ox, oy, tuple(cam.location)[:2], a.grass_radius, seed=data.get('seed', 42)+i)
        out = Path(point['output']).resolve()
        partial = out.with_name('partial_'+out.name)
        scene.render.filepath = str(partial)
        render_still(scene)
        out.with_suffix('.manifest.json').write_text(json.dumps(
            {**manifest, 'panorama': info, 'cameraPosition': list(cam.location)}, ensure_ascii=False), encoding='utf-8')
        os.replace(partial, out)

if __name__ == '__main__':
    main()
