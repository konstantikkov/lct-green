import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { Placement, ProjectDocument } from "../api/types";
import { buildCadGroups, disposeObject } from "./cadGeometry";
import type { Theme } from "../theme";

const loader = new GLTFLoader();
const models = new Map<string, Promise<THREE.Group>>();
const requests = new WeakMap<ProjectDocument, Promise<{modelUrl:string;origin:number[];sourceBuildings:number}>>();
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
        if(state.state==="COMPLETED")return {modelUrl:job.modelUrl as string,origin:job.origin as number[],sourceBuildings:Number(job.sourceBuildings??0)};
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
export async function downloadSourceModel(doc:ProjectDocument,status:(message:string)=>void) {
  const job=await environment(doc,new AbortController().signal,status);
  const link=document.createElement("a");link.href=job.modelUrl;link.download=`${doc.projectId}_cad_surfaces.glb`;link.click();
  status(`3D CAD скачан · здания из чертежа: ${job.sourceBuildings} · текстуры встроены`);
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
  circleMeshes:THREE.InstancedMesh[];
  dispose:()=>void;
}
/** Blender-built environment + independently editable, instanced crown circles. */
export function buildEditablePreview(doc:ProjectDocument, placements:Placement[], theme:Theme,
  invalidate:()=>void,status:(s:string)=>void):EditablePreview {
  const group=new THREE.Group();group.name="blender-editor";
  const controller=new AbortController();let disposed=false;
  const circleMeshes:THREE.InstancedMesh[]=[];
  const plantGroups=new Map<string,THREE.Group>();
  const span=Math.max(doc.extents.max[0]-doc.extents.min[0],doc.extents.max[1]-doc.extents.min[1],10);
  const cx=(doc.extents.min[0]+doc.extents.max[0])/2,cy=(doc.extents.min[1]+doc.extents.max[1])/2;
  const fog=new THREE.Fog(0xdce3df,span*3,span*9);
  const fill=new THREE.HemisphereLight(0xcbdde8,0x555d3c,.8);fill.position.set(0,0,1);group.add(fill);
  const sun=new THREE.DirectionalLight(0xffecd2,3.3);sun.position.set(cx-span*.55,cy-span*.5,span*.52);
  sun.target.position.set(cx,cy,0);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);
  const reach=Math.min(span*.85,450);Object.assign(sun.shadow.camera,{left:-reach,right:reach,top:reach,bottom:-reach,near:.5,far:span*4});
  sun.shadow.bias=-.00015;sun.shadow.normalBias=.035;sun.shadow.radius=3;group.add(sun,sun.target);
  const base=new THREE.Mesh(new THREE.PlaneGeometry(doc.extents.max[0]-doc.extents.min[0]+4,doc.extents.max[1]-doc.extents.min[1]+4),new THREE.MeshStandardMaterial({color:0xc7ccc9,roughness:1}));base.name="composition-ground";base.position.set(cx,cy,-.07);base.receiveShadow=true;group.add(base);
  const detailGroup=buildCadGroups(doc.entities,doc.layers,theme).root;
  detailGroup.name="cad-underlay3d";detailGroup.position.z=.025;detailGroup.visible=false;group.add(detailGroup);
  const pickMeshes:EditablePreview["pickMeshes"]=[];
  for(const kind of ["TREE","SHRUB"] as const) {
    const list=placements.filter(p=>p.kind===kind);const slot=new THREE.Group();slot.name=kind==="TREE"?"trees3d":"shrubs3d";group.add(slot);
    const circles=new THREE.InstancedMesh(new THREE.RingGeometry(.983,1,64),
      new THREE.MeshBasicMaterial({color:0xffffff,side:THREE.DoubleSide,transparent:true,opacity:.65,depthTest:false,depthWrite:false}),list.length);
    circles.name="editable-planting-circles";circles.renderOrder=100;circleMeshes.push(circles);circles.frustumCulled=false;
    const matrix=new THREE.Matrix4();
    for(let i=0;i<list.length;i++) {
      const p=list[i],r=Math.max(p.crownRadiusM,.15);
      matrix.makeScale(r,r,1);matrix.setPosition(p.position[0],p.position[1],.22);circles.setMatrixAt(i,matrix);
      circles.setColorAt(i,new THREE.Color(kind==="TREE"?0x16b8a6:0x7c83e9));
      const plant=new THREE.Group();plant.name=`plant:${p.id}`;plant.position.set(p.position[0],p.position[1],.07);plant.userData.photoClass=p.kind==="TREE"?"tree":"plant";plantGroups.set(p.id,plant);slot.add(plant);
      const lobes=p.kind==="TREE"?7:4;
      for(let lobe=0;lobe<lobes;lobe++){
        const angle=lobe*Math.PI*2/lobes;
        const crown=new THREE.Mesh(new THREE.SphereGeometry(r*.68,12,8),new THREE.MeshStandardMaterial({color:p.kind==="TREE"?0x608a44:0x759e51,roughness:1}));
        crown.scale.z=p.kind==="TREE"?1.15:.65;
        crown.position.set(Math.cos(angle)*r*.3,Math.sin(angle)*r*.3,(p.kind==="TREE"?2+r:r*.55)+Math.sin(lobe*2.4)*r*.17);
        crown.castShadow=crown.receiveShadow=true;plant.add(crown);
      }
      if(p.kind==="TREE"){
        const stem=new THREE.Mesh(new THREE.CylinderGeometry(.09,.13,2+r,8),new THREE.MeshStandardMaterial({color:0x8b7f6c,roughness:1}));
        stem.rotation.x=Math.PI/2;stem.position.z=(2+r)/2;stem.castShadow=true;plant.add(stem);
      }
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
    const copy=shared(source.clone(true));copy.traverse(o=>{
      if(/Landscape.ground|Studio.background/i.test(o.name))o.visible=false;
      const m=o as THREE.Mesh;if(!m.isMesh)return;
      const name=m.name.toLowerCase();if(/cad.fence/.test(name))o.userData.photoClass="FENCE";let category=o.userData.cadCategory;
      for(let parent=o.parent;!category&&parent;parent=parent.parent)category=parent.userData.cadCategory;
      const color=category==="BUILDING"?0xcac3b7:category==="GREEN_AREA"||/grass|green/i.test(name)?0xb0bc94:category==="ROAD"||category==="PARKING"||/road|asphalt/i.test(name)?0x8c9394:0xc5c5bc;
      m.geometry=m.geometry.clone();m.material=new THREE.MeshStandardMaterial({color,roughness:1});o.userData.sharedPreviewResource=false;
    });wrapper.add(copy);group.add(wrapper);group.userData.sourceBuildings=job.sourceBuildings;status(`Макет CAD · зданий: ${job.sourceBuildings} · выберите камеру для AI-фото`);invalidate();
  }).catch(e=>{if(!disposed && e.name!=="AbortError")status(`Сцена недоступна: ${e.message}. Круги и CAD доступны; переключите вид для повторной загрузки.`);});
  return {group,pickMeshes,plantGroups,fog,detailGroup,circleMeshes,dispose:()=>{disposed=true;controller.abort();}};
}
