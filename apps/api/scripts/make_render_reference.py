"""Reference-inspired demonstration, explicitly not an imported customer site.
Generate scene.json with API Python, then render locally with Blender.
"""
import json
import math
import random
import sys
from pathlib import Path
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import triangulate, unary_union

rng=random.Random(42)
surfaces=[]
def surface(poly,category,name,**kwargs):
    surfaces.append({'layer':name,'category':category,'triangles':[list(t.exterior.coords)[:3] for t in triangulate(poly) if poly.covers(t)],**kwargs})
surface(box(-11,-4,0,73),'BUILDING','West residential frontage',height=27)
surface(box(0,65,84,77),'BUILDING','North residential frontage',height=36)
surface(box(76,-4,87,65),'BUILDING','East residential frontage',height=24)
def smooth(points):
    controls=[points[0]]+points+[points[-1]];out=[]
    for i in range(1,len(controls)-2):
        p0,p1,p2,p3=controls[i-1:i+3]
        for j in range(12):
            t=j/12
            out.append(tuple(.5*((2*p1[d])+(-p0[d]+p2[d])*t+(2*p0[d]-5*p1[d]+4*p2[d]-p3[d])*t*t+(-p0[d]+3*p1[d]-3*p2[d]+p3[d])*t*t*t) for d in (0,1)))
    return LineString(out+[points[-1]])
paths=[smooth([(8,-2),(18,15),(34,31),(48,48),(69,66)]).buffer(2.6,quad_segs=12),
       smooth([(-1,44),(20,43),(34,31),(55,22),(77,12)]).buffer(2.2,quad_segs=12),
       Point(34,31).buffer(9,quad_segs=32),
       LineString([(10,1),(10,56),(66,56),(66,4)]).buffer(1.5,quad_segs=8)]
paving=unary_union(paths).intersection(box(1,0,75,64))
surface(paving,'SIDEWALK','Pedestrian promenade and central court')
play=Point(54,41).buffer(7,quad_segs=24)
surface(play,'SIDEWALK','Play courtyard',style='play')
lawns=box(1,0,75,64).difference(paving.union(play))
surface(lawns,'GREEN_AREA','Courtyard lawns')
placements=[]
for x in range(5,75,8):
 for y in range(7,63,9):
  px,py=x+rng.uniform(-1.5,1.5),y+rng.uniform(-1.5,1.5)
  if lawns.contains(Point(px,py).buffer(1.5)):
   placements.append({'id':f'tree{len(placements)}','kind':'TREE','position':[px,py],'crownRadiusM':rng.uniform(1.7,2.8)})
props=[{'kind':'PLAYGROUND','position':[54,41]},{'kind':'PERGOLA','position':[55,12],'size':[7,4]}, {'kind':'PERGOLA','position':[14,52],'size':[5,4]}]
for p in [(27,23),(42,34),(61,52),(17,35),(55,13)]:props.append({'kind':'RAISED_PLANTER','position':p,'size':[3.2,1.2]})
# People provide scale on a presentation demonstration, never normative data.
for p in [(18,15),(20,17),(31,32),(35,29),(48,48),(49,46),(12,40),(64,27)]:props.append({'kind':'PERSON','position':p})
data={'extents':{'min':[-11,-4],'max':[87,77]},'surfaces':surfaces,'placements':placements,'seed':42,
      'linearFeatures':[{'category':'FENCE','points':[[2,1],[2,34],[2,41],[2,63],[74,63],[74,20]]}],
      'pointFeatures':[{'category':'LIGHTING','position':p} for p in [(12,10),(25,23),(37,34),(53,50),(63,24),(12,47)]],
      'presentationObjects':props,'assumptions':['Reference-inspired illustrative courtyard, not a surveyed/customer plan. All architecture, planting and furniture are demonstration design.']}
out=Path(sys.argv[1]);out.write_text(json.dumps(data),encoding='utf-8');print(out,len(placements),'trees')
