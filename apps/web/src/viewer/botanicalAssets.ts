/** Sapling-authored branch/leaf meshes, bundled locally. Never decimate leaf
 * coverage on camera zoom. Normalize actual mesh vertices to the plan radius. */
import * as THREE from "three";
import {mergeGeometries} from "three/examples/jsm/utils/BufferGeometryUtils.js";
import {random} from "./landscapeVegetation";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader.js";
const trees=new Map<number,THREE.Group>();
let loading:Promise<void>|null=null;
let barkMap:THREE.CanvasTexture|null=null;
function barkTexture() {
  if(barkMap)return barkMap;
  const canvas=document.createElement('canvas');canvas.width=128;canvas.height=512;const ctx=canvas.getContext('2d')!;
  ctx.fillStyle='#a39b8e';ctx.fillRect(0,0,128,512);
  for(let i=0;i<500;i++){const x=(i*73)%128,y=(i*193)%512;ctx.strokeStyle=i%3?'rgba(44,36,29,.32)':'rgba(231,221,205,.35)';ctx.lineWidth=i%4*.4+.4;ctx.beginPath();ctx.moveTo(x,y);ctx.bezierCurveTo(x-2,y+5,x+3,y+13,x-1,y+30+i%25);ctx.stroke();}
  barkMap=new THREE.CanvasTexture(canvas);barkMap.colorSpace=THREE.SRGBColorSpace;barkMap.wrapS=barkMap.wrapT=THREE.RepeatWrapping;barkMap.repeat.set(2,4);barkMap.anisotropy=8;return barkMap;
}
export function loadBotanicalAssets() {
  if(!loading)loading=new GLTFLoader().loadAsync('/models/botanical-trees.glb?v=1').then(gltf=>{
    for(let i=0;i<4;i++) {
      const node=gltf.scene.getObjectByName(`plant_${i}_high`);if(!node)throw new Error('Botanical tree mesh is missing');
      const root=new THREE.Group();const oriented=new THREE.Group();oriented.rotation.x=Math.PI/2;oriented.add(node.clone(true));root.add(oriented);root.updateMatrixWorld(true);
      let radius=0;
      root.traverse(o=>{const mesh=o as THREE.Mesh;if(!mesh.isMesh)return;const pos=mesh.geometry.getAttribute('position'),v=new THREE.Vector3();
        for(let j=0;j<pos.count;j++){v.fromBufferAttribute(pos,j).applyMatrix4(mesh.matrixWorld);radius=Math.max(radius,Math.hypot(v.x,v.y));}
        const originalMaterial=mesh.material;
        const materials=(Array.isArray(originalMaterial)?originalMaterial:[originalMaterial]).map(material=>{
          const m=material.clone() as THREE.MeshStandardMaterial;
          if(/bark/i.test(m.name)){
            if(!mesh.geometry.getAttribute('uv')){
              mesh.geometry=mesh.geometry.clone();const positions=mesh.geometry.getAttribute('position'),uv=new Float32Array(positions.count*2);
              for(let k=0;k<positions.count;k++){uv[k*2]=Math.atan2(positions.getZ(k),positions.getX(k))/(Math.PI*2);uv[k*2+1]=positions.getY(k)*2;}
              mesh.geometry.setAttribute('uv',new THREE.BufferAttribute(uv,2));
            }
            m.map=barkTexture();m.bumpMap=m.map;m.bumpScale=.025;m.roughness=1;}
          else {m.roughness=.82;m.metalness=0;m.side=THREE.DoubleSide;m.color.multiplyScalar(.85);}
          return m;
        });mesh.material=Array.isArray(originalMaterial)?materials:materials[0];mesh.castShadow=mesh.receiveShadow=true;
      });
      if(!Number.isFinite(radius)||radius<.01)throw new Error('Invalid botanical crown size');
      oriented.scale.setScalar(1/radius);root.userData.normalizedCrownRadius=1;
      root.traverse(o=>o.userData.sharedPreviewResource=true);trees.set(i,root);
    }
  }).catch(error=>{loading=null;throw error;});
  return loading;
}
let larch:THREE.Group|null=null;
function larchModel() {
  if(larch)return larch;
  const rng=random(573),wood:THREE.BufferGeometry[]=[],needles:THREE.BufferGeometry[]=[];
  const rod=(a:THREE.Vector3,b:THREE.Vector3,r:number)=>{const d=b.clone().sub(a),g=new THREE.CylinderGeometry(r*.35,r,d.length(),7);g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),d.normalize()));g.translate((a.x+b.x)/2,(a.y+b.y)/2,(a.z+b.z)/2);wood.push(g);};
  rod(new THREE.Vector3(),new THREE.Vector3(0,0,6.4),.065);
  for(let level=0;level<14;level++)for(let branch=0;branch<7;branch++) {
    const z=1.1+level*.36,angle=branch*Math.PI*2/7+level*.7,length=(1-level/16)*(.83+rng()*.18);
    const from=new THREE.Vector3(0,0,z),end=new THREE.Vector3(Math.cos(angle)*length,Math.sin(angle)*length,z+.12);
    rod(from,end,.016*(1-level/18));
    for(let twig=1;twig<9;twig++) {
      const t=twig/9,center=from.clone().lerp(end,t);center.z+=rng()*.12;
      const tip=center.clone().add(new THREE.Vector3(Math.cos(angle+1.1)*.12,Math.sin(angle+1.1)*.12,.10+rng()*.10));rod(center,tip,.004);
      for(let j=0;j<35;j++) {
        const c=center.clone().lerp(tip,rng()),az=rng()*Math.PI*2;
        const v=new THREE.Vector3(Math.cos(az),Math.sin(az),rng()*.9).normalize().multiplyScalar(.055+rng()*.04);
        const side=new THREE.Vector3(-Math.sin(az)*.004,Math.cos(az)*.004,0),g=new THREE.BufferGeometry();
        g.setAttribute('position',new THREE.Float32BufferAttribute([...c.clone().sub(side).toArray(),...c.clone().add(side).toArray(),...c.clone().add(v).toArray()],3));g.computeVertexNormals();needles.push(g);
      }
    }
  }
  larch=new THREE.Group();const trunk=new THREE.Mesh(mergeGeometries(wood),new THREE.MeshStandardMaterial({color:0x65503b,map:barkTexture(),roughness:1}));
  const leaves=new THREE.Mesh(mergeGeometries(needles),new THREE.MeshStandardMaterial({color:0x54743b,roughness:.9,side:THREE.DoubleSide}));
  larch.add(trunk,leaves);for(const g of [...wood,...needles])g.dispose();
  let radius=0;for(const m of [trunk,leaves]){const p=m.geometry.getAttribute('position');for(let i=0;i<p.count;i++)radius=Math.max(radius,Math.hypot(p.getX(i),p.getY(i)));}
  for(const m of [trunk,leaves]){m.geometry.scale(1/radius,1/radius,1/radius);m.castShadow=m.receiveShadow=true;}
  larch.userData.normalizedCrownRadius=1;larch.traverse(o=>o.userData.sharedPreviewResource=true);return larch;
}
export function botanicalTree(id:string,speciesId:string,radius:number) {
  let hash=0;for(const c of id)hash=(hash*31+c.charCodeAt(0))|0;
  const variant=/birch|берез/i.test(speciesId)?1:/maple|клен/i.test(speciesId)?2:Math.abs(hash)%4;
  const source=/larch|листвен/i.test(speciesId)?larchModel():trees.get(variant);if(!source)throw new Error('Tree assets are not ready');
  const root=source.clone(true);root.rotation.z=(Math.abs(hash)%628)/100;root.scale.setScalar(Math.max(radius,.15));
  return root;
}
