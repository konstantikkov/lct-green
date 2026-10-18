"""CAD evidence for 3D: no guessed closure of dashed/open outlines."""
import math
import re
from statistics import median


def source_attributes(entity, scale):
    """Keep WCS Z/solid thickness alongside the unchanged 2D regulatory IR."""
    result={}
    try:
        t=entity.dxftype();points=[]
        if t=='LINE':points=[entity.dxf.start,entity.dxf.end]
        elif t in ('3DFACE','SOLID','TRACE'):
            points=[entity.dxf.get(f'vtx{i}') for i in range(4)]
        elif t=='LWPOLYLINE':points=list(entity.vertices_in_wcs())
        elif t=='POLYLINE':points=list(entity.points_in_wcs())
        elif t in ('POINT','TEXT','MTEXT','ATTRIB','INSERT','CIRCLE','ARC'):
            points=[entity.dxf.get('location' if t=='POINT' else 'center' if t in ('CIRCLE','ARC') else 'insert')]
        xyz=[[float(p[0])*scale,float(p[1])*scale,float(p[2])*scale] for p in points if p is not None]
        if xyz and all(math.isfinite(c) for p in xyz for c in p):
            if t=='3DFACE':result['vertices3D']=[p for i,p in enumerate(xyz) if i==0 or p!=xyz[i-1]]
            zs=[p[2] for p in xyz]
            if max(zs)-min(zs)<1e-5 and abs(zs[0])>1e-6:result['elevationM']=zs[0]
        thickness=float(entity.dxf.get('thickness',0))*scale
        if thickness>0 and math.isfinite(thickness):result['heightM']=thickness;result['heightSource']='CAD thickness'
    except (AttributeError,TypeError,ValueError,IndexError):pass
    return result


def height_evidence(poly, entities, attrs, fallback):
    height=attrs.get('heightM')
    if isinstance(height,(int,float)) and math.isfinite(height) and .1<=height<=400:
        return float(height),attrs.get('heightSource','CAD attribute')
    # Read only labels geometrically inside this footprint; no layer-name heuristics.
    from shapely.geometry import Point
    for e in entities:
        if e.kind!='text' or not poly.covers(Point(e.geometry['position'])):continue
        text=e.geometry.get('text','').upper().replace(',','.')
        match=re.search(r'(?:H|Н|ВЫСОТА)\s*[=:]\s*(\d+(?:\.\d+)?)',text)
        if match and .1<=float(match[1])<=400:return float(match[1]),'CAD height label'
        floors=re.fullmatch(r'\s*(\d{1,2})\s*(?:КЖ|КН|ЭТ\.?|ЭТАЖА?)\s*',text)
        if floors and 0<int(floors[1])<=80:return int(floors[1])*3.,'CAD storeys × assumed 3 m'
    return float(fallback),'user fallback (height absent in CAD)'


def enrich_surfaces(doc, surfaces, options):
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
    origin=median([e.attributes['elevationM'] for e in doc.entities if 'elevationM' in e.attributes]) if any('elevationM' in e.attributes for e in doc.entities) else median([p[2] for e in doc.entities for p in e.attributes.get('vertices3D',[])]) if any(e.attributes.get('vertices3D') for e in doc.entities) else 0
    from shapely import STRtree
    from shapely.geometry import Point
    texts=[e for e in doc.entities if e.kind=='text']
    text_index=STRtree([Point(e.geometry['position']) for e in texts])
    by_id={e.id:e for e in doc.entities}
    for s in surfaces:
        poly=unary_union([Polygon(t) for t in s['triangles']])
        parts=list(poly.geoms) if hasattr(poly,'geoms') else [poly]
        s['outlines']=[list(r.coords) for raw in parts if raw.geom_type=='Polygon' for p in [orient(raw,sign=1)] for r in [p.exterior,*p.interiors]]
        attrs=by_id[s['entityId']].attributes if s.get('entityId') in by_id else {}
        s['baseZ']=float(attrs.get('elevationM',origin))-origin
        if s['category']=='BUILDING':s['height'],s['heightSource']=height_evidence(poly,[texts[int(i)] for i in text_index.query(poly)],attrs,options.get('buildingHeightM',9))
        else:s['height']=.12 if s['category']=='SIDEWALK' else .04 if s['category'] in ('ROAD','PARKING') else .02
    terrain=[]
    for e in doc.entities:
        verts=e.attributes.get('vertices3D')
        if not verts or doc.category_of(e).startswith('UTILITY'):continue
        # 3DFACE is actual supplied geometry, not a heightmap inferred from labels.
        verts=[(p[0],p[1],p[2]-origin) for p in verts]
        terrain.append({'vertices':verts,'layer':e.layer,'entityId':e.id,'category':doc.category_of(e)})
    return {'elevationOriginM':origin,'terrainFaces':terrain}


def excavation_buildings(doc, surfaces, options):
    """User-authorized visualization of labelled excavations as buildings.

    Close only mutual-nearest short gaps; never use a bounding box/convex hull.
    Accept only closed cycles containing an excavation label.
    """
    if not options.get('excavationsAsBuildings',True):return []
    from collections import Counter
    from shapely.geometry import LineString,Point,Polygon
    from shapely.ops import polygonize,unary_union,triangulate
    result=[]
    for layer in doc.layers:
        entities=[e for e in doc.entities if e.layer==layer.name]
        labels=[e for e in entities if e.kind=='text' and 'КОТЛОВАН' in e.geometry.get('text','').upper()]
        if not labels:continue
        lines=[LineString(e.geometry['points']) for e in entities if e.kind=='polyline' and len(e.geometry.get('points',[]))>=2]
        if not lines:continue
        endpoints=[tuple(round(v,6) for v in p) for line in lines for p in (line.coords[0],line.coords[-1])]
        counts=Counter(endpoints);ends=[p for p in counts if counts[p]==1]
        outward={}
        for line in lines:
            coords=list(line.coords)
            for p,q in [(coords[0],coords[1]),(coords[-1],coords[-2])]:
                length=math.dist(p,q)
                if length>1e-8:outward[tuple(round(v,6) for v in p)]=((p[0]-q[0])/length,(p[1]-q[1])/length)
        def aligned(p,q):
            length=math.dist(p,q)
            if length<1e-8 or length>.65:return False
            dx,dy=(q[0]-p[0])/length,(q[1]-p[1])/length
            a,b=outward.get(p,(0,0)),outward.get(q,(0,0))
            return a[0]*dx+a[1]*dy>.05 and -b[0]*dx-b[1]*dy>.05
        # Spatial lookup avoids quadratic scans on dense topographic packages.
        from shapely import STRtree
        tree=STRtree([Point(p) for p in ends]);nearest={}
        for i,p in enumerate(ends):
            candidates=[int(j) for j in tree.query(Point(p).buffer(.65)) if int(j)!=i and aligned(p,ends[int(j)])]
            if candidates:nearest[i]=min(candidates,key=lambda j:math.dist(p,ends[j]))
        bridges=[LineString([ends[i],ends[j]]) for i,j in nearest.items() if i<j and nearest.get(j)==i]
        for poly in polygonize(unary_union(lines+bridges)):
            if poly.area<1 or not any(poly.covers(Point(e.geometry['position'])) for e in labels):continue
            if any(s['category']=='BUILDING' and unary_union([Polygon(t) for t in s['triangles']]).covers(poly.representative_point()) for s in surfaces):continue
            triangles=[list(t.exterior.coords)[:3] for t in triangulate(poly) if poly.covers(t)]
            result.append({'category':'BUILDING','layer':layer.name,'triangles':triangles,
                'interpretation':'excavation-as-building (user setting)','gapToleranceM':.65})
    return result
