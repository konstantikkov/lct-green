"""Author bundled botanical meshes with Blender's optional Sapling generator.
Run: blender -b --python create_render_assets.py -- /path/to/trees.blend
Sapling is needed only for asset authoring, never for rendering the saved meshes.
"""
import ast
from pathlib import Path
import sys
import bpy
import addon_utils
addon_utils.enable('add_curve_sapling', default_set=False)
import add_curve_sapling
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
assets=set()
for index, preset in enumerate(('small_maple','white_birch','japanese_maple','quaking_aspen')):
    params=ast.literal_eval((Path(add_curve_sapling.__file__).parent/'presets'/f'{preset}.py').read_text().split('\n\n')[-1].strip())
    params.update(seed=42+index,showLeaves=True,bevel=True,useArm=False,armAnim=False,leafAnim=False)
    before=set(bpy.data.objects)
    bpy.ops.curve.tree_add(**params)
    objects=set(bpy.data.objects)-before
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
        if obj.type not in ('MESH','CURVE'): continue
        mat=bpy.data.materials.get('Leaves' if 'leav' in obj.name.lower() else 'Bark')
        if not mat:
            mat=bpy.data.materials.new('Leaves' if 'leav' in obj.name.lower() else 'Bark')
            mat.diffuse_color=(.09,.22,.025,1) if 'leav' in obj.name.lower() else (.17,.085,.035,1)
        obj.data.materials.clear();obj.data.materials.append(mat)
    bpy.context.view_layer.objects.active=next(iter(objects))
    bpy.ops.object.convert(target='MESH')
    bpy.ops.object.join()
    obj=bpy.context.object
    bpy.ops.object.transform_apply(location=False,rotation=True,scale=True)
    maxwidth=max(obj.dimensions.x,obj.dimensions.y)
    factor=2.9/max(maxwidth,.1)
    minz=min(v.co.z for v in obj.data.vertices)
    for v in obj.data.vertices:
        v.co.z-=minz
        v.co*=factor
    obj.name='TREE' if index==0 else f'TREE_{index}'
    obj.location=(0,0,0)
    assets.add(obj)
    print('GENERATED',obj.name,len(obj.data.vertices))
output=sys.argv[sys.argv.index('--')+1]
bpy.data.libraries.write(output,assets,fake_user=True,compress=True)
print('ASSETS',output)
