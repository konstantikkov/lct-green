import * as THREE from "three";
import type {
  ChunkInfo, ConstraintsResponse, ExclusionZone, IterationDetail,
  LayerInfo, Placement, ProjectDocument,
} from "../api/types";
import { buildCadGroups, disposeObject, layerColor } from "./cadGeometry";
import { buildEditablePreview, type EditablePreview } from "./blenderPreview";
import { RGBELoader } from "three/examples/jsm/loaders/RGBELoader.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import {
  buildAllowedGroup, buildChunkGridGroup, buildLawnGroup, buildPlacementsGroup,
  buildRejectedGroup, buildZonesGroup, swapChild, Z, type PlacementsBuild,
} from "./overlayGeometry";
import type { OverlayState, ViewMode } from "../state/store";
import type { Theme } from "../theme";

export type HoverHit =
  | { kind: "placement"; placement: Placement }
  | { kind: "zone"; zone: ExclusionZone }
  | null;

export interface SceneCallbacks {
  onPreviewStatus: (text: string) => void;
  onPickPlacement: (p: Placement) => void;
  onPickZone: (zoneId: string) => void;
  onPickNothing: () => void;
  onCursor: (worldX: number, worldY: number) => void;
  onZoom: (pxPerMeter: number) => void;
  /** rAF-throttled hover; px/py are viewer-local pixels. */
  onHover: (hit: HoverHit, px: number, py: number) => void;
  /** Dragging a selected planting; x/y in meters, px/py viewer-local pixels. */
  onPlacementDrag: (p: Placement, x: number, y: number, px: number, py: number) => void;
  /** Drop at x/y (meters) — the consumer persists the move. */
  onPlacementDrop: (p: Placement, x: number, y: number) => void;
  /** Drag aborted (Escape / touch cancel) — visuals already restored. */
  onPlacementDragCancel: () => void;
}

/** Transient state of an in-progress placement drag. */
interface PlacementDragState {
  placement: Placement;
  origX: number;
  origY: number;
  lastX: number;
  lastY: number;
  moved: boolean;
  tinted: boolean;
  /** Original 3D crown instance color (restored when the tint clears). */
  orig3dColor: THREE.Color | null;
  grabOffsetX: number;
  grabOffsetY: number;
}

// 3D clear color matches the sky-dome horizon so extreme zoom-outs stay seamless.
const CLEAR_COLORS: Record<Theme, Record<ViewMode, number>> = {
  light: { "2d": 0xf6f7f5, "3d": 0xdce3df },
  dark: { "2d": 0x0b0f14, "3d": 0xdce3df },
};

/** Owns the three.js scene; imperative API driven by React effects. */
export class SceneManager {
  private presentationContext=true;
  private plantingCircles=true;
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.OrthographicCamera;
  private camera3d: THREE.PerspectiveCamera;
  private canvas: HTMLCanvasElement;
  private cb: SceneCallbacks;
  private raycaster = new THREE.Raycaster();

  private theme: Theme = "light";
  private mode: ViewMode = "2d";

  // 2D camera state (world units = meters)
  private cx = 0;
  private cy = 0;
  private halfH = 50; // half of visible height in meters
  private width = 2;
  private height = 2;

  // 3D orbit state (z-up)
  private orbit = { az: -Math.PI / 4, polar: 0.95, dist: 200, tx: 0, ty: 0, initialized: false };

  // slots (2D plan)
  private slotRoot = new THREE.Group();
  private cadRoot: THREE.Object3D | null = null;
  private cadByLayer = new Map<string, THREE.Group>();
  private vegLayers = new Set<string>();
  private zonesObj: THREE.Object3D | null = null;
  private allowedObj: THREE.Object3D | null = null;
  private chunksObj: THREE.Object3D | null = null;
  private rejectedObj: THREE.Object3D | null = null;
  private lawnObj: THREE.Object3D | null = null;
  private placementsObj: THREE.Object3D | null = null;
  private pickMeshes: PlacementsBuild["pickMeshes"] = [];
  private zoneById = new Map<string, ExclusionZone>();
  private selectionRing: THREE.Mesh;
  private selectedPlacement: Placement | null = null;

  // 3D massing
  private root3d = new THREE.Group();
  private massing: THREE.Object3D | null = null;
  private editablePreview: EditablePreview | null = null;
  private environmentMap: THREE.WebGLRenderTarget;
  private pickMeshes3d: PlacementsBuild["pickMeshes"] = [];
  private trunks3d: { mesh: THREE.InstancedMesh; indexById: Map<string, number> } | null = null;
  private massingDirty = true;
  private fog3d: THREE.Fog | null = null;
  /** "Detailed" extras subgroup (CAD underlay, lanterns, fences). */
  private detailGroup3d: THREE.Group | null = null;
  private massingDetail = true;

  // source-data caches (for theme rebuilds / 3D rebuilds)
  private doc: ProjectDocument | null = null;
  private constraints: ConstraintsResponse | null = null;
  private iteration: IterationDetail | null = null;
  private chunks: ChunkInfo[] | null = null;

  private overlays: OverlayState | null = null;
  private layerVisibility: Record<string, boolean> = {};
  private extents: { min: [number, number]; max: [number, number] } | null = null;

  private dirty = true;
  private rafId = 0;
  private disposed = false;
  private resizeObserver: ResizeObserver;

  // interaction
  private dragging = false;
  private dragButton = 0;
  private panned = false;
  private lastPx = 0;
  private lastPy = 0;
  private downPx = 0;
  private downPy = 0;

  // placement drag-move (iteration 6)
  private placementDrag: PlacementDragState | null = null;
  private scratchM = new THREE.Matrix4();

  // hover (rAF-throttled)
  private hoverPending = false;
  private hoverPx = 0;
  private hoverPy = 0;
  private lastHoverKey: string | null = null;

  // touch interaction (pointerType === "touch"; mouse path is untouched)
  private touchPoints = new Map<number, { x: number; y: number }>();
  private touchMode: "none" | "drag" | "pinch" | "movePlacement" = "none";
  private tapCandidate = false;
  private touchDownTime = 0;
  private pinchDist = 1;
  private pinchMidX = 0;
  private pinchMidY = 0;

  constructor(canvas: HTMLCanvasElement, cb: SceneCallbacks, theme: Theme) {
    this.canvas = canvas;
    this.cb = cb;
    this.theme = theme;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(CLEAR_COLORS[theme]["2d"], 1);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    // ACES tone mapping only in 3D; the 2D plan keeps exact flat colors.
    this.renderer.toneMapping = THREE.NoToneMapping;
    this.renderer.shadowMap.enabled = true; // only 3D materials/lights use it
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.shadowMap.autoUpdate=false;this.renderer.shadowMap.needsUpdate=true;
    const pmrem=new THREE.PMREMGenerator(this.renderer);
    const room=new RoomEnvironment();this.environmentMap=pmrem.fromScene(room,.04);
    room.dispose();pmrem.dispose();
    void new RGBELoader().loadAsync("/maps/garden-environment.hdr").then(texture=>{
      if(this.disposed){texture.dispose();return;}
      const generator=new THREE.PMREMGenerator(this.renderer);
      const outdoor=generator.fromEquirectangular(texture);generator.dispose();texture.dispose();
      this.environmentMap.dispose();this.environmentMap=outdoor;
      if(this.mode==="3d"){this.scene.environment=outdoor.texture;this.scene.background=outdoor.texture;}
      this.scene.environmentRotation.set(Math.PI/2,0,.25);this.scene.backgroundRotation.copy(this.scene.environmentRotation);this.scene.backgroundBlurriness=.15;this.scene.backgroundIntensity=.75;this.invalidate();
    }).catch(()=>{});

    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
    this.camera.position.set(0, 0, 100);
    this.camera.lookAt(0, 0, 0);

    this.camera3d = new THREE.PerspectiveCamera(50, 1, 0.5, 100000);
    this.camera3d.up.set(0, 0, 1);

    // Pointer events handle panning/pinching themselves — never the browser.
    canvas.style.touchAction = "none";

    this.scene.add(this.slotRoot);
    this.root3d.visible = false;
    this.scene.add(this.root3d);

    const ringGeo = new THREE.RingGeometry(1.05, 1.18, 40);
    this.selectionRing = new THREE.Mesh(
      ringGeo,
      new THREE.MeshBasicMaterial({ color: 0xfbbf24, transparent: true, opacity: 0.95, depthWrite: false }),
    );
    this.selectionRing.visible = false;
    this.selectionRing.position.z = Z.selection;
    this.selectionRing.raycast = () => {};
    this.scene.add(this.selectionRing);

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas.parentElement ?? canvas);
    this.resize();
    this.bindEvents();
    this.loop();
  }

  // ---------- public API ----------

  setDocument(doc: ProjectDocument | null) {
    this.doc = doc;
    if (this.cadRoot) {
      this.slotRoot.remove(this.cadRoot);
      disposeObject(this.cadRoot);
      this.cadRoot = null;
    }
    this.cadByLayer.clear();
    this.vegLayers.clear();
    if (doc) {
      const { root, byLayer } = buildCadGroups(doc.entities, doc.layers, this.theme);
      this.cadRoot = root;
      this.cadByLayer = byLayer;
      for (const l of doc.layers) {
        const cat = l.classification.category;
        if (cat === "EXISTING_TREE" || cat === "EXISTING_SHRUB") this.vegLayers.add(l.name);
      }
      this.slotRoot.add(root);
      this.extents = doc.extents;
      this.orbit.initialized = false;
      this.fitToExtents();
    } else {
      this.extents = null;
    }
    this.massingDirty = true;
    if (this.mode === "3d") this.rebuild3d();
    this.applyVisibility();
    this.invalidate();
  }

  updateLayerInfo(layers: LayerInfo[]) {
    // Re-derive vegetation membership and colors after classification overrides.
    if (this.doc) this.doc = { ...this.doc, layers };
    this.vegLayers.clear();
    for (const l of layers) {
      const cat = l.classification.category;
      if (cat === "EXISTING_TREE" || cat === "EXISTING_SHRUB") this.vegLayers.add(l.name);
      const group = this.cadByLayer.get(l.name);
      if (group) {
        const color = new THREE.Color(layerColor(l, this.theme));
        group.traverse((child) => {
          const mat = (child as THREE.Mesh).material as THREE.Material | undefined;
          if (mat && "color" in mat) (mat as THREE.MeshBasicMaterial).color.copy(color);
        });
      }
    }
    this.massingDirty = true;
    if (this.mode === "3d") this.rebuild3d();
    this.applyVisibility();
    this.invalidate();
  }

  setConstraints(c: ConstraintsResponse | null) {
    this.constraints = c;
    this.zonesObj = swapChild(this.slotRoot, this.zonesObj, c ? buildZonesGroup(c.exclusionZones, this.theme) : null);
    this.allowedObj = swapChild(this.slotRoot, this.allowedObj, c ? buildAllowedGroup(c, this.theme) : null);
    this.zoneById.clear();
    if (c) for (const z of c.exclusionZones) this.zoneById.set(z.id, z);
    this.massingDirty = true; // 3D allowed-area patches derive from constraints
    if (this.mode === "3d") this.rebuild3d();
    this.applyVisibility();
    this.invalidate();
  }

  setIteration(it: IterationDetail | null) {
    this.iteration = it;
    const build = it ? buildPlacementsGroup(it.placements, this.theme) : null;
    this.placementsObj = swapChild(this.slotRoot, this.placementsObj, build?.group ?? null);
    this.pickMeshes = build?.pickMeshes ?? [];
    this.rejectedObj = swapChild(
      this.slotRoot, this.rejectedObj,
      it ? buildRejectedGroup(it.rejectedCandidates, this.theme) : null,
    );
    this.lawnObj = swapChild(
      this.slotRoot, this.lawnObj,
      it?.lawn?.polygons?.length ? buildLawnGroup(it.lawn.polygons, this.theme) : null,
    );
    this.massingDirty = true;
    if (this.mode === "3d") this.rebuild3d();
    this.applyVisibility();
    this.invalidate();
  }

  setChunks(chunks: ChunkInfo[] | null) {
    this.chunks = chunks;
    this.chunksObj = swapChild(this.slotRoot, this.chunksObj, chunks ? buildChunkGridGroup(chunks, this.theme) : null);
    this.applyVisibility();
    this.invalidate();
  }

  setOverlays(o: OverlayState) {
    this.overlays = o;
    this.applyVisibility();
    this.invalidate();
  }

  setLayerVisibility(v: Record<string, boolean>) {
    this.layerVisibility = v;
    this.applyVisibility();
    this.invalidate();
  }

  /** Simple ⇄ Detailed 3D massing — pure visibility toggle, no rebuild. */
  setMassingDetail(detailed: boolean) {
    if (detailed === this.massingDetail) return;
    this.massingDetail = detailed;
    if (this.detailGroup3d) this.detailGroup3d.visible = detailed;
    this.invalidate();
  }

  downloadPresentationImage() {
    if(this.mode!=="3d")return;
    const selected=this.selectionRing.visible;
    try {
      this.selectionRing.visible=false;
      this.editablePreview?.circleMeshes.forEach(m=>(m.material as THREE.MeshBasicMaterial).opacity=0);
      this.renderer.render(this.scene,this.camera3d);
      const link=document.createElement("a");
      link.download=`${this.doc?.projectId??"landscape"}_${this.presentationContext?"concept":"plan"}.png`;
      link.href=this.canvas.toDataURL("image/png");link.click();
    } finally {
      this.selectionRing.visible=selected;this.setPlantingCircles(this.plantingCircles);
    }
  }

  setPresentationCamera(preset:"aerial"|"garden") {
    if(preset==="aerial"){this.fit3d();return;}
    if(!this.extents)return;
    const [x0,y0]=this.extents.min,[x1,y1]=this.extents.max;
    const w=Math.max(x1-x0,10),h=Math.max(y1-y0,10);
    this.orbit.tx=(x0+x1)/2;this.orbit.ty=(y0+y1)/2;
    const dx=w*.22,dy=-h*.92,z=Math.max(3.5,Math.min(7,w*.10));
    this.orbit.dist=Math.hypot(dx,dy,z);this.orbit.az=Math.atan2(dy,dx);this.orbit.polar=Math.acos(z/this.orbit.dist);this.orbit.initialized=true;
    this.update3dCamera();
  }

  setPresentationContext(show:boolean) {
    this.presentationContext=show;if(this.editablePreview)this.editablePreview.contextGroup.visible=show;this.invalidate();
  }
  setPlantingCircles(show:boolean) {
    this.plantingCircles=show;
    this.editablePreview?.circleMeshes.forEach(m=>{(m.material as THREE.MeshBasicMaterial).opacity=show?.65:0;});
    this.invalidate();
  }

  setSelectedPlacement(p: Placement | null) {
    this.selectedPlacement = p;
    this.updateSelectionRing();
    this.invalidate();
  }

  /** Tints the dragged planting red while the live check reports violations. */
  setDragInvalid(invalid: boolean) {
    const d = this.placementDrag;
    if (!d || d.tinted === invalid) return;
    this.applyDragTint(d, invalid);
  }

  /** Live theme switch: recolors CAD linework and rebuilds tinted overlays. */
  setTheme(theme: Theme) {
    if (theme === this.theme) return;
    this.theme = theme;
    this.renderer.setClearColor(CLEAR_COLORS[theme][this.mode], 1);
    if (this.doc) this.updateLayerInfo(this.doc.layers);
    this.setConstraints(this.constraints);
    this.setIteration(this.iteration);
    this.setChunks(this.chunks);
    // the massing model is palette-baked — rebuild immediately when visible
    if (this.mode === "3d") this.rebuild3d();
    this.invalidate();
  }

  setMode(mode: ViewMode) {
    if (mode === this.mode) return;
    this.mode = mode;
    this.renderer.setClearColor(CLEAR_COLORS[this.theme][mode], 1);
    this.renderer.toneMapping = mode === "3d" ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping;
    this.scene.fog = mode === "3d" ? this.fog3d : null;
    this.scene.environment = mode === "3d" ? this.environmentMap.texture : null;
    this.scene.background = mode === "3d" ? this.environmentMap.texture : null;
    this.renderer.toneMappingExposure = mode === "3d" ? .88 : 1;
    this.scene.environmentIntensity=mode === "3d" ? .35 : 1;
    if (mode === "3d") {
      if (this.massingDirty) this.rebuild3d();
      if (!this.orbit.initialized) this.fit3d();
      this.update3dCamera();
    } else {
      this.updateCamera();
    }
    this.slotRoot.visible = mode === "2d";
    this.root3d.visible = mode === "3d";
    this.updateSelectionRing();
    this.applyVisibility();
    this.invalidate();
  }

  fitToExtents() {
    if (this.mode === "3d") {
      this.fit3d();
      return;
    }
    if (!this.extents) return;
    const [minX, minY] = this.extents.min;
    const [maxX, maxY] = this.extents.max;
    const w = Math.max(maxX - minX, 1e-6);
    const h = Math.max(maxY - minY, 1e-6);
    this.cx = (minX + maxX) / 2;
    this.cy = (minY + maxY) / 2;
    const aspect = this.width / this.height;
    this.halfH = Math.max(h / 2, w / 2 / aspect) * 1.06;
    this.updateCamera();
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.rafId);
    this.resizeObserver.disconnect();
    window.removeEventListener("keydown", this.onKeyDown);
    this.setDocument(null);
    this.setConstraints(null);
    this.setIteration(null);
    this.setChunks(null);
    if (this.massing) {
      this.root3d.remove(this.massing);
      disposeObject(this.massing);
      this.massing = null;
    }
    this.selectionRing.geometry.dispose();
    (this.selectionRing.material as THREE.Material).dispose();
    this.editablePreview?.dispose();
    this.environmentMap.dispose();
    this.renderer.dispose();
  }

  // ---------- internals ----------

  private invalidate(updateShadows=false) {
    this.dirty = true;
    if(updateShadows)this.renderer.shadowMap.needsUpdate=true;
  }

  private activeCamera(): THREE.Camera {
    return this.mode === "3d" ? this.camera3d : this.camera;
  }

  private loop = () => {
    if (this.disposed) return;
    if (this.hoverPending && !this.dragging && !this.placementDrag) {
      this.hoverPending = false;
      this.processHover();
    }
    if (this.dirty) {
      this.dirty = false;
      this.renderer.render(this.scene, this.activeCamera());
    }
    this.rafId = requestAnimationFrame(this.loop);
  };

  private resize() {
    const el = this.canvas.parentElement;
    if (!el) return;
    const w = Math.max(el.clientWidth, 2);
    const h = Math.max(el.clientHeight, 2);
    this.width = w;
    this.height = h;
    this.renderer.setSize(w, h, false);
    this.camera3d.aspect = w / h;
    this.camera3d.updateProjectionMatrix();
    this.updateCamera();
  }

  private updateCamera() {
    const aspect = this.width / this.height;
    this.camera.left = this.cx - this.halfH * aspect;
    this.camera.right = this.cx + this.halfH * aspect;
    this.camera.top = this.cy + this.halfH;
    this.camera.bottom = this.cy - this.halfH;
    this.camera.updateProjectionMatrix();
    if (this.mode === "2d") this.cb.onZoom(this.height / (2 * this.halfH));
    this.invalidate();
  }

  private fit3d() {
    if (!this.extents) return;
    const [minX, minY] = this.extents.min;
    const [maxX, maxY] = this.extents.max;
    const w = Math.max(maxX - minX, 10);
    const h = Math.max(maxY - minY, 10);
    this.orbit.tx = (minX + maxX) / 2;
    this.orbit.ty = (minY + maxY) / 2;
    const radius = Math.max(Math.hypot(w, h) * 0.46 / Math.min(this.camera3d.aspect,1.25), Math.max(w,h)*.40);
    this.orbit.dist = radius / Math.tan((this.camera3d.fov * Math.PI) / 360);
    this.orbit.az = -Math.PI / 4;
    this.orbit.polar = .90;
    this.orbit.initialized = true;
    this.update3dCamera();
  }

  private update3dCamera() {
    const { az, polar, dist, tx, ty } = this.orbit;
    const sp = Math.sin(polar);
    this.camera3d.position.set(
      tx + dist * sp * Math.cos(az),
      ty + dist * sp * Math.sin(az),
      Math.max(dist * Math.cos(polar), 0.5),
    );
    this.camera3d.near = Math.max(dist / 500, 0.1);
    this.camera3d.far = dist * 100;
    this.camera3d.updateProjectionMatrix();
    this.camera3d.lookAt(tx, ty, Math.min(3,this.orbit.dist*.045));
    this.invalidate();
  }

  private rebuild3d() {
    this.editablePreview?.dispose();this.editablePreview=null;
    if(this.massing){this.root3d.remove(this.massing);disposeObject(this.massing);this.massing=null;}
    this.pickMeshes3d=[];this.trunks3d=null;this.fog3d=null;this.detailGroup3d=null;
    if(this.doc){
      const build=buildEditablePreview(this.doc,this.iteration?.placements??[],this.theme,
        ()=>this.invalidate(true),text=>{
          if(text.startsWith("Сцена недоступна")||text.startsWith("Модели растений"))this.massingDirty=true;
          this.cb.onPreviewStatus(text);
        });
      this.editablePreview=build;build.contextGroup.visible=this.presentationContext;build.circleMeshes.forEach(m=>m.material instanceof THREE.Material && (m.material.opacity=this.plantingCircles?.65:0));this.massing=build.group;this.pickMeshes3d=build.pickMeshes;
      this.fog3d=build.fog;this.detailGroup3d=build.detailGroup;
      this.detailGroup3d.visible=this.massingDetail;this.root3d.add(build.group);
    }
    if(this.mode==="3d")this.scene.fog=this.fog3d;
    this.massingDirty=false;this.applyVisibility();this.invalidate(true);
  }

  private updateSelectionRing() {
    const p = this.selectedPlacement;
    if (p) {
      const r = Math.max(p.crownRadiusM, 0.15);
      this.selectionRing.scale.set(r, r, 1);
      // In 3D the ring lies flat on the ground under the tree (above ribbons).
      const z = this.mode === "3d" ? 0.2 : Z.selection;
      this.selectionRing.position.set(p.position[0], p.position[1], z);
      this.selectionRing.visible = true;
    } else {
      this.selectionRing.visible = false;
    }
  }

  private screenToWorld(px: number, py: number): [number, number] {
    const mPerPx = (2 * this.halfH) / this.height;
    return [
      this.cx + (px - this.width / 2) * mPerPx,
      this.cy - (py - this.height / 2) * mPerPx,
    ];
  }

  /** Ray/ground-plane (z=0) intersection for 3D cursor readout. */
  private groundPoint(px: number, py: number): [number, number] | null {
    const ndc = new THREE.Vector2((px / this.width) * 2 - 1, -(py / this.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera3d);
    const { origin, direction } = this.raycaster.ray;
    if (Math.abs(direction.z) < 1e-8) return null;
    const t = -origin.z / direction.z;
    if (t < 0) return null;
    return [origin.x + direction.x * t, origin.y + direction.y * t];
  }

  // ---------- placement drag-move (iteration 6) ----------

  /** Finds the instanced-mesh slot of a placement in a pick-mesh list. */
  private instanceOf(
    list: PlacementsBuild["pickMeshes"], id: string,
  ): { mesh: THREE.InstancedMesh; index: number } | null {
    for (const { mesh, placements } of list) {
      const i = placements.findIndex((p) => p.id === id);
      if (i >= 0) return { mesh, index: i };
    }
    return null;
  }

  /** Moves every visual of a placement (2D symbol, 3D crown+trunk, ring). */
  private movePlacementVisual(id: string, x: number, y: number) {
    const m = this.scratchM;
    const shift = (mesh: THREE.InstancedMesh, index: number) => {
      mesh.getMatrixAt(index, m);
      m.elements[12] = x;
      m.elements[13] = y;
      mesh.setMatrixAt(index, m);
      mesh.instanceMatrix.needsUpdate = true;
      mesh.boundingSphere=null;mesh.boundingBox=null;
    };
    const hit2d = this.instanceOf(this.pickMeshes, id);
    if (hit2d) shift(hit2d.mesh, hit2d.index);
    for(const {mesh,placements} of this.pickMeshes3d){
      const i=placements.findIndex(p=>p.id===id);if(i>=0)shift(mesh,i);
    }
    const plant=this.editablePreview?.plantGroups.get(id);
    if(plant){plant.position.x=x;plant.position.y=y;}
    const trunkIdx = this.trunks3d?.indexById.get(id);
    if (this.trunks3d && trunkIdx != null) shift(this.trunks3d.mesh, trunkIdx);
    if (this.selectedPlacement?.id === id) {
      this.selectionRing.position.x = x;
      this.selectionRing.position.y = y;
    }
    this.invalidate(true);
  }

  private applyDragTint(d: PlacementDragState, invalid: boolean) {
    d.tinted = invalid;
    const red = new THREE.Color(0xef4444);
    const white = new THREE.Color(0xffffff);
    const hit2d = this.instanceOf(this.pickMeshes, d.placement.id);
    if (hit2d) {
      // setColorAt allocates a zero-filled buffer — prefill with white first
      // (instanceColor multiplies the symbol texture).
      if (!hit2d.mesh.instanceColor) {
        for (let i = 0; i < hit2d.mesh.count; i++) hit2d.mesh.setColorAt(i, white);
      }
      hit2d.mesh.setColorAt(hit2d.index, invalid ? red : white);
      if (hit2d.mesh.instanceColor) hit2d.mesh.instanceColor.needsUpdate = true;
    }
    const hit3d = this.instanceOf(this.pickMeshes3d, d.placement.id);
    if (hit3d) {
      hit3d.mesh.setColorAt(hit3d.index, invalid ? red : (d.orig3dColor ?? white));
      if (hit3d.mesh.instanceColor) hit3d.mesh.instanceColor.needsUpdate = true;
    }
    this.invalidate();
  }

  private startPlacementDrag(p: Placement) {
    let orig3dColor: THREE.Color | null = null;
    const hit3d = this.instanceOf(this.pickMeshes3d, p.id);
    if (hit3d?.mesh.instanceColor) {
      orig3dColor = new THREE.Color();
      hit3d.mesh.getColorAt(hit3d.index, orig3dColor);
    }
    this.placementDrag = {
      placement: p,
      origX: p.position[0],
      origY: p.position[1],
      lastX: p.position[0],
      lastY: p.position[1],
      moved: false,
      tinted: false,
      orig3dColor,
      grabOffsetX:0,grabOffsetY:0,
    };
    const box=this.canvas.getBoundingClientRect();
    const grab=this.mode==="2d"?this.screenToWorld(this.downPx-box.left,this.downPy-box.top):this.groundPoint(this.downPx-box.left,this.downPy-box.top);
    if(grab){this.placementDrag.grabOffsetX=p.position[0]-grab[0];this.placementDrag.grabOffsetY=p.position[1]-grab[1];}
    this.canvas.style.cursor = "grabbing";
    this.clearHover();
  }

  /** clientX/Y in screen px, px/py viewer-local px (world-lookup source). */
  private updatePlacementDrag(clientX: number, clientY: number, px: number, py: number) {
    const d = this.placementDrag;
    if (!d) return;
    // 3px dead zone distinguishes click (re-pick) from an actual move.
    if (!d.moved && Math.hypot(clientX - this.downPx, clientY - this.downPy) < 3) return;
    d.moved = true;
    const wp = this.mode === "2d" ? this.screenToWorld(px, py) : this.groundPoint(px, py);
    if (!wp) return; // 3D ray missed the ground plane — keep the last position
    d.lastX=wp[0]+d.grabOffsetX;d.lastY=wp[1]+d.grabOffsetY;
    this.movePlacementVisual(d.placement.id,d.lastX,d.lastY);
    this.cb.onPlacementDrag(d.placement,d.lastX,d.lastY,px,py);
  }

  /** Drop: commit if actually moved, otherwise treat as a plain click. */
  private finishPlacementDrag() {
    const d = this.placementDrag;
    if (!d) return;
    this.placementDrag = null;
    if (d.tinted) this.applyDragTint(d, false);
    this.canvas.style.cursor = "grab";
    if (d.moved) this.cb.onPlacementDrop(d.placement, d.lastX, d.lastY);
    else this.cb.onPickPlacement(d.placement);
  }

  /** Escape / pointer-cancel: restore the original position. */
  private cancelPlacementDrag() {
    const d = this.placementDrag;
    if (!d) return;
    this.placementDrag = null;
    if (d.tinted) this.applyDragTint(d, false);
    this.movePlacementVisual(d.placement.id, d.origX, d.origY);
    this.canvas.style.cursor = "default";
    this.cb.onPlacementDragCancel();
  }

  private applyVisibility() {
    const o = this.overlays;
    for (const [name, group] of this.cadByLayer) {
      const layerOn = this.layerVisibility[name] !== false;
      const isVeg = this.vegLayers.has(name);
      const overlayOn = o == null ? true : isVeg ? o.existingVegetation : o.originalCad;
      group.visible = layerOn && overlayOn;
    }
    if (this.zonesObj) this.zonesObj.visible = o?.exclusionZones !== false;
    if (this.allowedObj) this.allowedObj.visible = o?.allowedArea !== false;
    if (this.chunksObj) this.chunksObj.visible = o?.chunkGrid === true;
    if (this.rejectedObj) this.rejectedObj.visible = o?.rejectedCandidates === true;
    if (this.lawnObj) this.lawnObj.visible = o?.lawn === true;
    if (this.placementsObj) {
      for (const child of this.placementsObj.children) {
        if (child.name === "trees") child.visible = o?.trees !== false;
        if (child.name === "shrubs") child.visible = o?.shrubs !== false;
      }
    }
    if (this.massing) {
      for (const child of this.massing.children) {
        if (child.name === "trees3d") child.visible = o?.trees !== false;
        if (child.name === "shrubs3d") child.visible = o?.shrubs !== false;
        if (child.name === "existingVeg3d") child.visible = o?.existingVegetation !== false;
      }
    }
  }

  private pickAt(px: number, py: number): HoverHit {
    const ndc = new THREE.Vector2((px / this.width) * 2 - 1, -(py / this.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.activeCamera());

    // Placements first (drawn on top).
    const meshes = this.mode === "3d" ? this.pickMeshes3d : this.pickMeshes;
    for (const { mesh, placements } of meshes) {
      if (!mesh.parent?.visible || !mesh.visible) continue;
      const hits = this.raycaster.intersectObject(mesh, false);
      if (hits.length > 0 && hits[0].instanceId != null) {
        return { kind: "placement", placement: placements[hits[0].instanceId] };
      }
    }
    if (this.mode === "2d" && this.zonesObj?.visible) {
      const hits = this.raycaster.intersectObject(this.zonesObj, true);
      for (const hit of hits) {
        const id = hit.object.userData.zoneId as string | undefined;
        if (id) {
          const zone = this.zoneById.get(id);
          if (zone) return { kind: "zone", zone };
        }
      }
    }
    return null;
  }

  private processHover() {
    const hit = this.pickAt(this.hoverPx, this.hoverPy);
    // The selected planting is draggable — signal it with a grab cursor.
    this.canvas.style.cursor = hit
      ? hit.kind === "placement" && hit.placement.id === this.selectedPlacement?.id
        ? "grab"
        : "pointer"
      : "default";
    const key = hit == null
      ? null
      : hit.kind === "placement" ? `p:${hit.placement.id}` : `z:${hit.zone.id}`;
    // Always report position so the tooltip follows the cursor; the consumer
    // writes straight to the DOM, so this stays cheap.
    if (key !== null || this.lastHoverKey !== null) {
      this.cb.onHover(hit, this.hoverPx, this.hoverPy);
    }
    this.lastHoverKey = key;
  }

  private clearHover() {
    if (this.lastHoverKey !== null) {
      this.lastHoverKey = null;
      this.cb.onHover(null, 0, 0);
    }
    this.hoverPending = false;
  }

  private bindEvents() {
    const c = this.canvas;
    c.addEventListener("contextmenu", (e) => e.preventDefault());

    c.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "touch") {
        this.onTouchDown(e);
        return;
      }
      if (e.button !== 0 && e.button !== 1 && e.button !== 2) return;
      // Pointer-down on the SELECTED planting starts a drag-move; anywhere
      // else keeps the existing pan/orbit behavior.
      if (e.button === 0 && this.selectedPlacement) {
        const rect = c.getBoundingClientRect();
        const hit = this.pickAt(e.clientX - rect.left, e.clientY - rect.top);
        if (hit?.kind === "placement" && hit.placement.id === this.selectedPlacement.id) {
          this.downPx = e.clientX;
          this.downPy = e.clientY;
          c.setPointerCapture(e.pointerId);
          this.startPlacementDrag(hit.placement);
          return;
        }
      }
      this.dragging = true;
      this.dragButton = e.button;
      this.panned = false;
      this.lastPx = this.downPx = e.clientX;
      this.lastPy = this.downPy = e.clientY;
      c.setPointerCapture(e.pointerId);
      this.clearHover();
    });

    c.addEventListener("pointermove", (e) => {
      if (e.pointerType === "touch") {
        this.onTouchMove(e);
        return;
      }
      const rect = c.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;

      if (this.mode === "2d") {
        const [wx, wy] = this.screenToWorld(px, py);
        this.cb.onCursor(wx, wy);
      } else {
        const gp = this.groundPoint(px, py);
        if (gp) this.cb.onCursor(gp[0], gp[1]);
      }

      if (this.placementDrag) {
        this.updatePlacementDrag(e.clientX, e.clientY, px, py);
        return;
      }

      if (this.dragging) {
        // 3px dead zone distinguishes click (pick) from drag.
        if (!this.panned) {
          this.panned = Math.hypot(e.clientX - this.downPx, e.clientY - this.downPy) >= 3;
        }
        if (this.panned) {
          const dx = e.clientX - this.lastPx;
          const dy = e.clientY - this.lastPy;
          if (this.mode === "2d") {
            const mPerPx = (2 * this.halfH) / this.height;
            this.cx -= dx * mPerPx;
            this.cy += dy * mPerPx;
            this.updateCamera();
          } else if (this.dragButton === 0) {
            // orbit
            this.orbit.az -= dx * 0.005;
            this.orbit.polar = Math.min(Math.max(this.orbit.polar - dy * 0.005, 0.08), 1.5);
            this.update3dCamera();
          } else {
            // right/middle drag — pan on ground plane
            const { az, dist } = this.orbit;
            const s = dist * 0.0011;
            const fx = -Math.cos(az), fy = -Math.sin(az); // camera forward on ground
            const rx = fy, ry = -fx;                       // camera right on ground
            this.orbit.tx += (-rx * dx + fx * dy) * s;
            this.orbit.ty += (-ry * dx + fy * dy) * s;
            this.update3dCamera();
          }
        }
        this.lastPx = e.clientX;
        this.lastPy = e.clientY;
      } else {
        this.hoverPx = px;
        this.hoverPy = py;
        this.hoverPending = true; // resolved on the next animation frame
      }
    });

    c.addEventListener("pointerleave", () => {
      if (!this.dragging) this.clearHover();
    });

    c.addEventListener("pointercancel", (e) => {
      if (e.pointerType === "touch") {
        this.onTouchUp(e, true);
      } else if (this.placementDrag) {
        this.cancelPlacementDrag();
        if (c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId);
      } else if (this.dragging) {
        this.dragging = false;
        if (c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId);
      }
    });

    c.addEventListener("pointerup", (e) => {
      if (e.pointerType === "touch") {
        this.onTouchUp(e, false);
        return;
      }
      if (this.placementDrag) {
        if (c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId);
        this.finishPlacementDrag();
        return;
      }
      if (!this.dragging) {
        // e.g. drag cancelled via Escape while the button was still down
        if (c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId);
        return;
      }
      this.dragging = false;
      c.releasePointerCapture(e.pointerId);
      if (!this.panned && e.button === 0) {
        const rect = c.getBoundingClientRect();
        const hit = this.pickAt(e.clientX - rect.left, e.clientY - rect.top);
        if (hit?.kind === "placement") this.cb.onPickPlacement(hit.placement);
        else if (hit?.kind === "zone") this.cb.onPickZone(hit.zone.id);
        else this.cb.onPickNothing();
      }
    });

    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const factor = Math.pow(1.0015, e.deltaY);
      if (this.mode === "2d") {
        const rect = c.getBoundingClientRect();
        const px = e.clientX - rect.left;
        const py = e.clientY - rect.top;
        const [wx, wy] = this.screenToWorld(px, py);
        this.halfH = Math.min(Math.max(this.halfH * factor, 0.05), 1e6);
        // keep the world point under the cursor fixed
        const mPerPx = (2 * this.halfH) / this.height;
        this.cx = wx - (px - this.width / 2) * mPerPx;
        this.cy = wy + (py - this.height / 2) * mPerPx;
        this.updateCamera();
      } else {
        this.orbit.dist = Math.min(Math.max(this.orbit.dist * factor, 2), 1e6);
        this.update3dCamera();
      }
    }, { passive: false });

    window.addEventListener("keydown", this.onKeyDown);
  }

  // ---------- touch (pointerType === "touch") ----------
  // 2D: one-finger drag = pan, pinch = zoom to midpoint, tap = pick.
  // 3D: one-finger drag = orbit, pinch = dolly, two-finger drag = pan, tap = pick.
  // No hover on touch — selection happens via tap only.

  private onTouchDown(e: PointerEvent) {
    this.touchPoints.set(e.pointerId, { x: e.clientX, y: e.clientY });
    this.canvas.setPointerCapture(e.pointerId);
    this.clearHover();
    if (this.touchPoints.size === 1) {
      this.tapCandidate = true;
      this.touchDownTime = performance.now();
      this.lastPx = this.downPx = e.clientX;
      this.lastPy = this.downPy = e.clientY;
      // Touch on the SELECTED planting drags it; anywhere else pans as before.
      if (this.selectedPlacement) {
        const rect = this.canvas.getBoundingClientRect();
        const hit = this.pickAt(e.clientX - rect.left, e.clientY - rect.top);
        if (hit?.kind === "placement" && hit.placement.id === this.selectedPlacement.id) {
          this.touchMode = "movePlacement";
          this.startPlacementDrag(hit.placement);
          return;
        }
      }
      this.touchMode = "drag";
    } else if (this.touchPoints.size === 2) {
      // A second finger during a placement move aborts it — pinch wins.
      if (this.touchMode === "movePlacement") this.cancelPlacementDrag();
      this.touchMode = "pinch";
      this.tapCandidate = false;
      this.updatePinchBase();
    } else {
      this.tapCandidate = false;
    }
  }

  /** Re-anchors pinch distance/midpoint from the current two touch points. */
  private updatePinchBase() {
    const pts = [...this.touchPoints.values()];
    if (pts.length < 2) return;
    this.pinchDist = Math.max(Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y), 1);
    this.pinchMidX = (pts[0].x + pts[1].x) / 2;
    this.pinchMidY = (pts[0].y + pts[1].y) / 2;
  }

  private onTouchMove(e: PointerEvent) {
    const pt = this.touchPoints.get(e.pointerId);
    if (!pt) return;
    pt.x = e.clientX;
    pt.y = e.clientY;

    if (this.touchMode === "pinch" && this.touchPoints.size >= 2) {
      const pts = [...this.touchPoints.values()];
      const dist = Math.max(Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y), 1);
      const midX = (pts[0].x + pts[1].x) / 2;
      const midY = (pts[0].y + pts[1].y) / 2;
      const scale = this.pinchDist / dist; // > 1 → zoom out
      const rect = this.canvas.getBoundingClientRect();
      if (this.mode === "2d") {
        // The world point under the previous midpoint stays under the current
        // midpoint — this yields pinch-zoom + two-finger pan in one formula.
        const [wx, wy] = this.screenToWorld(this.pinchMidX - rect.left, this.pinchMidY - rect.top);
        this.halfH = Math.min(Math.max(this.halfH * scale, 0.05), 1e6);
        const mPerPx = (2 * this.halfH) / this.height;
        const px = midX - rect.left;
        const py = midY - rect.top;
        this.cx = wx - (px - this.width / 2) * mPerPx;
        this.cy = wy + (py - this.height / 2) * mPerPx;
        this.updateCamera();
      } else {
        // dolly + two-finger pan on the ground plane
        this.orbit.dist = Math.min(Math.max(this.orbit.dist * scale, 2), 1e6);
        const dx = midX - this.pinchMidX;
        const dy = midY - this.pinchMidY;
        const { az, dist } = this.orbit;
        const s = dist * 0.0011;
        const fx = -Math.cos(az), fy = -Math.sin(az);
        const rx = fy, ry = -fx;
        this.orbit.tx += (-rx * dx + fx * dy) * s;
        this.orbit.ty += (-ry * dx + fy * dy) * s;
        this.update3dCamera();
      }
      this.pinchDist = dist;
      this.pinchMidX = midX;
      this.pinchMidY = midY;
      return;
    }

    if (this.touchMode === "movePlacement") {
      // 8px dead zone — a quick tap on the selected planting is still a pick.
      if (this.tapCandidate &&
          Math.hypot(e.clientX - this.downPx, e.clientY - this.downPy) >= 8) {
        this.tapCandidate = false;
      }
      if (!this.tapCandidate) {
        const rect = this.canvas.getBoundingClientRect();
        this.updatePlacementDrag(
          e.clientX, e.clientY, e.clientX - rect.left, e.clientY - rect.top,
        );
      }
      return;
    }

    if (this.touchMode === "drag") {
      // 8px dead zone distinguishes tap (pick) from drag.
      if (this.tapCandidate &&
          Math.hypot(e.clientX - this.downPx, e.clientY - this.downPy) >= 8) {
        this.tapCandidate = false;
      }
      if (!this.tapCandidate) {
        const dx = e.clientX - this.lastPx;
        const dy = e.clientY - this.lastPy;
        if (this.mode === "2d") {
          const mPerPx = (2 * this.halfH) / this.height;
          this.cx -= dx * mPerPx;
          this.cy += dy * mPerPx;
          this.updateCamera();
        } else {
          this.orbit.az -= dx * 0.005;
          this.orbit.polar = Math.min(Math.max(this.orbit.polar - dy * 0.005, 0.08), 1.5);
          this.update3dCamera();
        }
      }
      this.lastPx = e.clientX;
      this.lastPy = e.clientY;
    }
  }

  private onTouchUp(e: PointerEvent, cancelled: boolean) {
    if (!this.touchPoints.delete(e.pointerId)) return;
    if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);

    if (this.touchPoints.size === 0) {
      if (this.touchMode === "movePlacement") {
        this.touchMode = "none";
        this.tapCandidate = false;
        if (cancelled) this.cancelPlacementDrag();
        else this.finishPlacementDrag(); // drop if moved, plain pick otherwise
        this.canvas.style.cursor = "default";
        return;
      }
      const isTap = !cancelled && this.touchMode === "drag" && this.tapCandidate &&
        performance.now() - this.touchDownTime < 250;
      this.touchMode = "none";
      this.tapCandidate = false;
      if (isTap) {
        const rect = this.canvas.getBoundingClientRect();
        const hit = this.pickAt(e.clientX - rect.left, e.clientY - rect.top);
        if (hit?.kind === "placement") this.cb.onPickPlacement(hit.placement);
        else if (hit?.kind === "zone") this.cb.onPickZone(hit.zone.id);
        else this.cb.onPickNothing();
      }
    } else if (this.touchPoints.size === 1) {
      // pinch ended with one finger still down — continue as a plain drag
      this.touchMode = "drag";
      this.tapCandidate = false;
      const rest = [...this.touchPoints.values()][0];
      this.lastPx = rest.x;
      this.lastPy = rest.y;
    } else {
      this.updatePinchBase();
    }
  }

  private onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape" && this.placementDrag) {
      this.cancelPlacementDrag();
      return;
    }
    if (e.key === "f" || e.key === "F") {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT")) return;
      this.fitToExtents();
    }
  };
}
