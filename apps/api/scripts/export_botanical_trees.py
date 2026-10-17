"""Export browser trees without punching holes in trunks/branches.
Blender: --python export_botanical_trees.py -- trees.blend botanical-trees.glb
"""
import sys,math,random
import bpy
source,output=sys.argv[sys.argv.index('--')+1:]
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
with bpy.data.libraries.load(source,link=False) as (src,dst):
    dst.objects=sorted(n for n in src.objects if n=='TREE' or n.startswith('TREE_'))
for index,prototype in enumerate(dst.objects):
    old=prototype.data
    bark_faces=[];leaf_faces=[]
    for p in old.polygons:
        (leaf_faces if 'leav' in old.materials[p.material_index].name.lower() else bark_faces).append(p)
    rng=random.Random(742+index)
    budget=min(24000,len(leaf_faces))
    selected=[leaf_faces[int(i*len(leaf_faces)/budget)] for i in range(budget)] if budget else []
    gain=min(2.2,max(1,math.sqrt(len(leaf_faces)/max(budget,1))*.82))
    root=bpy.data.objects.new(f'plant_{index}_high',None);bpy.context.scene.collection.objects.link(root)
    for kind,polys in [('Bark',bark_faces),('Leaves',selected)]:
        verts=[];faces=[]
        if kind=='Bark':
            used={v for p in polys for v in p.vertices};mapping={v:i for i,v in enumerate(sorted(used))}
            verts=[tuple(old.vertices[v].co) for v in sorted(used)]
            faces=[tuple(mapping[v] for v in p.vertices) for p in polys]
        else:
            for poly in polys:
                center=poly.center;start=len(verts)
                verts.extend(tuple(center+(old.vertices[v].co-center)*gain) for v in poly.vertices)
                faces.append(tuple(range(start,len(verts))))
        # Clear stem and mature vertical proportion; XY crown coordinates stay
        # unchanged and the client normalizes their measured radius exactly.
        verts=[(x,y,z*1.45+.45*(1-math.exp(-max(0,z)/.20))) for x,y,z in verts]
        mesh=bpy.data.meshes.new(kind);mesh.from_pydata(verts,[],faces);mesh.update()
        material=bpy.data.materials.new(kind);material.diffuse_color=(.14,.09,.055,1) if kind=='Bark' else (.065+index*.012,.18+index*.012,.025,1)
        material.use_nodes=True;bs=material.node_tree.nodes.get('Principled BSDF');bs.inputs['Base Color'].default_value=material.diffuse_color;bs.inputs['Roughness'].default_value=.9
        mesh.materials.append(material)
        obj=bpy.data.objects.new(f'{kind}_{index}',mesh);bpy.context.scene.collection.objects.link(obj);obj.parent=root
        if kind=='Bark':
            for p in mesh.polygons:p.use_smooth=True
            if len(mesh.polygons)>28000:
                bpy.context.view_layer.objects.active=obj;obj.select_set(True)
                mod=obj.modifiers.new('Continuous branch simplification','DECIMATE');mod.ratio=28000/len(mesh.polygons)
                bpy.ops.object.modifier_apply(modifier=mod.name);obj.select_set(False)
        print(index,kind,len(obj.data.polygons),flush=True)
bpy.ops.export_scene.gltf(filepath=output,export_format='GLB',export_lights=False,export_cameras=False)
