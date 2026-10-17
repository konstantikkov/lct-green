/** Optional architectural setting. All additions are illustrative and never
 * enter CAD export, planting validation, or the persisted project document. */
import * as THREE from "three";
import type {Placement,ProjectDocument} from "../api/types";
import {gardenRoute} from "./gardenPaths";
import {random,plantingModel} from "./landscapeVegetation";

type Box={x:number;y:number;z:number;w:number;d:number;h:number;angle:number};
class Architecture {
  group=new THREE.Group();
  batches=new Map<THREE.Material,Box[]>();
  material(color:number,roughness=.8,metalness=0) {return new THREE.MeshStandardMaterial({color,roughness,metalness});}
  box(mat:THREE.Material,x:number,y:number,z:number,w:number,d:number,h:number,angle=0) {
    const batch=this.batches.get(mat)??[];batch.push({x,y,z,w,d,h,angle});this.batches.set(mat,batch);
  }
  finish() {
    for(const [mat,boxes] of this.batches) {
      const mesh=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),mat,boxes.length);
      const matrix=new THREE.Matrix4(),rotation=new THREE.Quaternion(),position=new THREE.Vector3(),scale=new THREE.Vector3();
      boxes.forEach((b,i)=>{position.set(b.x,b.y,b.z);scale.set(b.w,b.d,b.h);rotation.setFromAxisAngle(new THREE.Vector3(0,0,1),b.angle);matrix.compose(position,rotation,scale);mesh.setMatrixAt(i,matrix);});
      mesh.castShadow=mesh.receiveShadow=true;mesh.instanceMatrix.needsUpdate=true;this.group.add(mesh);
    }
    return this.group;
  }
}
function pavingTexture() {
  const canvas=document.createElement('canvas');canvas.width=canvas.height=512;const ctx=canvas.getContext('2d')!;
  const rng=random(74);ctx.fillStyle='#8a8982';ctx.fillRect(0,0,512,512);
  const colors=['#cec9bc','#c3bdaf','#d6d1c5','#b6b4aa','#d1ccbf'];
  for(let y=0;y<16;y++)for(let x=-1;x<8;x++) {
    ctx.fillStyle=colors[Math.floor(rng()*colors.length)];ctx.fillRect(x*64+(y%2)*32+1,y*32+1,62,30);
    for(let k=0;k<18;k++){ctx.fillStyle=rng()>.5?'rgba(255,255,255,.06)':'rgba(35,30,25,.04)';ctx.fillRect(x*64+(y%2)*32+rng()*62,y*32+rng()*30,2,2);}
  }
  const tex=new THREE.CanvasTexture(canvas);tex.colorSpace=THREE.SRGBColorSpace;tex.wrapS=tex.wrapT=THREE.RepeatWrapping;tex.anisotropy=8;return tex;
}
function masonryTexture() {
  const canvas=document.createElement('canvas');canvas.width=canvas.height=512;const c=canvas.getContext('2d')!,rng=random(318);
  c.fillStyle='#706b62';c.fillRect(0,0,512,512);
  for(let row=0;row<16;row++)for(let col=-1;col<8;col++){
    const value=Math.floor(165+rng()*55);c.fillStyle=`rgb(${value},${value-3},${value-8})`;
    const x=col*64+(row%2)*32,y=row*32;c.fillRect(x+1,y+1,62,30);
    for(let j=0;j<30;j++){c.fillStyle=rng()>.5?'rgba(255,255,255,.09)':'rgba(35,30,25,.07)';c.fillRect(x+1+rng()*61,y+1+rng()*29,1+rng()*3,1+rng()*2);}
  }
  const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;texture.wrapS=texture.wrapT=THREE.RepeatWrapping;texture.repeat.set(12,8);texture.anisotropy=8;return texture;
}
export function buildPresentationContext(doc:ProjectDocument,plants:Placement[]) {
  const a=new Architecture(),rng=random(946);
  const [x0,y0]=doc.extents.min,[x1,y1]=doc.extents.max;
  const w=Math.max(x1-x0,12),d=Math.max(y1-y0,12),cx=(x0+x1)/2,cy=(y0+y1)/2;
  const span=Math.max(w,d),s=Math.min(1.6,Math.max(.65,span/60));
  const urbanFloor=new THREE.Mesh(new THREE.PlaneGeometry(span*20,span*20),new THREE.MeshStandardMaterial({color:0x737672,roughness:.98}));
  urbanFloor.position.set(cx,cy,-.12);urbanFloor.receiveShadow=true;a.group.add(urbanFloor);
  const stone=a.material(0xc2bcac),dark=a.material(0x303837,.5,.4),wood=a.material(0x96704a,.7),soil=a.material(0x302b21),trim=a.material(0xc8c4b8);
  const brick=[a.material(0x80644e),a.material(0xada395),a.material(0x747c76)];
  const masonry=masonryTexture();for(const material of brick){material.map=masonry;material.bumpMap=masonry;material.bumpScale=.025;}
  const glass=[a.material(0x6e858f,.17,.4),a.material(0xb5b9b4,.25,.25),a.material(0x40545f,.15,.5)];
  const tex=pavingTexture();const paving=new THREE.MeshStandardMaterial({map:tex,bumpMap:tex,bumpScale:.012,roughness:.94,color:0xffffff});
  function path(x:number,y:number,pw:number,pd:number,z=.02) {
    const geo=new THREE.PlaneGeometry(pw,pd);const uv=geo.getAttribute('uv');for(let i=0;i<uv.count;i++)uv.setXY(i,uv.getX(i)*pw/5,uv.getY(i)*pd/5);
    const mesh=new THREE.Mesh(geo,paving);mesh.position.set(x,y,z);mesh.receiveShadow=true;a.group.add(mesh);
  }
  // Finite perimeter promenade instead of an endless tiled grass plane.
  path(cx,y0-2.3*s,w+10*s,4.6*s);path(cx,y1+2.3*s,w+10*s,4.6*s);
  path(x0-2.3*s,cy,4.6*s,d);path(x1+2.3*s,cy,4.6*s,d);
  for(const x of [x0-.08*s,x1+.08*s])a.box(stone,x,cy,.045,.18*s,d,.12);
  for(const y of [y0-.08*s,y1+.08*s])a.box(stone,cx,y,.045,w,.18*s,.12);
  // Only supply surrounding architecture when the drawing has no closed
  // building footprints. Existing source buildings remain the authority.
  const categories=new Map(doc.layers.map(l=>[l.name,l.classification.category]));
  const hasBuildings=doc.entities.some(e=>categories.get(e.layer)==='BUILDING'&&(e.kind==='polygon'||e.closed));
  const tall=Math.min(30,Math.max(15,span*.38));
  function frontage(x:number,y:number,length:number,height:number,angle:number,palette:number) {
    const depth=7*s;const along=new THREE.Vector2(Math.cos(angle),Math.sin(angle)),normal=new THREE.Vector2(-along.y,along.x);
    function box(mat:THREE.Material,u:number,v:number,z:number,bw:number,bd:number,bh:number) {a.box(mat,x+along.x*u+normal.x*v,y+along.y*u+normal.y*v,z,bw,bd,bh,angle);}
    box(brick[palette],0,depth/2,height/2,length,depth,height);
    box(trim,0,-.035,height-.2,length,.16,.4);
    box(dark,0,-.055,1.35,length,.14,2.7);
    const cols=Math.max(2,Math.floor(length/(3.15*s))),floors=Math.max(3,Math.floor(height/(3.1*s))),bay=length/cols,fh=height/floors;
    for(let floor=0;floor<floors;floor++)for(let col=0;col<cols;col++) {
      const u=-length/2+(col+.5)*bay,z=(floor+.5)*fh;
      const width=bay*(floor===0?.73:.52),height=fh*.68;
      box(trim,u,-.15,z,width+.18,.24,height+.18);
      box(glass[(floor+col)%3],u,-.29,z,width,.035,height);
      box(dark,u,-.32,z,.035,.035,height);
      box(dark,u,-.32,z-height*.18,width,.035,.035);
      box(trim,u,-.27,z-height/2-.07,width+.3,.42,.11);
      // Alternating recessed balconies, with thin metal rails and wood reveals.
      if(floor>0 && col%4===1) {
        box(stone,u,-.8,z-height/2-.14,bay*.85,1.5,.16);
        box(dark,u,-1.48,z-height/2+.38,bay*.85,.045,.055);
        for(let k=0;k<7;k++)box(dark,u+(k-3)*bay*.12,-1.48,z-height/2+.12,.026,.026,.55);
      }
    }
    for(let col=0;col<=cols;col+=3)box(trim,-length/2+col*bay,-.08,height/2,.24,.22,height);
    for(let j=0;j<Math.floor(length/5);j++)box(dark,-length/2+(j+.5)*5,depth/2,height+.5,2.3,2,.8);
  }
  if(!hasBuildings && span<450) {
    frontage(x0-5*s,cy,d+16*s,tall,Math.PI/2,0);
    frontage(cx,y1+5*s,w+18*s,tall*1.15,0,1);
    // A short return closes the courtyard without obscuring the foreground.
    frontage(x1+5*s,y1-d*.13,d*.32,tall*.85,-Math.PI/2,2);
  }
  if(!hasBuildings && span<250) {
    const routes=[
      [[cx,y0],[x0+w*.73,y1]],
      [[x0,cy-d*.18],[x1,cy+d*.08]],
    ];
    const routeObstacles=[...plants];
    for(const e of doc.entities) {
      if(categories.get(e.layer)!=='FENCE'||!e.points)continue;
      for(let j=0;j+3<e.points.length;j+=2){
        const ax=e.points[j],ay=e.points[j+1],bx=e.points[j+2],by=e.points[j+3];
        const samples=Math.min(300,Math.ceil(Math.hypot(bx-ax,by-ay)/.6));
        for(let k=0;k<=samples;k++){const t=samples?k/samples:0;routeObstacles.push({id:`fence:${e.id}:${j}:${k}`,kind:'TREE',position:[ax+(bx-ax)*t,ay+(by-ay)*t],crownRadiusM:0} as Placement);}
      }
    }
    const halfWidth=1.05*s;
    for(const [start,end] of routes) {
      const points=gardenRoute([x0,y0,x1,y1],routeObstacles,start,end,halfWidth);
      if(points.length<2)continue;
      const positions:number[]=[],uvs:number[]=[];let distance=0;
      const offsets=points.map((p,i)=>{
        const before=points[Math.max(0,i-1)],after=points[Math.min(points.length-1,i+1)];
        const first=new THREE.Vector2(p[0]-before[0],p[1]-before[1]).normalize();
        const last=new THREE.Vector2(after[0]-p[0],after[1]-p[1]).normalize();
        if(i===0)first.copy(last);if(i===points.length-1)last.copy(first);
        const normal=new THREE.Vector2(-first.y-last.y,first.x+last.x).normalize();
        const factor=halfWidth/Math.max(.5,normal.dot(new THREE.Vector2(-last.y,last.x)));
        return normal.multiplyScalar(factor);
      });
      for(let i=0;i<points.length-1;i++) {
        const [ax,ay]=points[i],[bx,by]=points[i+1];const dx=bx-ax,dy=by-ay,len=Math.hypot(dx,dy);if(len<.01)continue;
        const na=offsets[i],nb=offsets[i+1];
        const corners=[[ax+na.x,ay+na.y],[ax-na.x,ay-na.y],[bx-nb.x,by-nb.y],[bx+nb.x,by+nb.y]];
        for(const k of [0,1,2,0,2,3]) {positions.push(corners[k][0],corners[k][1],.075);uvs.push(k===0||k===3?0:halfWidth*2/5,(distance+(k>1?len:0))/5);}
        for(const side of [-1,1]){
          const sx=ax+na.x*side,sy=ay+na.y*side,ex=bx+nb.x*side,ey=by+nb.y*side;
          a.box(stone,(sx+ex)/2,(sy+ey)/2,.09,Math.hypot(ex-sx,ey-sy)+.02,.10,.13,Math.atan2(ey-sy,ex-sx));
        }
        distance+=len;
      }
      const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geo.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));geo.computeVertexNormals();
      const mesh=new THREE.Mesh(geo,paving);mesh.receiveShadow=true;a.group.add(mesh);
    }
  }
  function bench(x:number,y:number,angle:number) {
    for(let j=0;j<5;j++)a.box(wood,x,y+(j-2)*.095*s,.47*s,1.9*s,.08*s,.065*s,angle);
    for(const dx of [-.7,.7]){a.box(dark,x+dx*s,y,.24*s,.065*s,.4*s,.48*s);a.box(dark,x+dx*s,y+.22*s,.7*s,.06*s,.06*s,.6*s);}
    for(let j=0;j<3;j++)a.box(wood,x,y+.24*s,(.61+j*.11)*s,1.9*s,.07*s,.08*s,angle);
  }
  const extras=new THREE.Group();extras.name='illustrative-landscape-context';
  const count=Math.min(18,Math.max(5,Math.floor(w/(4.5*s))));
  for(let i=0;i<count;i++) {
    const x=x0+(i+.5)*w/count;
    a.box(soil,x,y0-4.4*s,.07,2.0*s,.8*s,.16);
    a.box(stone,x,y0-4.8*s,.09,2.1*s,.10*s,.23);
    for(let j=0;j<3;j++){const plant=plantingModel(`context:${i}:${j}`,s*.48,true);plant.position.set(x+(j-1)*.57*s,y0-4.4*s,.15);extras.add(plant);}
    if(i%3===1)bench(x,y0-2.8*s,0);
    if(i%4===0) {
      a.box(dark,x,y0-3.7*s,2.4*s,.075*s,.075*s,4.8*s);
      a.box(dark,x,y0-3.4*s,4.8*s,.13*s,.75*s,.10*s);
      a.box(trim,x,y0-3.17*s,4.74*s,.12*s,.26*s,.035*s);
    }
  }
  // Slender boundary fence: individual vertical bars, rails and planted margins.
  for(const edge of [x0-4.65*s,x1+4.65*s]) {
    a.box(dark,edge,cy,.95*s,.05*s,d, .045*s);
    a.box(dark,edge,cy,.2*s,.05*s,d, .045*s);
    for(let i=0;i<Math.min(500,d/(.22*s));i++)a.box(dark,edge,y0+i*.22*s,.55*s,.025*s,.025*s,1.1*s);
    for(let i=0;i<Math.floor(d/(5*s));i++){
      const y=y0+(i+.5)*5*s;
      const plant=plantingModel(`margin:${edge}:${i}`,s*(.6+rng()*.35),true);plant.position.set(edge+(edge<cx?1:-1)*.7*s,y,.04);extras.add(plant);
    }
  }
  // Layered perennial borders: fine arching grasses, flowering stems and
  // continuous dark mulch give the foreground a planted, maintained scale.
  const blades:number[]=[],bladeColors:number[]=[];
  const blooms:{x:number;y:number;z:number;color:number}[]=[];
  const bands=Math.min(100,Math.max(12,Math.floor(w/(.65*s))));
  a.box(soil,cx,y1+.7*s,.015,w,.95*s,.06);
  for(let i=0;i<bands;i++) {
    const x=x0+(i+.5)*w/bands,y=y1+(.5+rng()*.4)*s;
    for(let k=0;k<28;k++) {
      const angle=rng()*Math.PI*2,height=(.28+rng()*.50)*s,lean=(.12+rng()*.22)*s;
      const baseX=x+(rng()-.5)*.34*s,baseY=y+(rng()-.5)*.34*s;
      const width=.014*s,sideX=Math.cos(angle)*width,sideY=Math.sin(angle)*width;
      const a0=[baseX-sideX,baseY-sideY,.08],b0=[baseX+sideX,baseY+sideY,.08];
      const mid=[baseX+Math.sin(angle)*lean*.2,baseY+Math.cos(angle)*lean*.2,height*.65];
      const tip=[baseX+Math.sin(angle)*lean,baseY+Math.cos(angle)*lean,height];
      const color=new THREE.Color().setHSL(.17+rng()*.09,.3,.18+rng()*.13);
      for(const v of [a0,b0,[mid[0]+sideX*.5,mid[1]+sideY*.5,mid[2]],a0,[mid[0]+sideX*.5,mid[1]+sideY*.5,mid[2]],tip]){blades.push(...v);bladeColors.push(color.r,color.g,color.b);}
      if(k<3) {
        const z=(.4+rng()*.3)*s;
        a.box(dark,baseX,baseY,z/2,.006*s,.006*s,z);
        blooms.push({x:baseX,y:baseY,z,color:i%3===0?0xd2d0b6:0x856380});
      }
    }
  }
  const bladesGeo=new THREE.BufferGeometry();bladesGeo.setAttribute('position',new THREE.Float32BufferAttribute(blades,3));bladesGeo.setAttribute('color',new THREE.Float32BufferAttribute(bladeColors,3));bladesGeo.computeVertexNormals();
  const grassMesh=new THREE.Mesh(bladesGeo,new THREE.MeshStandardMaterial({vertexColors:true,roughness:1,side:THREE.DoubleSide}));grassMesh.castShadow=grassMesh.receiveShadow=true;a.group.add(grassMesh);
  const flowerMesh=new THREE.InstancedMesh(new THREE.SphereGeometry(1,6,4),a.material(0xffffff,.9),blooms.length);const m=new THREE.Matrix4();
  blooms.forEach((b,i)=>{m.makeScale(.045*s,.045*s,.11*s);m.setPosition(b.x,b.y,b.z);flowerMesh.setMatrixAt(i,m);flowerMesh.setColorAt(i,new THREE.Color(b.color));});flowerMesh.castShadow=true;a.group.add(flowerMesh);
  // Timber garden shelter with slatted roof and seating, outside the CAD bounds.
  if(w>22 && d>20 && span<250) {
    const px=x1-7*s,py=y0-2.1*s;
    for(const dx of [-2.1,2.1])for(const dy of [-1.3,1.3])a.box(wood,px+dx*s,py+dy*s,1.4*s,.12*s,.12*s,2.8*s);
    for(let j=0;j<23;j++)a.box(wood,px+(j-11)*.21*s,py,2.85*s,.10*s,3.2*s,.16*s);
    a.box(dark,px,py-1.35*s,2.73*s,4.65*s,.09*s,.16*s);a.box(dark,px,py+1.35*s,2.73*s,4.65*s,.09*s,.16*s);
    bench(px,py,0);
  }
  a.group.add(extras);a.finish();a.group.name='presentation-context';return a.group;
}
