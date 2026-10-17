/** Deterministic, full-canopy botanical assets. Leaf cards retain coverage at
 * every distance, unlike decimating away disconnected individual leaves. */
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

export function random(seed:number) { return () => {seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);t=t+Math.imul(t^t>>>7,61|t)^t;return ((t^t>>>14)>>>0)/4294967296;}; }
const library=new Map<string,THREE.Group>();
let leafMap:THREE.CanvasTexture|null=null;
function foliageMap() {
  if(leafMap)return leafMap;
  const canvas=document.createElement("canvas");canvas.width=canvas.height=256;
  const ctx=canvas.getContext("2d")!;const rng=random(392);
  // A leafy twig, including interior coverage and an irregular transparent edge.
  for(let i=0;i<210;i++) {
    const a=rng()*Math.PI*2,r=Math.sqrt(rng());
    const x=128+Math.cos(a)*r*112,y=128+Math.sin(a)*r*108;
    const s=5+rng()*9;const light=29+rng()*34;
    ctx.save();ctx.translate(x,y);ctx.rotate(rng()*Math.PI);
    ctx.fillStyle=`hsl(${83+rng()*29},${30+rng()*25}%,${light}%)`;
    ctx.beginPath();ctx.ellipse(0,0,s*.46,s,0,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle="rgba(210,221,140,.23)";ctx.lineWidth=.7;ctx.beginPath();ctx.moveTo(0,-s*.85);ctx.lineTo(0,s*.85);ctx.stroke();ctx.restore();
  }
  leafMap=new THREE.CanvasTexture(canvas);leafMap.colorSpace=THREE.SRGBColorSpace;
  leafMap.anisotropy=4;return leafMap;
}
function cylinder(a:THREE.Vector3,b:THREE.Vector3,r1:number,r2:number) {
  const d=b.clone().sub(a),geo=new THREE.CylinderGeometry(r2,r1,d.length(),6,1);
  geo.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),d.normalize()));
  geo.translate((a.x+b.x)/2,(a.y+b.y)/2,(a.z+b.z)/2);return geo;
}
function botanical(variant:number,shrub:boolean) {
  const key=`${variant}:${shrub}`;const cached=library.get(key);if(cached)return cached;
  const rng=random(variant*1741+27),root=new THREE.Group();
  const branchGeometries:THREE.BufferGeometry[]=[];
  const leaves:THREE.BufferGeometry[]=[];
  const stemHeight=shrub?.08:1.02, crownHeight=shrub?1.08:1.7;
  if(!shrub)branchGeometries.push(cylinder(new THREE.Vector3(),new THREE.Vector3(.04,-.02,2.16),.065,.018));
  const centers:THREE.Vector3[]=[];
  // Branch fans form an asymmetric broadleaf crown, with a visible clear stem.
  const fans=shrub?22:38;
  for(let i=0;i<fans;i++) {
    const angle=i*2.39996+rng()*.4;
    const z=stemHeight+.25+rng()*crownHeight;
    const envelope=Math.sqrt(Math.max(.12,1-Math.pow((z-stemHeight-crownHeight*.5)/(crownHeight*.78),2)));
    const radius=envelope*(.5+rng()*.4);
    const end=new THREE.Vector3(Math.cos(angle)*radius,Math.sin(angle)*radius,z);
    centers.push(end);
    branchGeometries.push(cylinder(new THREE.Vector3(.02,0,shrub?.07:Math.max(.8,z-.65)),end,shrub?.015:.025,.005));
  }
  const cardSize=shrub?.48:.53;
  for(const center of centers) {
    for(let j=0;j<(shrub?12:15);j++) {
      const pos=center.clone().add(new THREE.Vector3((rng()-.5)*.48,(rng()-.5)*.48,(rng()-.5)*.45));
      const geo=new THREE.PlaneGeometry(cardSize*(.75+rng()*.55),cardSize*(.85+rng()*.65));
      const rotation=new THREE.Euler(rng()*Math.PI,rng()*Math.PI,rng()*Math.PI);
      geo.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(rotation));geo.translate(pos.x,pos.y,pos.z);
      const color=new THREE.Color().setHSL(.20+(variant%3)*.014,.16,.62+rng()*.16);
      const colors=new Float32Array(geo.getAttribute("position").count*3);
      for(let k=0;k<colors.length;k+=3){colors[k]=color.r;colors[k+1]=color.g;colors[k+2]=color.b;}
      geo.setAttribute("color",new THREE.BufferAttribute(colors,3));leaves.push(geo);
    }
  }
  const bark=new THREE.MeshStandardMaterial({color:0x665447,roughness:1});
  const leafMaterial=new THREE.MeshStandardMaterial({map:foliageMap(),vertexColors:true,side:THREE.DoubleSide,alphaTest:.48,roughness:.9,metalness:0});
  const branches=new THREE.Mesh(mergeGeometries(branchGeometries),bark);
  const crown=new THREE.Mesh(mergeGeometries(leaves),leafMaterial);
  branches.castShadow=branches.receiveShadow=crown.castShadow=crown.receiveShadow=true;
  root.add(branches,crown);
  for(const geo of [...branchGeometries,...leaves])geo.dispose();
  root.traverse(o=>o.userData.sharedPreviewResource=true);
  library.set(key,root);return root;
}
/** Source crown radius remains the footprint; foliage is independent of zoom. */
export function plantingModel(id:string,radius:number,shrub:boolean) {
  let hash=0;for(const c of id)hash=(hash*31+c.charCodeAt(0))|0;
  const variant=Math.abs(hash)%5;
  const root=botanical(variant,shrub).clone(true);
  root.rotation.z=(Math.abs(hash)%628)/100;
  root.scale.setScalar(Math.max(radius,.15)/1.25);
  return root;
}
