"""Dependency-free geometric lookup shared by Blender and regression tests."""
import math

def point_in_tri(p, tri):
    signs=[]
    for a,b in zip(tri,tri[1:]+tri[:1]): signs.append((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]))
    return min(signs)>=-1e-7 or max(signs)<=1e-7


class TriangleIndex:
    """Small uniform grid for standalone Blender Python (no GIS dependency)."""
    def __init__(self, triangles):
        self.bins={};points=[p for t in triangles for p in t]
        if not points: self.step=1;return
        self.step=max(.25,max(max(p[d] for p in points)-min(p[d] for p in points) for d in (0,1))/24)
        for t in triangles:
            lo=[math.floor(min(p[d] for p in t)/self.step) for d in (0,1)]
            hi=[math.floor(max(p[d] for p in t)/self.step) for d in (0,1)]
            for x in range(lo[0],hi[0]+1):
                for y in range(lo[1],hi[1]+1):self.bins.setdefault((x,y),[]).append(t)

    def contains(self,p):
        return any(point_in_tri(p,t) for t in self.bins.get((math.floor(p[0]/self.step),math.floor(p[1]/self.step)),[]))

