"""Enforce browser geometry budgets, including disconnected leaf surfaces."""
import sys,math
import bpy
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
source,output=sys.argv[sys.argv.index('--')+1:]
bpy.ops.import_scene.gltf(filepath=source)
for obj in list(bpy.context.scene.objects):
    if obj.type!='MESH':continue
    level=obj.name.rsplit('_',1)[-1]
    budget={'high':40000,'medium':8000,'low':1200}.get(level)
    old=obj.data
    if not budget or len(old.polygons)<=budget:continue
    verts=[];faces=[];slots=[]
    gain=min(2.4,max(1,math.sqrt(len(old.polygons)/budget)*.75))
    for i in range(budget):
        poly=old.polygons[int(i*len(old.polygons)/budget)]
        leafy='leav' in old.materials[poly.material_index].name.lower()
        center=poly.center
        start=len(verts)
        verts.extend(tuple(center+(old.vertices[v].co-center)*(gain if leafy else 1)) for v in poly.vertices)
        faces.append(tuple(range(start,len(verts))));slots.append(poly.material_index)
    mesh=bpy.data.meshes.new(obj.name);mesh.from_pydata(verts,[],faces);mesh.update()
    for mat in old.materials:mesh.materials.append(mat)
    for p,slot in zip(mesh.polygons,slots):p.material_index=slot
    obj.data=mesh
    print(obj.name,len(mesh.polygons),flush=True)
bpy.ops.export_scene.gltf(filepath=output,export_format='GLB',export_lights=False,export_cameras=False)
