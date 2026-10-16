"""Author reusable glTF vegetation LODs from the bundled Blender meshes."""
import sys
from pathlib import Path
import bpy
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
source,output=sys.argv[sys.argv.index('--')+1:]
with bpy.data.libraries.load(source,link=False) as (src,dst):dst.objects=[n for n in src.objects if n=='TREE' or n.startswith('TREE_')]
for index,prototype in enumerate(dst.objects):
    for label,faces in [('high',40000),('medium',8000),('low',1200)]:
        obj=bpy.data.objects.new(f'plant_{index}_{label}',prototype.data.copy());bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active=obj;obj.select_set(True)
        # Keep leaf surfaces until the material-aware sampling pass.
        obj.select_set(False)
        print(obj.name,len(obj.data.polygons),flush=True)
bpy.ops.export_scene.gltf(filepath=output,export_format='GLB',export_lights=False,export_cameras=False)
