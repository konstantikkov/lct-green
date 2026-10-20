import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { Placement, ProjectDocument, LawnInfo } from "../api/types";
import { buildCadGroups, disposeObject } from "./cadGeometry";
import type { Theme } from "../theme";

const loader = new GLTFLoader();
const models = new Map<string, Promise<THREE.Group>>();
export type ReconstructionSettings={buildingHeightM:number;showFences:boolean};
const requests = new WeakMap<ProjectDocument, Map<string,Promise<{modelUrl:string;origin:number[];sourceBuildings:number;sourceFences:number}>>>();
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
async function environment(doc:ProjectDocument, signal:AbortSignal, status:(s:string)=>void,settings:ReconstructionSettings={buildingHeightM:9,showFences:false}) {
  const key=JSON.stringify(settings);let cache=requests.get(doc);if(!cache){cache=new Map();requests.set(doc,cache);}
  let request=cache.get(key);
  if (!request) {
    request=(async()=>{
      const r=await fetch(`/api/projects/${doc.projectId}/editor-preview`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(settings)});
      if(!r.ok)throw new Error((await r.json()).detail?.message || "Не удалось подготовить сцену");
      const job=await r.json();
      for(let i=0;i<300;i++) {
        const response=await fetch(job.statusUrl); if(!response.ok)throw new Error("Ошибка загрузки сцены");
        const state=await response.json();
        if(state.state==="COMPLETED")return {modelUrl:job.modelUrl as string,origin:job.origin as number[],sourceBuildings:Number(job.sourceBuildings??0),sourceFences:Number(job.sourceFences??0)};
        if(state.state==="FAILED")throw new Error(state.error);
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
      throw new Error("Подготовка сцены занимает слишком много времени. Откройте проект повторно.");
    })(); cache.set(key,request); request.catch(()=>cache!.delete(key));
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
  photoMaterials:Map<THREE.Mesh,THREE.Material|THREE.Material[]>;
  groundHeight:(x:number,y:number)=>number;
  dispose:()=>void;
}
/** Blender-built environment + independently editable, instanced crown circles. */
export function buildEditablePreview(doc:ProjectDocument, placements:Placement[], theme:Theme,
  invalidate:()=>void,status:(s:string)=>void,settings:ReconstructionSettings={buildingHeightM:9,showFences:false},lawn?:LawnInfo):EditablePreview {
  const group=new THREE.Group();group.name="blender-editor";
  const controller=new AbortController();let disposed=false;
  const circleMeshes:THREE.InstancedMesh[]=[];
  const plantGroups=new Map<string,THREE.Group>();
  const photoMaterials=new Map<THREE.Mesh,THREE.Material|THREE.Material[]>();
  const span=Math.max(doc.extents.max[0]-doc.extents.min[0],doc.extents.max[1]-doc.extents.min[1],10);
  const cx=(doc.extents.min[0]+doc.extents.max[0])/2,cy=(doc.extents.min[1]+doc.extents.max[1])/2;
  const fog=new THREE.Fog(0xdce3df,span*3,span*9);
  const fill=new THREE.HemisphereLight(0xcbdde8,0x555d3c,.8);fill.position.set(0,0,1);group.add(fill);
  const sun=new THREE.DirectionalLight(0xffecd2,3.3);sun.position.set(cx-span*.55,cy-span*.5,span*.52);
  sun.target.position.set(cx,cy,0);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);
  const reach=Math.min(span*.85,450);Object.assign(sun.shadow.camera,{left:-reach,right:reach,top:reach,bottom:-reach,near:.5,far:span*4});
  sun.shadow.bias=-.00015;sun.shadow.normalBias=.035;sun.shadow.radius=3;group.add(sun,sun.target);
  const base=new THREE.Mesh(new THREE.PlaneGeometry(doc.extents.max[0]-doc.extents.min[0]+4,doc.extents.max[1]-doc.extents.min[1]+4),new THREE.MeshStandardMaterial({color:0xc3c9bb,roughness:1}));base.name="composition-ground";base.position.set(cx,cy,-.07);base.receiveShadow=true;group.add(base);
  // Actual recommended lawn polygons, including holes, are separate proposed geometry.
  const lawnMaterial=new THREE.MeshStandardMaterial({color:0x91b66b,roughness:1,side:THREE.DoubleSide});
  const groundTexture=new THREE.TextureLoader().load('/api/render-materials/ground_diff.jpg',()=>{if(!disposed)invalidate();});
  groundTexture.colorSpace=THREE.SRGBColorSpace;groundTexture.wrapS=groundTexture.wrapT=THREE.RepeatWrapping;groundTexture.anisotropy=8;lawnMaterial.map=groundTexture;
  const lawnMeshes:THREE.Mesh[]=[];
  for(const polygon of lawn?.polygons??[]){
    const shape=new THREE.Shape();for(let i=0;i<polygon.shell.length;i+=2){if(i===0)shape.moveTo(polygon.shell[i],polygon.shell[i+1]);else shape.lineTo(polygon.shell[i],polygon.shell[i+1]);}shape.closePath();
    for(const hole of polygon.holes??[]){const path=new THREE.Path();for(let i=0;i<hole.length;i+=2){if(i===0)path.moveTo(hole[i],hole[i+1]);else path.lineTo(hole[i],hole[i+1]);}path.closePath();shape.holes.push(path);}
    const geo=new THREE.ShapeGeometry(shape);const uv=geo.getAttribute('uv');for(let i=0;i<uv.count;i++)uv.setXY(i,uv.getX(i)/2,uv.getY(i)/2);
    const mesh=new THREE.Mesh(geo,new THREE.MeshStandardMaterial({color:0x91ad86,roughness:1,side:THREE.DoubleSide}));photoMaterials.set(mesh,lawnMaterial);mesh.position.z=.025;mesh.name='Proposed lawn';mesh.userData.cadCategory='GREEN_AREA';mesh.receiveShadow=true;group.add(mesh);lawnMeshes.push(mesh);
  }
  if(!lawnMeshes.length){groundTexture.dispose();lawnMaterial.dispose();}
  const detailGroup=buildCadGroups(doc.entities,doc.layers,theme).root;
  detailGroup.name="cad-underlay3d";detailGroup.position.z=.025;detailGroup.visible=false;group.add(detailGroup);
  const groundMeshes:THREE.Mesh[]=[];
  const groundRay=new THREE.Raycaster();
  const groundHeight=(x:number,y:number)=>{groundRay.set(new THREE.Vector3(x,y,10000),new THREE.Vector3(0,0,-1));return groundRay.intersectObjects(groundMeshes,false)[0]?.point.z??(base.position.z+.07);};
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
        crown.castShadow=crown.receiveShadow=true;crown.userData.crown=true;plant.add(crown);
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
  void environment(doc,controller.signal,status,settings).then(async job=>{
    if(disposed)return;const source=await model(job.modelUrl);if(disposed)return;
    const wrapper=new THREE.Group();wrapper.name="blender-environment3d";wrapper.rotation.x=Math.PI/2;wrapper.position.set(job.origin[0],job.origin[1],0);
    const copy=shared(source.clone(true));copy.traverse(o=>{
      if(/Landscape.ground|Studio.background/i.test(o.name))o.visible=false;
      const m=o as THREE.Mesh;if(!m.isMesh)return;
      const name=m.name.toLowerCase();if(/cad.fence/.test(name))o.userData.photoClass="FENCE";let category=o.userData.cadCategory;
      for(let parent=o.parent;!category&&parent;parent=parent.parent)category=parent.userData.cadCategory;
      m.geometry=m.geometry.clone();const cloneMaterial=(original:THREE.Material)=>{const mat=original.clone() as THREE.MeshStandardMaterial;mat.side=THREE.DoubleSide;for(const key of ['map','normalMap','roughnessMap','metalnessMap','aoMap'] as const){if(mat[key])mat[key]=mat[key]!.clone();}return mat;};m.material=Array.isArray(m.material)?m.material.map(cloneMaterial):cloneMaterial(m.material);if(category)o.userData.cadCategory=category;o.userData.sharedPreviewResource=false;
      const surfaceColors:Record<string,number>={BUILDING:0xeeeae5,GREEN_AREA:0x91ad86,ROAD:0x899296,SIDEWALK:0xd5cdbf,PARKING:0xa5adb0};
      const editorColor=surfaceColors[category]??(o.userData.sourceTerrain?0xc3c9bb:undefined);
      if(editorColor!==undefined){
        // Calm, readable editor colors; photographic textures are used only during capture.
        photoMaterials.set(m,m.material);
        m.material=new THREE.MeshStandardMaterial({color:editorColor,roughness:.95,side:THREE.DoubleSide});
      }
    });wrapper.add(copy);group.add(wrapper);group.updateMatrixWorld(true);
    copy.traverse(o=>{const m=o as THREE.Mesh;if(m.isMesh&&m.visible&&(["GREEN_AREA","ROAD","SIDEWALK","PARKING"].includes(m.userData.cadCategory)||m.userData.sourceTerrain))groundMeshes.push(m);});
    if(groundMeshes.some(m=>m.userData.sourceTerrain)){const bounds=new THREE.Box3();for(const mesh of groundMeshes)if(mesh.userData.sourceTerrain)bounds.expandByObject(mesh);if(!bounds.isEmpty())base.position.z=bounds.min.z-.07;}
    if(groundMeshes.some(m=>m.userData.sourceTerrain))for(const mesh of lawnMeshes){const pos=mesh.geometry.getAttribute('position');for(let i=0;i<pos.count;i++)pos.setZ(i,groundHeight(pos.getX(i),pos.getY(i)));pos.needsUpdate=true;mesh.geometry.computeVertexNormals();}
    for(const p of placements){const z=groundHeight(p.position[0],p.position[1]);const plant=plantGroups.get(p.id);if(plant)plant.position.z=z+.07;for(const slot of pickMeshes){const i=slot.placements.findIndex(v=>v.id===p.id);if(i>=0){const m=new THREE.Matrix4();slot.mesh.getMatrixAt(i,m);m.elements[14]+=z;slot.mesh.setMatrixAt(i,m);slot.mesh.instanceMatrix.needsUpdate=true;}}}
    group.userData.sourceBuildings=job.sourceBuildings;group.userData.sourceFences=job.sourceFences;status(`Макет CAD · зданий: ${job.sourceBuildings} · выберите ракурс`);invalidate();
  }).catch(e=>{if(!disposed && e.name!=="AbortError")status(`Сцена недоступна: ${e.message}. Круги и CAD доступны; переключите вид для повторной загрузки.`);});
  return {group,pickMeshes,groundHeight,plantGroups,fog,detailGroup,circleMeshes,photoMaterials,dispose:()=>{
    disposed=true;controller.abort();
    photoMaterials.forEach(material=>{
      for(const mat of Array.isArray(material)?material:[material]){
        for(const texture of new Set(Object.values(mat).filter(v=>v instanceof THREE.Texture) as THREE.Texture[]))texture.dispose();
        mat.dispose();
      }
    });
    photoMaterials.clear();
  }};
}
