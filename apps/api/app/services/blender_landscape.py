"""Offline, batched architectural detail for Cycles presentation scenes.

CAD-derived structures and optional illustrative landscape furniture have
separate mesh names. This module never changes the planting/CAD export.
"""
import math
import random
from collections import Counter
import bpy
from mathutils import Vector


class DetailBuilder:
    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self.batches = {}
        self.materials = {}

    def mat(self, name, color, rough=.65, metal=0):
        if name in self.materials: return self.materials[name]
        m = bpy.data.materials.new(name); m.use_nodes = True
        bs = m.node_tree.nodes.get('Principled BSDF')
        bs.inputs['Base Color'].default_value = (*color,1)
        bs.inputs['Roughness'].default_value = rough
        bs.inputs['Metallic'].default_value = metal
        self.materials[name] = m
        return m

    def add(self, name, verts, faces, mat):
        key = (name, mat.name)
        vs, fs, _ = self.batches.setdefault(key, ([],[],mat))
        start = len(vs); vs.extend(verts); fs.extend(tuple(start+i for i in f) for f in faces)

    def box(self, name, center, size, mat, angle=0):
        x,y,z = center; sx,sy,sz = (v/2 for v in size)
        c,s = math.cos(angle),math.sin(angle)
        v = [(x+u*c-w*s,y+u*s+w*c,z+h) for h in (-sz,sz) for u,w in [(-sx,-sy),(sx,-sy),(sx,sy),(-sx,sy)]]
        self.add(name,v,[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],mat)

    def rod(self, name, a, b, radius, mat, sides=6):
        a,b=Vector(a),Vector(b); direction=b-a
        if direction.length < 1e-5: return
        q=direction.to_track_quat('Z','Y')
        verts=[tuple(pt+q@Vector((radius*math.cos(j*math.tau/sides),radius*math.sin(j*math.tau/sides),0))) for pt in (a,b) for j in range(sides)]
        faces=[tuple(range(sides-1,-1,-1)),tuple(range(sides,2*sides))]
        faces.extend((j,(j+1)%sides,(j+1)%sides+sides,j+sides) for j in range(sides))
        self.add(name,verts,faces,mat)

    def clump(self, center, radius, height, mat):
        x,y,z=center; verts=[]
        for ring in range(6):
            polar=math.pi*ring/5
            for j in range(9):
                az=j*math.tau/9
                rr=radius*(1+self.rng.uniform(-.12,.12))*math.sin(polar)
                verts.append((x+rr*math.cos(az),y+rr*math.sin(az),z+height*(1+math.cos(polar))/2))
        faces=[(r*9+j,r*9+(j+1)%9,(r+1)*9+(j+1)%9,(r+1)*9+j) for r in range(5) for j in range(9)]
        self.add('Illustrative layered groundcover',verts,faces,mat)

    def flush(self):
        for (name,_),(verts,faces,mat) in self.batches.items():
            if not verts: continue
            geo=bpy.data.meshes.new(name);geo.from_pydata(verts,[],faces);geo.update()
            obj=bpy.data.objects.new(name,geo);bpy.context.scene.collection.objects.link(obj);geo.materials.append(mat)
            if any(w in name for w in ('cladding','frames','furniture','seating')):
                bevel=obj.modifiers.new('Small realistic edge bevels','BEVEL');bevel.width=.012;bevel.segments=2


def boundary_edges(triangles):
    edges=Counter()
    for tri in triangles:
        pts=[tuple(round(float(v),4) for v in p) for p in tri]
        for a,b in zip(pts,pts[1:]+pts[:1]): edges[tuple(sorted((a,b)))]+=1
    return [edge for edge,count in edges.items() if count == 1]


from render_geometry import TriangleIndex, point_in_tri

def enrich(data, ox, oy, presentation=False):
    """Model only fences/lights explicitly present in source CAD.

    The legacy presentation argument is accepted for old bundles, but can no
    longer add buildings, furniture, paths, flower beds or other scenery.
    """
    d=DetailBuilder(data.get('seed',42))
    metal=d.mat('Source fence and lighting metal',(.12,.13,.12),.55,.65)
    def local(pt):return (pt[0]-ox,pt[1]-oy)
    # Fences and lighting explicitly present in the CAD classification.
    for feature in data.get('linearFeatures',[]):
        if feature['category']!='FENCE': continue
        pts=feature['points']
        for pa,pb in zip(pts,pts[1:]):
            a,b=Vector((*local(pa),0)),Vector((*local(pb),0));delta=b-a
            if delta.length>.1:
                for z in (.15,1.22):d.rod('CAD fence rails',a+Vector((0,0,z)),b+Vector((0,0,z)),.025,metal)
                for k in range(min(1500,math.ceil(delta.length/.16))+1):
                    p=a+delta*k/max(1,math.ceil(delta.length/.16))
                    d.rod('CAD fence verticals',p,p+Vector((0,0,1.3)),.014,metal)
    for f in data.get('pointFeatures',[]):
        if f['category']=='LIGHTING':
            x,y=local(f['position']);d.rod('CAD lighting columns',(x,y,0),(x,y,4.2),.055,metal)
            d.box('CAD light housings',(x,y,4.22),(.75,.24,.10),metal)
    d.flush()
