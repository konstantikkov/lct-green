import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { Placement, ProjectDocument } from "../api/types";
import { buildCadGroups, disposeObject } from "./cadGeometry";
import type { Theme } from "../theme";

const loader = new GLTFLoader();
const models = new Map<string, Promise<THREE.Group>>();
const requests = new WeakMap<ProjectDocument, Promise<{modelUrl:string;origin:number[]}>>();
function model(url:string) {
  let p=models.get(url);
  if(p){models.delete(url);models.set(url,p);}
  if (!p) { p=loader.loadAsync(url).then(g=>g.scene); models.set(url,p); p.catch(()=>models.delete(url)); }
  if(models.size>6){
    const oldest=[...models.keys()].find(k=>k!==url && !k.includes("editor-plants"));
    if(oldest){const retired=models.get(oldest)!;models.delete(oldest);void retired.then(disposeObject).catch(()=>{});}
  }
  return p;
}
async function environment(doc:ProjectDocument, signal:AbortSignal, status:(s:string)=>void) {
  let request=requests.get(doc);
  if (!request) {
    request=(async()=>{
      const r=await fetch(`/api/projects/${doc.projectId}/editor-preview`,{method:"POST"});
      if(!r.ok)throw new Error((await r.json()).detail?.message || "Не удалось подготовить сцену");
      const job=await r.json();
      for(let i=0;i<300;i++) {
        const response=await fetch(job.statusUrl); if(!response.ok)throw new Error("Ошибка загрузки сцены");
        const state=await response.json();
        if(state.state==="COMPLETED")return {modelUrl:job.modelUrl as string,origin:job.origin as number[]};
        if(state.state==="FAILED")throw new Error(state.error);
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
      throw new Error("Подготовка сцены занимает слишком много времени. Откройте проект повторно.");
    })(); requests.set(doc,request); request.catch(()=>requests.delete(doc));
  }
  status("Подготовка 3D-сцены Blender… Круги уже доступны для редактирования.");
  const result=await request;
  if(signal.aborted)throw new DOMException("Aborted","AbortError");
  return result;
}
function shared(object:THREE.Object3D) {
  object.traverse(o=>{
    o.userData.sharedPreviewResource=true;
    const mesh=o as THREE.Mesh;
    if(mesh.isMesh){mesh.receiveShadow=true;mesh.castShadow=true;}
  });
  return object;
}
export interface EditablePreview {
  group:THREE.Group;
  pickMeshes:{mesh:THREE.InstancedMesh;placements:Placement[]}[];
  plantGroups:Map<string,THREE.Group>;
  fog:THREE.Fog;
  detailGroup:THREE.Group;
  dispose:()=>void;
}
/** Blender-built environment + independently editable, instanced crown circles. */
export function buildEditablePreview(doc:ProjectDocument, placements:Placement[], theme:Theme,
  invalidate:()=>void,status:(s:string)=>void):EditablePreview {
  const group=new THREE.Group();group.name="blender-editor";
  const controller=new AbortController();let disposed=false;
  const plantGroups=new Map<string,THREE.Group>();
  const span=Math.max(doc.extents.max[0]-doc.extents.min[0],doc.extents.max[1]-doc.extents.min[1],10);
  const cx=(doc.extents.min[0]+doc.extents.max[0])/2,cy=(doc.extents.min[1]+doc.extents.max[1])/2;
  const fog=new THREE.Fog(theme==="dark"?0x202934:0xe8edf0,span*4,span*12);
  const grid=new THREE.GridHelper(span*1.7,40,0x7b8991,theme==="dark"?0x37444b:0xbcc6cb);
  grid.rotation.x=Math.PI/2;grid.position.set(cx,cy,-.1);group.add(grid);
  const fill=new THREE.HemisphereLight(0xf4f7fa,0x899176,2.2);fill.position.set(0,0,1);group.add(fill);
  const sun=new THREE.DirectionalLight(0xfff1dc,3.0);sun.position.set(cx+span*.4,cy-span*.6,span*.8);
  sun.target.position.set(cx,cy,0);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);
  const reach=Math.min(span*.8,400);Object.assign(sun.shadow.camera,{left:-reach,right:reach,top:reach,bottom:-reach,near:.5,far:span*4});
  sun.shadow.bias=-.0002;sun.shadow.normalBias=.12;group.add(sun,sun.target);
  const detailGroup=buildCadGroups(doc.entities,doc.layers,theme).root;
  detailGroup.name="cad-underlay3d";detailGroup.position.z=.025;group.add(detailGroup);
  const pickMeshes:EditablePreview["pickMeshes"]=[];
  for(const kind of ["TREE","SHRUB"] as const) {
    const list=placements.filter(p=>p.kind===kind);const slot=new THREE.Group();slot.name=kind==="TREE"?"trees3d":"shrubs3d";group.add(slot);
    const circles=new THREE.InstancedMesh(new THREE.RingGeometry(.95,1,64),
      new THREE.MeshBasicMaterial({color:0xffffff,side:THREE.DoubleSide,transparent:true,opacity:.95,depthTest:false,depthWrite:false}),list.length);
    circles.name="editable-planting-circles";circles.renderOrder=100;circles.frustumCulled=false;
    const matrix=new THREE.Matrix4();
    for(let i=0;i<list.length;i++) {
      const p=list[i],r=Math.max(p.crownRadiusM,.15);
      matrix.makeScale(r,r,1);matrix.setPosition(p.position[0],p.position[1],.22);circles.setMatrixAt(i,matrix);
      circles.setColorAt(i,new THREE.Color(kind==="TREE"?0x16b8a6:0x7c83e9));
      const plant=new THREE.Group();plant.name=`plant:${p.id}`;plant.position.set(p.position[0],p.position[1],.07);plantGroups.set(p.id,plant);slot.add(plant);
    }
    circles.instanceMatrix.needsUpdate=true;slot.add(circles);pickMeshes.push({mesh:circles,placements:list});
    const hitMaterial=new THREE.MeshBasicMaterial({visible:false,side:THREE.DoubleSide,transparent:true,opacity:0,depthWrite:false});
    const disks=new THREE.InstancedMesh(new THREE.CircleGeometry(1,32),hitMaterial,list.length);
    const crowns=new THREE.InstancedMesh(new THREE.SphereGeometry(1,12,8),hitMaterial,list.length);
    disks.frustumCulled=crowns.frustumCulled=false;
    for(let i=0;i<list.length;i++) {
      const p=list[i],r=Math.max(.15,p.crownRadiusM);
      matrix.makeScale(r,r,1);matrix.setPosition(p.position[0],p.position[1],.22);disks.setMatrixAt(i,matrix);
      matrix.makeScale(r,r,p.kind==="TREE"?r*1.1:r*.6);matrix.setPosition(p.position[0],p.position[1],p.kind==="TREE"?2+r:r*.6);crowns.setMatrixAt(i,matrix);
    }
    slot.add(disks,crowns);pickMeshes.push({mesh:disks,placements:list},{mesh:crowns,placements:list});
  }
  // Camera-independent geometry, reused after moves; the environment is never
  // regenerated when a proposed planting changes coordinates.
  void environment(doc,controller.signal,status).then(async job=>{
    if(disposed)return;const source=await model(job.modelUrl);if(disposed)return;
    const wrapper=new THREE.Group();wrapper.name="blender-environment3d";wrapper.rotation.x=Math.PI/2;wrapper.position.set(job.origin[0],job.origin[1],0);
    const copy=shared(source.clone(true));wrapper.add(copy);group.add(wrapper);status("3D-редактор · сцена Blender");invalidate();
  }).catch(e=>{if(!disposed && e.name!=="AbortError")status(`Сцена недоступна: ${e.message}. Круги и CAD доступны; переключите вид для повторной загрузки.`);});
  void model("/models/editor-plants.glb?v=2").then(source=>{
    if(disposed)return;
    for(let i=0;i<placements.length;i++) {
      const p=placements[i],plant=plantGroups.get(p.id)!;const lod=new THREE.LOD();
      for(const [level,distance] of [["high",0],["medium",30],["low",90]] as const) {
        const asset=source.getObjectByName(`plant_${i%4}_${level}`);if(!asset)continue;
        const mesh=shared(asset.clone(true));const wrapper=new THREE.Group();wrapper.rotation.x=Math.PI/2;wrapper.add(mesh);lod.addLevel(wrapper,distance);
      }
      const scale=p.crownRadiusM/1.45;
      lod.scale.set(scale,scale,p.kind==="TREE"?scale:scale*.32);plant.add(lod);
    }
    invalidate();
  }).catch(()=>{if(!disposed)status("Модели растений недоступны; круги остаются редактируемыми.");});
  return {group,pickMeshes,plantGroups,fog,detailGroup,dispose:()=>{disposed=true;controller.abort();}};
}
