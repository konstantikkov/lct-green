"""Standalone Blender script; only Blender's bundled Python is required."""
import argparse
import json
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--scene', default='scene.json')
    p.add_argument('--output', default='render.png')
    p.add_argument('--samples', type=int, default=128)
    p.add_argument('--width', type=int, default=1920)
    p.add_argument('--height', type=int, default=1080)
    p.add_argument('--azimuth', type=float, default=225)
    p.add_argument('--target-x', type=float)
    p.add_argument('--target-y', type=float)
    p.add_argument('--target-z', type=float, default=2)
    p.add_argument('--exposure', type=float, default=-1)
    p.add_argument('--elevation', type=float, default=23)
    p.add_argument('--distance', type=float, default=.8)
    p.add_argument('--blend')
    p.add_argument('--assets')
    a = p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    data = json.loads(Path(a.scene).read_text())
    rng = random.Random(data['seed'])
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = a.samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = a.width, a.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
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
    bark = material('Bark', (.22,.12,.055), 8)
    leaves = [material('Leaf '+str(i), c, 5, .55) for i,c in enumerate([
        (.13,.29,.045),(.22,.38,.065),(.30,.40,.09),(.095,.23,.055)])]
    for m in leaves:
        bs = m.node_tree.nodes.get('Principled BSDF')
        socket = bs.inputs.get('Subsurface Weight') or bs.inputs.get('Subsurface')
        if socket is not None: socket.default_value = .08

    def mesh(name, verts, faces, mat):
        geo = bpy.data.meshes.new(name)
        geo.from_pydata(verts, [], faces)
        geo.update()
        obj = bpy.data.objects.new(name, geo)
        scene.collection.objects.link(obj)
        obj.data.materials.append(mat)
        return obj

    mesh('Landscape ground', [(-span*2,-span*2,-.08),(span*2,-span*2,-.08),
        (span*2,span*2,-.08),(-span*2,span*2,-.08)], [(0,1,2,3)], grass)
    for surface in data['surfaces']:
        cat = surface['category']
        verts, faces = [], []
        z = .04 if cat != 'GREEN_AREA' else 0
        for tri in surface['triangles']:
            start = len(verts)
            verts.extend((x-ox,y-oy,z) for x,y in tri)
            faces.append((start,start+1,start+2))
        obj = mesh(surface['layer'], verts, faces, wall if cat=='BUILDING' else
                   asphalt if cat in ('ROAD','PARKING') else grass if cat=='GREEN_AREA' else paving)
        if cat == 'BUILDING':
            mod = obj.modifiers.new('Survey footprint / illustrative height','SOLIDIFY')
            mod.thickness = float(surface['height'])
            mod.offset = 1
            bevel = obj.modifiers.new('Facade edges', 'BEVEL')
            bevel.width = .08
            bevel.segments = 2

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

    for i,item in enumerate(data['placements']):
        kind=item['kind']; proto=plant(kind,i%4)
        obj=bpy.data.objects.new(item['id'],proto.data)
        scene.collection.objects.link(obj)
        x,y=item['position']; obj.location=(x-ox,y-oy,.06)
        r=float(item['crownRadiusM'])
        obj.scale=(r/1.45,r/1.45,r/1.45)
        obj.rotation_euler.z=rng.random()*math.tau
    world=bpy.data.worlds.new('Physical sky');scene.world=world;world.use_nodes=True
    nodes=world.node_tree.nodes
    sky=nodes.new('ShaderNodeTexSky');sky.sky_type='NISHITA'
    sky.sun_elevation=math.radians(25);sky.sun_rotation=math.radians(130)
    sky.altitude=.15
    nodes.get('Background').inputs['Strength'].default_value=.18
    world.node_tree.links.new(sky.outputs[0],nodes.get('Background').inputs['Color'])
    bpy.ops.object.light_add(type='SUN', location=(0,0,50))
    sun=bpy.context.object;sun.data.energy=2.5;sun.data.angle=math.radians(2)
    sun.rotation_euler=(math.radians(55),math.radians(-15),math.radians(-35))
    az,el=math.radians(a.azimuth),math.radians(a.elevation)
    dist=span*max(a.distance,.25)
    target=Vector(((a.target_x-ox) if a.target_x is not None else 0,
                   (a.target_y-oy) if a.target_y is not None else 0,a.target_z))
    bpy.ops.object.camera_add(location=target+Vector((dist*math.cos(el)*math.cos(az),dist*math.cos(el)*math.sin(az),dist*math.sin(el))))
    cam=bpy.context.object;cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler()
    cam.data.lens=36;cam.data.clip_end=max(span*10,1000);scene.camera=cam
    if a.blend: bpy.ops.wm.save_as_mainfile(filepath=str(Path(a.blend).resolve()))
    try:
        bpy.ops.render.render(write_still=True)
    except RuntimeError as exc:
        if 'denois' not in str(exc).lower():
            raise
        # Some Linux distribution builds omit OpenImageDenoise.
        scene.cycles.use_denoising = False
        bpy.ops.render.render(write_still=True)

if __name__ == '__main__':
    main()
