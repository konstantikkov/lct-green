"""Photographic PBR on existing CAD surfaces. Adds no scene objects."""
from pathlib import Path
import bpy


def apply_source_materials(asset_root):
    root=Path(asset_root)
    for obj in bpy.context.scene.objects:
        if obj.type!='MESH':continue
        if not any(m and m.name.startswith(('Mineral facade','Asphalt','Warm stone','Source roof','Grass')) for m in obj.data.materials):continue
        if obj.modifiers:
            bpy.context.view_layer.objects.active=obj
            for modifier in list(obj.modifiers):bpy.ops.object.modifier_apply(modifier=modifier.name)
        mesh=obj.data
        roofs=[i for i,m in enumerate(mesh.materials) if m and m.name.startswith('Source roof')]
        if roofs:
            highest=max(v.co.z for v in mesh.vertices)
            for face in mesh.polygons:
                face.material_index=roofs[0] if face.normal.z>.75 and face.center.z>highest-.10 else 0
        if not mesh.uv_layers:
            uv=mesh.uv_layers.new(name='SourceMeters')
            for face in mesh.polygons:
                axis=max(range(3),key=lambda a:abs(face.normal[a]))
                for li in face.loop_indices:
                    v=mesh.vertices[mesh.loops[li].vertex_index].co
                    # World-meter scale: wall brick courses retain physical
                    # dimensions, instead of stretching one image per building.
                    uv.data[li].uv=((v.x if axis!=0 else v.y)/2,(v.y if axis==2 else v.z)/2)
    for material in bpy.data.materials:
        name=material.name
        prefix=('facade' if name.startswith('Mineral facade') else
                'asphalt' if name.startswith('Asphalt') else
                'paving' if name.startswith(('Warm stone','Source roof')) else
                'ground' if name.startswith('Grass') else None)
        if not prefix or not (root/f'{prefix}_diff.jpg').is_file():continue
        material.use_nodes=True
        nodes=material.node_tree.nodes;links=material.node_tree.links;nodes.clear()
        bs=nodes.new('ShaderNodeBsdfPrincipled');out=nodes.new('ShaderNodeOutputMaterial');links.new(bs.outputs[0],out.inputs['Surface'])
        bs.inputs['Roughness'].default_value=.85
        bs.inputs['Base Color'].default_value=(1,1,1,1)
        for suffix,socket in [('diff','Base Color'),('rough','Roughness'),('normal','Normal')]:
            path=root/f'{prefix}_{suffix}.jpg'
            if not path.is_file():continue
            image=bpy.data.images.load(str(path),check_existing=True)
            if suffix!='diff':image.colorspace_settings.name='Non-Color'
            texture=nodes.new('ShaderNodeTexImage');texture.image=image
            if suffix=='normal':
                normal=nodes.new('ShaderNodeNormalMap');normal.inputs['Strength'].default_value=.65
                links.new(texture.outputs['Color'],normal.inputs['Color']);links.new(normal.outputs[0],bs.inputs[socket])
            else:links.new(texture.outputs['Color'],bs.inputs[socket])
