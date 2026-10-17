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

def enrich(data, ox, oy, presentation=True):
    d=DetailBuilder(data.get('seed',42));rng=d.rng
    indexes={id(s):TriangleIndex(s['triangles']) for s in data.get('surfaces',[])}
    metal=d.mat('Powder-coated anthracite',(.025,.035,.04),.38,.7)
    glass=d.mat('Architectural blue-grey glazing',(.055,.095,.12),.12,.55)
    stone=d.mat('Limestone coping',(.58,.55,.46),.75)
    concrete=d.mat('Cast stone furniture',(.39,.40,.36),.8)
    wood=[d.mat('Oiled larch '+str(i),(.29+i*.025,.145+i*.015,.065+i*.009),.48) for i in range(4)]
    pavers=[d.mat('Paving tone '+str(i),c,.88) for i,c in enumerate([(.50,.48,.42),(.66,.63,.55),(.36,.38,.37),(.59,.56,.49)])]
    soil=d.mat('Planting bed mulch',(.065,.043,.024),1)
    green=[d.mat('Groundcover '+str(i),c,.8) for i,c in enumerate([(.09,.19,.035),(.16,.25,.055),(.25,.29,.09),(.10,.18,.14)])]
    flower=[d.mat('Perennial accent '+str(i),c,.72) for i,c in enumerate([(.48,.19,.40),(.56,.49,.20),(.67,.68,.59),(.31,.19,.43)])]
    def local(pt): return (pt[0]-ox,pt[1]-oy)
    # Source building footprints drive real geometric frames, sills, roof
    # parapets and projecting facade bays. Dimensions/styles are illustrative.
    for si,surface in enumerate(data.get('surfaces',[])):
        cat=surface['category'];triangles=surface['triangles'];index=indexes[id(surface)]
        edges=boundary_edges(triangles)
        if presentation and cat=='BUILDING':
            height=float(surface.get('height',12));floors=max(1,min(15,round(height/3)))
            for ea,eb in edges:
                ax,ay=local(ea);bx,by=local(eb);dx,dy=bx-ax,by-ay;length=math.hypot(dx,dy)
                if length<1: continue
                angle=math.atan2(dy,dx);u=Vector((dx/length,dy/length,0))
                midpoint=Vector(((ax+bx)/2,(ay+by)/2,0));n=Vector((u.y,-u.x,0))
                # Determine which side is outside the actual triangulated area.
                probe=(midpoint.x+ox+n.x*.05,midpoint.y+oy+n.y*.05)
                if index.contains(probe): n=-n
                for z in (.22,height+.15):
                    d.box('CAD footprint / stone coping',tuple(midpoint+n*.05+Vector((0,0,z))),(length+.08,.28,.20),stone,angle)
                bays=max(1,int(length/3.2));step=length/bays
                for col in range(bays):
                    anchor=Vector((ax,ay,0))+u*(col+.5)*step
                    for floor in range(floors):
                        z=(floor+.52)*height/floors
                        center=anchor+n*.09+Vector((0,0,z))
                        ww=min(1.55,step*.58);hh=min(1.8,height/floors*.60)
                        d.box('CAD footprint / glazing',tuple(center),(ww,.07,hh),glass,angle)
                        for side in (-1,1):
                            d.box('CAD footprint / window frames',tuple(center+u*(side*(ww/2+.04))),( .075,.19,hh+.20),metal,angle)
                            d.box('CAD footprint / window frames',tuple(center+Vector((0,0,side*(hh/2+.04)))),(ww+.15,.19,.075),metal,angle)
                        d.box('CAD footprint / recessed sills',tuple(center+n*.08-Vector((0,0,hh/2+.11))),(ww+.27,.35,.10),stone,angle)
                        if presentation and floor>0 and (col+si)%4==1:
                            balcony=center+n*.65-Vector((0,0,hh/2+.12))
                            d.box('Illustrative balcony slabs',tuple(balcony),(ww+.5,1.2,.13),stone,angle)
                            front=balcony+n*.53
                            d.rod('Illustrative balcony rails',front-u*(ww/2+.15)+Vector((0,0,1)),front+u*(ww/2+.15)+Vector((0,0,1)),.025,metal)
                            for k in range(9):
                                p=front+u*(-ww/2-.15+k*(ww+.3)/8)
                                d.rod('Illustrative balcony rails',p,p+Vector((0,0,1)),.013,metal)
        if presentation and cat=='GREEN_AREA':
            # Planting bands follow actual lawn boundaries, with foliage kept
            # on the lawn side. Grid deduplication avoids dense corner overlaps.
            planted=set()
            for ea,eb in edges:
                ax,ay=local(ea);bx,by=local(eb);delta=Vector((bx-ax,by-ay,0));length=delta.length
                if length<.1:continue
                u=delta.normalized();n=Vector((u.y,-u.x,0));mid=Vector(((ax+bx)/2,(ay+by)/2,0))
                if not index.contains((mid.x+ox+n.x*.1,mid.y+oy+n.y*.1)):n=-n
                verts=[(ax,ay,.045),(bx,by,.045),(bx+n.x*.85,by+n.y*.85,.045),(ax+n.x*.85,ay+n.y*.85,.045)]
                d.add('Illustrative perennial border mulch',verts,[(0,1,2,3)],soil)
                for j in range(min(100,math.ceil(length/.65))):
                    q=Vector((ax,ay,0))+delta*(j+.5)/max(1,math.ceil(length/.65))+n*.42
                    key=(round(q.x/.5),round(q.y/.5))
                    if key in planted or len(planted)>2000:continue
                    planted.add(key)
                    d.clump((q.x,q.y,.05),rng.uniform(.25,.42),rng.uniform(.30,.65),green[j%4])
                    if j%2==0:
                        for k in range(4):d.clump((q.x+rng.uniform(-.15,.15),q.y+rng.uniform(-.15,.15),rng.uniform(.35,.65)),.055,.12,flower[j%4])
        if presentation and cat in ('SIDEWALK','GREEN_AREA'):
            for ea,eb in edges:
                a,b=Vector((*local(ea),.07)),Vector((*local(eb),.07))
                length=(b-a).length
                if length>.2: d.box('CAD surface / raised edging',tuple((a+b)/2),(length,.11,.17),stone,math.atan2(b.y-a.y,b.x-a.x))
        if presentation and cat=='SIDEWALK' and surface.get('style') != 'play':
            # Individually modeled paving, clipped to the CAD surface.
            pts=[p for t in triangles for p in t]
            if not pts: continue
            x0,x1=min(p[0] for p in pts),max(p[0] for p in pts)
            y0,y1=min(p[1] for p in pts),max(p[1] for p in pts)
            count=0
            for row in range(min(200,math.ceil((y1-y0)/.6))):
                for col in range(min(400,math.ceil((x1-x0)/1.2))):
                    x=x0+(col+.5)*1.2+(row%2)*.6;y=y0+(row+.5)*.6
                    corners=[(x+dx,y+dy) for dx in (-.58,.58) for dy in (-.28,.28)]
                    if all(index.contains(c) for c in corners):
                        d.box('CAD sidewalk / individual stone pavers',(x-ox,y-oy,.075),(1.18,.58,.06),pavers[rng.randrange(4)])
                        count+=1
                    if count>=16000:break
                if count>=16000:break
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
    if presentation:
        beds=[]
        for i,p in enumerate(data.get('placements',[])):
            if p['kind']!='TREE':continue
            x,y=local(p['position']);r=min(2.1,max(.85,p['crownRadiusM']*.58))
            # Avoid covering known paving/buildings with ornamental planting.
            if any(s['category'] in ('BUILDING','SIDEWALK','ROAD','PARKING') and indexes[id(s)].contains(p['position']) for s in data.get('surfaces',[])):continue
            if any((x-a)**2+(y-b)**2<((r+c)*.75)**2 for a,b,c in beds):continue
            beds.append((x,y,r))
            # Low, irregular, layered beds instead of isolated tree symbols.
            verts=[(x,y,.055)]+[(x+r*math.cos(j*math.tau/24),y+r*.78*math.sin(j*math.tau/24),.055) for j in range(24)]
            d.add('Illustrative mulched perennial beds',verts,[(0,j+1,(j+1)%24+1) for j in range(24)],soil)
            for j in range(26):
                az=rng.random()*math.tau;rr=rng.uniform(.35,1)*r
                cx,cy=x+rr*math.cos(az),y+rr*.78*math.sin(az)
                d.clump((cx,cy,.07),rng.uniform(.18,.38),rng.uniform(.25,.65),green[j%4])
                for k in range(7):
                    az2=rng.random()*math.tau;h=rng.uniform(.35,.8)
                    start=(cx+rng.uniform(-.10,.10),cy+rng.uniform(-.10,.10),.1)
                    tip=(start[0]+.22*math.cos(az2),start[1]+.22*math.sin(az2),h)
                    d.rod('Illustrative ornamental grass',start,tip,.008,green[(j+1)%4],3)
                if j%3==0:d.clump((cx,cy,.47),.09,.14,flower[(i+j)%4])
        # Furniture is constrained to already paved surfaces and marked as
        # illustrative. It never enters the DXF or compliance report.
        paved=[s for s in data.get('surfaces',[]) if s['category']=='SIDEWALK']
        seats=[]
        for surface in paved:
            for t in surface['triangles']:
                x=sum(p[0] for p in t)/3;y=sum(p[1] for p in t)/3
                if any((x-a)**2+(y-b)**2<64 for a,b in seats):continue
                if any((x-p['position'][0])**2+(y-p['position'][1])**2<4 for p in data.get('placements',[])):continue
                seats.append((x,y));x-=ox;y-=oy
                for k in range(6):d.box('Illustrative timber seating',(x,y+(k-2.5)*.08,.48),(1.9,.065,.065),wood[k%4])
                for side in (-1,1):
                    d.box('Illustrative furniture supports',(x+side*.7,y,.24),(.08,.46,.44),metal)
                for k in range(4):d.box('Illustrative timber seating',(x,y+.25,.68+k*.11),(1.9,.055,.075),wood[k%4])
                if len(seats)>=60:break
    # Explicit designer-approved presentation objects are accepted by the local
    # scene format. The reference demo uses these; nothing is added to CAD.
    for prop in data.get('presentationObjects',[]) if presentation else []:
        x,y=local(prop['position']);angle=prop.get('rotation',0)
        if prop['kind']=='PERSON':
            clothing=d.mat('Scale figure clothing '+str(int(x)%3),[(.07,.12,.19),(.42,.28,.17),(.53,.49,.38)][int(x)%3],.9)
            skin=d.mat('Scale figure skin',(.49,.31,.21),.8)
            d.clump((x,y,1.48),.10,.23,skin)
            d.rod('Illustrative scale figures',(x,y,.82),(x,y,1.48),.16,clothing,8)
            for side in (-1,1):
                d.rod('Illustrative scale figures',(x+side*.08,y,.88),(x+side*.12,y+side*.12,.08),.065,metal,6)
                d.rod('Illustrative scale figures',(x+side*.17,y,1.38),(x+side*.24,y+.08,.91),.055,clothing,6)
        if prop['kind']=='PLAYGROUND':
            # Detailed timber climbing frame, rope net and slide in the
            # explicitly authored illustrative play area.
            for sx in (-1,1):
                for sy in (-1,1):d.rod('Illustrative play timber posts',(x+sx,y+sy,0),(x+sx,y+sy,2.7),.09,wood[1])
            for j in range(14):d.box('Illustrative play deck',(x-1+j*.15,y,1.4),(.13,2.1,.08),wood[j%4])
            for z in (1.7,2.2):d.rod('Illustrative play rails',(x-1,y+1,z),(x+1,y+1,z),.045,wood[0])
            rope=d.mat('Natural play rope',(.38,.29,.17),.9)
            for k in range(7):
                v=-1+k/3
                d.rod('Illustrative climbing net',(x+1,y+v,.12),(x+1,y+v,2.35),.014,rope)
                d.rod('Illustrative climbing net',(x+1,y-1,.15+k*.35),(x+1,y+1,.15+k*.35),.014,rope)
            for k in range(7):d.rod('Illustrative play ladder',(x-1.5,y-1.8+k*.12,k*.2),(x-.7,y-1.8+k*.12,k*.2),.025,wood[1])
            slide=d.mat('Satin stainless slide',(.36,.39,.38),.23,.8)
            verts=[]
            for k in range(16):
                t=k/15
                for side in (-1,1):verts.append((x+side*.37,y-1-t*3,1.4*(1-t)**2+.10))
            d.add('Illustrative slide',verts,[(2*k,2*k+1,2*k+3,2*k+2) for k in range(15)],slide)
        if prop['kind']=='PERGOLA':
            w,h=prop.get('size',[5,4]);
            for sx in (-1,1):
                for sy in (-1,1):d.box('Illustrative pergola columns',(x+sx*w/2,y+sy*h/2,1.4),(.14,.14,2.8),wood[0])
            for j in range(math.ceil(w/.22)+1):d.box('Illustrative pergola canopy',(x-w/2+j*.22,y,2.85),(.08,h+.5,.18),wood[j%4])
            for side in (-1,1):d.box('Illustrative pergola beams',(x,y+side*h/2,2.7),(w+.4,.14,.24),wood[1])
        if prop['kind']=='RAISED_PLANTER':
            w,h=prop.get('size',[3,1.2]);d.box('Illustrative planter walls',(x,y,.36),(w,h,.72),concrete,angle)
            for j in range(15):d.clump((x+rng.uniform(-w*.4,w*.4),y+rng.uniform(-h*.35,h*.35),.72),.25,rng.uniform(.4,.8),green[j%4])
    d.flush()
