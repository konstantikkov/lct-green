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
