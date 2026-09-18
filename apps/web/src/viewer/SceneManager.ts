import * as THREE from "three";
import type {
  ChunkInfo, ConstraintsResponse, ExclusionZone, IterationDetail,
  LayerInfo, Placement, ProjectDocument,
} from "../api/types";
import { buildCadGroups, disposeObject, layerColor } from "./cadGeometry";
import {
  buildAllowedGroup, buildChunkGridGroup, buildPlacementsGroup,
  buildRejectedGroup, buildZonesGroup, swapChild, Z, type PlacementsBuild,
} from "./overlayGeometry";
import type { OverlayState } from "../state/store";

export interface SceneCallbacks {
  onPickPlacement: (p: Placement) => void;
  onPickZone: (zoneId: string) => void;
  onPickNothing: () => void;
  onCursor: (worldX: number, worldY: number) => void;
  onZoom: (pxPerMeter: number) => void;
}

/** Owns the three.js scene; imperative API driven by React effects. */
export class SceneManager {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.OrthographicCamera;
  private canvas: HTMLCanvasElement;
  private cb: SceneCallbacks;
  private raycaster = new THREE.Raycaster();

  // camera state (world units = meters)
  private cx = 0;
  private cy = 0;
  private halfH = 50; // half of visible height in meters
  private width = 2;
  private height = 2;

  // slots
  private slotRoot = new THREE.Group();
  private cadRoot: THREE.Object3D | null = null;
  private cadByLayer = new Map<string, THREE.Group>();
  private vegLayers = new Set<string>();
  private zonesObj: THREE.Object3D | null = null;
  private allowedObj: THREE.Object3D | null = null;
  private chunksObj: THREE.Object3D | null = null;
  private rejectedObj: THREE.Object3D | null = null;
  private placementsObj: THREE.Object3D | null = null;
  private pickMeshes: PlacementsBuild["pickMeshes"] = [];
  private zoneById = new Map<string, ExclusionZone>();
  private selectionRing: THREE.Mesh;

  private overlays: OverlayState | null = null;
  private layerVisibility: Record<string, boolean> = {};
  private extents: { min: [number, number]; max: [number, number] } | null = null;

  private dirty = true;
  private rafId = 0;
  private disposed = false;
  private resizeObserver: ResizeObserver;

  // interaction
  private dragging = false;
  private panned = false;
  private lastPx = 0;
  private lastPy = 0;
  private downPx = 0;
  private downPy = 0;

  constructor(canvas: HTMLCanvasElement, cb: SceneCallbacks) {
    this.canvas = canvas;
    this.cb = cb;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x0b0f14, 1);

    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
    this.camera.position.set(0, 0, 100);
    this.camera.lookAt(0, 0, 0);

    this.scene.add(this.slotRoot);

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
    if (this.cadRoot) {
      this.slotRoot.remove(this.cadRoot);
      disposeObject(this.cadRoot);
      this.cadRoot = null;
    }
    this.cadByLayer.clear();
    this.vegLayers.clear();
    if (doc) {
      const { root, byLayer } = buildCadGroups(doc.entities, doc.layers);
      this.cadRoot = root;
      this.cadByLayer = byLayer;
      for (const l of doc.layers) {
        const cat = l.classification.category;
        if (cat === "EXISTING_TREE" || cat === "EXISTING_SHRUB") this.vegLayers.add(l.name);
      }
      this.slotRoot.add(root);
      this.extents = doc.extents;
      this.fitToExtents();
    }
    this.applyVisibility();
    this.invalidate();
  }

  updateLayerInfo(layers: LayerInfo[]) {
    // Re-derive vegetation membership and colors after classification overrides.
    this.vegLayers.clear();
    for (const l of layers) {
      const cat = l.classification.category;
      if (cat === "EXISTING_TREE" || cat === "EXISTING_SHRUB") this.vegLayers.add(l.name);
      const group = this.cadByLayer.get(l.name);
      if (group) {
        const color = new THREE.Color(layerColor(l));
        group.traverse((child) => {
          const mat = (child as THREE.Mesh).material as THREE.Material | undefined;
          if (mat && "color" in mat) (mat as THREE.MeshBasicMaterial).color.copy(color);
        });
      }
    }
    this.applyVisibility();
    this.invalidate();
  }

  setConstraints(c: ConstraintsResponse | null) {
    this.zonesObj = swapChild(this.slotRoot, this.zonesObj, c ? buildZonesGroup(c.exclusionZones) : null);
    this.allowedObj = swapChild(this.slotRoot, this.allowedObj, c ? buildAllowedGroup(c) : null);
    this.zoneById.clear();
    if (c) for (const z of c.exclusionZones) this.zoneById.set(z.id, z);
    this.applyVisibility();
    this.invalidate();
  }

  setIteration(it: IterationDetail | null) {
    const build = it ? buildPlacementsGroup(it.placements) : null;
    this.placementsObj = swapChild(this.slotRoot, this.placementsObj, build?.group ?? null);
    this.pickMeshes = build?.pickMeshes ?? [];
    this.rejectedObj = swapChild(
      this.slotRoot, this.rejectedObj,
      it ? buildRejectedGroup(it.rejectedCandidates) : null,
    );
    this.applyVisibility();
    this.invalidate();
  }

  setChunks(chunks: ChunkInfo[] | null) {
    this.chunksObj = swapChild(this.slotRoot, this.chunksObj, chunks ? buildChunkGridGroup(chunks) : null);
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

  setSelectedPlacement(p: Placement | null) {
    if (p) {
      const r = Math.max(p.crownRadiusM, 0.15);
      this.selectionRing.scale.set(r, r, 1);
      this.selectionRing.position.set(p.position[0], p.position[1], Z.selection);
      this.selectionRing.visible = true;
    } else {
      this.selectionRing.visible = false;
    }
    this.invalidate();
  }

  fitToExtents() {
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
    this.selectionRing.geometry.dispose();
    (this.selectionRing.material as THREE.Material).dispose();
    this.renderer.dispose();
  }

  // ---------- internals ----------

  private invalidate() {
    this.dirty = true;
  }

  private loop = () => {
    if (this.disposed) return;
    if (this.dirty) {
      this.dirty = false;
      this.renderer.render(this.scene, this.camera);
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
    this.updateCamera();
  }

  private updateCamera() {
    const aspect = this.width / this.height;
    this.camera.left = this.cx - this.halfH * aspect;
    this.camera.right = this.cx + this.halfH * aspect;
    this.camera.top = this.cy + this.halfH;
    this.camera.bottom = this.cy - this.halfH;
    this.camera.updateProjectionMatrix();
    this.cb.onZoom(this.height / (2 * this.halfH));
    this.invalidate();
  }

  private screenToWorld(px: number, py: number): [number, number] {
    const mPerPx = (2 * this.halfH) / this.height;
    return [
      this.cx + (px - this.width / 2) * mPerPx,
      this.cy - (py - this.height / 2) * mPerPx,
    ];
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
    if (this.placementsObj) {
      for (const child of this.placementsObj.children) {
        if (child.name === "trees") child.visible = o?.trees !== false;
        if (child.name === "shrubs") child.visible = o?.shrubs !== false;
      }
    }
  }

  private pickAt(px: number, py: number): { placement?: Placement; zone?: ExclusionZone } | null {
    const ndc = new THREE.Vector2((px / this.width) * 2 - 1, -(py / this.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);

    // Placements first (drawn on top).
    if (this.placementsObj?.visible) {
      for (const { mesh, placements } of this.pickMeshes) {
        if (!mesh.parent?.visible) continue;
        const hits = this.raycaster.intersectObject(mesh, false);
        if (hits.length > 0 && hits[0].instanceId != null) {
          return { placement: placements[hits[0].instanceId] };
        }
      }
    }
    if (this.zonesObj?.visible) {
      const hits = this.raycaster.intersectObject(this.zonesObj, true);
      for (const hit of hits) {
        const id = hit.object.userData.zoneId as string | undefined;
        if (id) {
          const zone = this.zoneById.get(id);
          if (zone) return { zone };
        }
      }
    }
    return null;
  }

  private bindEvents() {
    const c = this.canvas;
    c.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 && e.button !== 1) return;
      this.dragging = true;
      this.panned = false;
      this.lastPx = this.downPx = e.clientX;
      this.lastPy = this.downPy = e.clientY;
      c.setPointerCapture(e.pointerId);
    });

    c.addEventListener("pointermove", (e) => {
      const rect = c.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const [wx, wy] = this.screenToWorld(px, py);
      this.cb.onCursor(wx, wy);

      if (this.dragging) {
        // 3px dead zone distinguishes click (pick) from pan.
        if (!this.panned) {
          this.panned = Math.hypot(e.clientX - this.downPx, e.clientY - this.downPy) >= 3;
        }
        if (this.panned) {
          const dx = e.clientX - this.lastPx;
          const dy = e.clientY - this.lastPy;
          const mPerPx = (2 * this.halfH) / this.height;
          this.cx -= dx * mPerPx;
          this.cy += dy * mPerPx;
          this.updateCamera();
        }
        this.lastPx = e.clientX;
        this.lastPy = e.clientY;
      } else {
        const hit = this.pickAt(px, py);
        c.style.cursor = hit ? "pointer" : "default";
      }
    });

    c.addEventListener("pointerup", (e) => {
      if (!this.dragging) return;
      this.dragging = false;
      c.releasePointerCapture(e.pointerId);
      if (!this.panned && e.button === 0) {
        const rect = c.getBoundingClientRect();
        const hit = this.pickAt(e.clientX - rect.left, e.clientY - rect.top);
        if (hit?.placement) this.cb.onPickPlacement(hit.placement);
        else if (hit?.zone) this.cb.onPickZone(hit.zone.id);
        else this.cb.onPickNothing();
      }
    });

    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const rect = c.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const [wx, wy] = this.screenToWorld(px, py);
      const factor = Math.pow(1.0015, e.deltaY);
      this.halfH = Math.min(Math.max(this.halfH * factor, 0.05), 1e6);
      // keep the world point under the cursor fixed
      const mPerPx = (2 * this.halfH) / this.height;
      this.cx = wx - (px - this.width / 2) * mPerPx;
      this.cy = wy + (py - this.height / 2) * mPerPx;
      this.updateCamera();
    }, { passive: false });

    window.addEventListener("keydown", this.onKeyDown);
  }

  private onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "f" || e.key === "F") {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT")) return;
      this.fitToExtents();
    }
  };
}
