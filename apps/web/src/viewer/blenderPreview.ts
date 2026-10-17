import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { Placement, ProjectDocument } from "../api/types";
import { buildCadGroups, disposeObject } from "./cadGeometry";
import { plantingModel, random } from "./landscapeVegetation";
import { buildPresentationContext } from "./presentationContext";
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
  contextGroup:THREE.Group;
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
  const groundCanvas=document.createElement("canvas");groundCanvas.width=groundCanvas.height=512;
  const ctx=groundCanvas.getContext("2d")!,rng=random(12);ctx.fillStyle="#66764b";ctx.fillRect(0,0,512,512);
  for(let i=0;i<42000;i++){const shade=55+rng()*45;ctx.strokeStyle=`hsla(${75+rng()*20},25%,${shade}%,.24)`;const x=rng()*512,y=rng()*512;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+(rng()-.5)*2,y-1-rng()*3);ctx.stroke();}
  const turf=new THREE.CanvasTexture(groundCanvas);turf.colorSpace=THREE.SRGBColorSpace;turf.wrapS=turf.wrapT=THREE.RepeatWrapping;turf.repeat.set(Math.max(2,(doc.extents.max[0]-doc.extents.min[0])/5),Math.max(2,(doc.extents.max[1]-doc.extents.min[1])/5));turf.anisotropy=8;
  const floor=new THREE.Mesh(new THREE.PlaneGeometry(doc.extents.max[0]-doc.extents.min[0]+16,doc.extents.max[1]-doc.extents.min[1]+16),new THREE.MeshStandardMaterial({map:turf,roughness:1}));
  floor.position.set(cx,cy,-.06);floor.receiveShadow=true;group.add(floor);
  const contextGroup=buildPresentationContext(doc,placements);group.add(contextGroup);
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
      const plant=new THREE.Group();plant.name=`plant:${p.id}`;plant.position.set(p.position[0],p.position[1],.07);plantGroups.set(p.id,plant);slot.add(plant);
      plant.add(plantingModel(p.id,r,p.kind==="SHRUB"));
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
    const copy=shared(source.clone(true));copy.traverse(o=>{if(/Landscape.ground/i.test(o.name))o.visible=false;});wrapper.add(copy);group.add(wrapper);status("3D-редактор · сцена Blender");invalidate();
  }).catch(e=>{if(!disposed && e.name!=="AbortError")status(`Сцена недоступна: ${e.message}. Круги и CAD доступны; переключите вид для повторной загрузки.`);});
  return {group,pickMeshes,plantGroups,fog,detailGroup,contextGroup,circleMeshes,dispose:()=>{disposed=true;controller.abort();}};
}
