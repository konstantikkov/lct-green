import * as THREE from "three";
import type { EntityRender, LayerInfo } from "../api/types";
import { CATEGORY_COLORS } from "../api/types";
import type { Theme } from "../theme";

// ANNOTATION / UNKNOWN linework
const DIMMED: Record<Theme, string> = { light: "#c8cdd4", dark: "#3d4654" };

/** Light-mode "ink on paper": category colors multiplied down to dark ink tones. */
const inkToneCache = new Map<string, string>();
function inkTone(hex: string): string {
  let out = inkToneCache.get(hex);
  if (!out) {
    const c = new THREE.Color(hex);
    const hsl = { h: 0, s: 0, l: 0 };
    c.getHSL(hsl);
    c.setHSL(hsl.h, Math.min(hsl.s * 0.85, 0.75), Math.min(hsl.l * 0.5, 0.38));
    out = `#${c.getHexString()}`;
    inkToneCache.set(hex, out);
  }
  return out;
}

export function layerColor(layer: LayerInfo, theme: Theme): string {
  const cat = layer.classification.category;
  if (cat === "ANNOTATION" || cat === "UNKNOWN") return DIMMED[theme];
  const base = CATEGORY_COLORS[cat] ?? DIMMED.dark;
  return theme === "light" ? inkTone(base) : base;
}

/** Groups keyed by layer name; each group holds merged geometry for that layer. */
export interface CadLayerGroups {
  root: THREE.Group;
  byLayer: Map<string, THREE.Group>;
}

/**
 * Builds one Group per CAD layer:
 *  - all polylines/polygon outlines merged into a single LineSegments
 *  - points/text entities merged into a single Points cloud
 *  - BUILDING closed polygons get a faint fill mesh
 * Everything sits in the z=0 plane.
 */
export function buildCadGroups(
  entities: EntityRender[],
  layers: LayerInfo[],
  theme: Theme,
): CadLayerGroups {
  const root = new THREE.Group();
  root.name = "cad";
  const byLayer = new Map<string, THREE.Group>();
  const layerInfo = new Map(layers.map((l) => [l.name, l]));

  // Bucket entities per layer.
  const buckets = new Map<string, EntityRender[]>();
  for (const e of entities) {
    let b = buckets.get(e.layer);
    if (!b) buckets.set(e.layer, (b = []));
    b.push(e);
  }

  for (const [name, ents] of buckets) {
    const info = layerInfo.get(name);
    const color = info ? layerColor(info, theme) : DIMMED[theme];
    const isBuilding = info?.classification.category === "BUILDING";

    const group = new THREE.Group();
    group.name = `layer:${name}`;

    const linePositions: number[] = [];
    const pointPositions: number[] = [];
    const fillShapes: THREE.Shape[] = [];

    for (const e of ents) {
      if (e.kind === "polyline" || e.kind === "polygon") {
        appendSegments(linePositions, e.points, e.kind === "polygon" || e.closed === true);
        if (isBuilding && (e.kind === "polygon" || e.closed) && e.points.length >= 6) {
          fillShapes.push(shapeFromFlat(e.points));
        }
      } else if (e.kind === "point" || e.kind === "text") {
        const p = e.position ?? (e.points.length >= 2 ? [e.points[0], e.points[1]] : null);
        if (p) pointPositions.push(p[0], p[1], 0);
      }
    }

    if (linePositions.length > 0) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(linePositions, 3));
      const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 });
      const lines = new THREE.LineSegments(geo, mat);
      lines.frustumCulled = false;
      group.add(lines);
    }

    if (pointPositions.length > 0) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(pointPositions, 3));
      const mat = new THREE.PointsMaterial({
        color, size: 3, sizeAttenuation: false, transparent: true, opacity: 0.7,
      });
      const pts = new THREE.Points(geo, mat);
      pts.frustumCulled = false;
      group.add(pts);
    }

    if (fillShapes.length > 0) {
      const geo = mergeShapeGeometries(fillShapes, -0.01);
      const mat = new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: 0.08, depthWrite: false, side: THREE.DoubleSide,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.frustumCulled = false;
      group.add(mesh);
    }

    byLayer.set(name, group);
    root.add(group);
  }

  return { root, byLayer };
}

/** Appends flat [x,y,...] as line-segment pairs (p0-p1, p1-p2, ...). */
function appendSegments(out: number[], flat: number[], closed: boolean) {
  const n = Math.floor(flat.length / 2);
  if (n < 2) return;
  for (let i = 0; i < n - 1; i++) {
    out.push(flat[i * 2], flat[i * 2 + 1], 0, flat[i * 2 + 2], flat[i * 2 + 3], 0);
  }
  if (closed) {
    out.push(flat[(n - 1) * 2], flat[(n - 1) * 2 + 1], 0, flat[0], flat[1], 0);
  }
}

export function shapeFromFlat(flat: number[]): THREE.Shape {
  const shape = new THREE.Shape();
  shape.moveTo(flat[0], flat[1]);
  for (let i = 1; i < Math.floor(flat.length / 2); i++) {
    shape.lineTo(flat[i * 2], flat[i * 2 + 1]);
  }
  shape.closePath();
  return shape;
}

export function shapeWithHoles(shell: number[], holes: number[][]): THREE.Shape {
  const shape = shapeFromFlat(shell);
  for (const hole of holes) {
    if (hole.length < 6) continue;
    const path = new THREE.Path();
    path.moveTo(hole[0], hole[1]);
    for (let i = 1; i < Math.floor(hole.length / 2); i++) {
      path.lineTo(hole[i * 2], hole[i * 2 + 1]);
    }
    path.closePath();
    shape.holes.push(path);
  }
  return shape;
}

/** Merges multiple ShapeGeometries into one BufferGeometry at the given z. */
export function mergeShapeGeometries(shapes: THREE.Shape[], z: number): THREE.BufferGeometry {
  const positions: number[] = [];
  const indices: number[] = [];
  let offset = 0;
  for (const shape of shapes) {
    let geo: THREE.ShapeGeometry;
    try {
      geo = new THREE.ShapeGeometry(shape);
    } catch {
      continue; // degenerate polygon — skip
    }
    const pos = geo.getAttribute("position");
    const idx = geo.getIndex();
    for (let i = 0; i < pos.count; i++) {
      positions.push(pos.getX(i), pos.getY(i), z);
    }
    if (idx) {
      for (let i = 0; i < idx.count; i++) indices.push(idx.getX(i) + offset);
    }
    offset += pos.count;
    geo.dispose();
  }
  const merged = new THREE.BufferGeometry();
  merged.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  merged.setIndex(indices);
  return merged;
}

/** Builds a closed outline (LineSegments positions) for shell + holes rings. */
export function ringOutlinePositions(rings: number[][], z: number): number[] {
  const out: number[] = [];
  for (const ring of rings) {
    const n = Math.floor(ring.length / 2);
    if (n < 2) continue;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      out.push(ring[i * 2], ring[i * 2 + 1], z, ring[j * 2], ring[j * 2 + 1], z);
    }
  }
  return out;
}

export function disposeObject(obj: THREE.Object3D) {
  const disposeMat = (m: THREE.Material) => {
    const map = (m as THREE.MeshBasicMaterial).map;
    if (map) map.dispose();
    m.dispose();
  };
  obj.traverse((child) => {
    const anyChild = child as THREE.Mesh;
    if ((anyChild as THREE.InstancedMesh).isInstancedMesh) {
      (anyChild as THREE.InstancedMesh).dispose(); // frees instance buffers
    }
    if (anyChild.geometry) anyChild.geometry.dispose();
    const mat = anyChild.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(mat)) mat.forEach(disposeMat);
    else if (mat) disposeMat(mat);
  });
}
