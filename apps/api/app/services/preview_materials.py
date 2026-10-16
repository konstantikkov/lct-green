"""Convert the offline scene to portable glTF PBR materials for the editor."""
from pathlib import Path
import bpy


def prepare_materials(asset_root):
    for material in bpy.data.materials:
        if not material.use_nodes:continue
        old=material.node_tree.nodes.get('Principled BSDF')
        if not old:continue
        color=tuple(old.inputs['Base Color'].default_value)
        rough=float(old.inputs['Roughness'].default_value)
        metal=float(old.inputs['Metallic'].default_value)
        brick=next((n for n in material.node_tree.nodes if n.type=='TEX_BRICK'),None)
        if brick:color=tuple(brick.inputs['Color1'].default_value)
        nodes=material.node_tree.nodes;links=material.node_tree.links;nodes.clear()
        bs=nodes.new('ShaderNodeBsdfPrincipled');out=nodes.new('ShaderNodeOutputMaterial')
        links.new(bs.outputs[0],out.inputs['Surface'])
        bs.inputs['Base Color'].default_value=color;bs.inputs['Roughness'].default_value=rough;bs.inputs['Metallic'].default_value=metal
        material.diffuse_color=color
        material.use_backface_culling=False
        if material.name.startswith('Grass') and (Path(asset_root)/'ground_diff.jpg').is_file():
            tex=nodes.new('ShaderNodeTexImage');tex.image=bpy.data.images.load(str(Path(asset_root)/'ground_diff.jpg'),check_existing=True)
            links.new(tex.outputs['Color'],bs.inputs['Base Color'])
    # Geometry is already in meters about a local origin. Generate stable UVs
    # for the photographic floor instead of baking world-coordinate shaders.
    for obj in bpy.context.scene.objects:
        if obj.type!='MESH':continue
        mesh=obj.data
        if mesh.uv_layers:continue
        uv=mesh.uv_layers.new(name='UVMap')
        for face in mesh.polygons:
            axis=max(range(3),key=lambda a:abs(face.normal[a]))
            for li in face.loop_indices:
                v=mesh.vertices[mesh.loops[li].vertex_index].co
                uv.data[li].uv=(v[(axis+1)%3]/5,v[(axis+2)%3]/5)
