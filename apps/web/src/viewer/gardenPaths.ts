/** Presentation-only pedestrian routes. Avoid planted stems/shrub crowns;
 * this is visual context, not a substitute for the regulatory validator. */
import type {Placement} from "../api/types";
export function gardenRoute(bounds:number[],plants:Placement[],start:number[],end:number[],halfWidth:number) {
  const [x0,y0,x1,y1]=bounds,step=Math.max(.6,Math.max(x1-x0,y1-y0)/100);
  const nx=Math.ceil((x1-x0)/step)+1,ny=Math.ceil((y1-y0)/step)+1;
  const point=(i:number)=>[x0+(i%nx)*step,y0+Math.floor(i/nx)*step];
  const blocked=new Uint8Array(nx*ny);
  for(let i=0;i<blocked.length;i++) {const [x,y]=point(i);blocked[i]=plants.some(p=>Math.hypot(x-p.position[0],y-p.position[1])<(p.kind==='TREE'?.85:p.crownRadiusM)+halfWidth+.15)?1:0;}
  function nearest(p:number[]) {let best=-1,d=Infinity;for(let i=0;i<blocked.length;i++)if(!blocked[i]){const q=point(i),dd=Math.hypot(q[0]-p[0],q[1]-p[1]);if(dd<d){best=i;d=dd;}}return best;}
  const from=nearest(start),to=nearest(end);if(from<0||to<0)return [];
  const scores=new Float64Array(nx*ny).fill(Infinity),parents=new Int32Array(nx*ny).fill(-1),closed=new Uint8Array(nx*ny);scores[from]=0;
  const open=new Set([from]),goal=point(to);
  while(open.size) {
    let cur=-1,best=Infinity;
    for(const i of open){const p=point(i),f=scores[i]+Math.hypot(p[0]-goal[0],p[1]-goal[1]);if(f<best){best=f;cur=i;}}
    if(cur===to)break;open.delete(cur);closed[cur]=1;
    const cx=cur%nx,cy=Math.floor(cur/nx);
    for(const [dx,dy] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[-1,1],[1,-1],[-1,-1]]) {
      const x=cx+dx,y=cy+dy;if(x<0||y<0||x>=nx||y>=ny)continue;
      const next=y*nx+x;if(blocked[next]||closed[next])continue;
      if(dx&&dy&&(blocked[cy*nx+x]||blocked[y*nx+cx]))continue;
      const score=scores[cur]+Math.hypot(dx,dy)*step;
      if(score<scores[next]){scores[next]=score;parents[next]=cur;open.add(next);}
    }
  }
  if(to!==from&&parents[to]<0)return [];
  const raw:number[][]=[];for(let i=to;i!==-1;i=parents[i]){raw.push(point(i));if(i===from)break;}raw.reverse();
  // String pulling removes grid stair-steps while checking the complete
  // segment against planting footprints (not just its endpoints).
  function clear(a:number[],b:number[]) {
    const n=Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/(step*.4));
    for(let k=0;k<=n;k++) {
      const t=n?k/n:0,x=a[0]+(b[0]-a[0])*t,y=a[1]+(b[1]-a[1])*t;
      if(plants.some(p=>Math.hypot(x-p.position[0],y-p.position[1])<(p.kind==='TREE'?.85:p.crownRadiusM)+halfWidth+.12))return false;
    }
    return true;
  }
  const result:number[][]=[];let i=0;
  while(i<raw.length-1){result.push(raw[i]);let j=raw.length-1;while(j>i+1&&!clear(raw[i],raw[j]))j--;i=j;}
  result.push(raw[raw.length-1]);return result;
}
