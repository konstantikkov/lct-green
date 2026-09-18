import * as THREE from "three";
import type { Category, EntityRender, LayerInfo, Placement } from "../api/types";

/**
 * Minimalist white architectural massing model (z-up, meters):
 *  - buildings extruded 9 m (closed polygons) / wall quads (open polylines)
 *  - white ground plane with a cheap fake-AO radial gradient
 *  - roads & sidewalks as flat light-grey linework
 *  - generated trees = trunk cylinder + flattened sphere crown (instanced)
 *  - existing vegetation = smaller darker spheres
 */

const BUILDING_HEIGHT_M = 9;
const TRUNK_HEIGHT_M = 2.5;
const TRUNK_RADIUS_M = 0.15;
const CROWN_FLATTEN = 0.8;

const FLAT_LINE_CATEGORIES = new Set<Category>([
  "ROAD", "SIDEWALK", "CURB", "PARKING", "ACCESS_ROUTE",
]);

export interface Massing3DBuild {
  group: THREE.Group;
  /** Crown/shrub InstancedMeshes for picking (instanceId → placements[i]). */
  pickMeshes: { mesh: THREE.InstancedMesh; placements: Placement[] }[];
}

/** Concatenates position+normal of non-indexed (or de-indexed) geometries. */
function mergeGeometries(geos: THREE.BufferGeometry[]): THREE.BufferGeometry | null {
  const positions: number[] = [];
  const normals: number[] = [];
  for (const src of geos) {
    const g = src.getIndex() ? src.toNonIndexed() : src;
    const pos = g.getAttribute("position");
    const nrm = g.getAttribute("normal");
    for (let i = 0; i < pos.count; i++) {
      positions.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      if (nrm) normals.push(nrm.getX(i), nrm.getY(i), nrm.getZ(i));
      else normals.push(0, 0, 1);
    }
    if (g !== src) g.dispose();
    src.dispose();
  }
  if (positions.length === 0) return null;
  const merged = new THREE.BufferGeometry();
  merged.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  merged.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  return merged;
}

function groundTexture(): THREE.CanvasTexture {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const grad = ctx.createRadialGradient(size / 2, size / 2, size * 0.1, size / 2, size / 2, size * 0.7);
  grad.addColorStop(0, "#ffffff");
  grad.addColorStop(1, "#e2e5e4");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function shapeFromFlatXY(flat: number[]): THREE.Shape | null {
  const n = Math.floor(flat.length / 2);
  if (n < 3) return null;
  const shape = new THREE.Shape();
  shape.moveTo(flat[0], flat[1]);
  for (let i = 1; i < n; i++) shape.lineTo(flat[i * 2], flat[i * 2 + 1]);
  shape.closePath();
  return shape;
}

/** Vertical wall quads (two triangles each) along an open polyline. */
function appendWallSegments(
  positions: number[], normals: number[], flat: number[], height: number,
) {
  const n = Math.floor(flat.length / 2);
  for (let i = 0; i < n - 1; i++) {
    const x0 = flat[i * 2], y0 = flat[i * 2 + 1];
    const x1 = flat[i * 2 + 2], y1 = flat[i * 2 + 3];
    const dx = x1 - x0, dy = y1 - y0;
    const len = Math.hypot(dx, dy);
    if (len < 1e-6) continue;
    const nx = dy / len, ny = -dx / len;
    // triangle 1: (p0,0) (p1,0) (p1,h) — triangle 2: (p0,0) (p1,h) (p0,h)
    positions.push(
      x0, y0, 0, x1, y1, 0, x1, y1, height,
      x0, y0, 0, x1, y1, height, x0, y0, height,
    );
    for (let k = 0; k < 6; k++) normals.push(nx, ny, 0);
  }
}

export function buildMassing3D(
  entities: EntityRender[],
  layers: LayerInfo[],
  placements: Placement[],
  extents: { min: [number, number]; max: [number, number] },
): Massing3DBuild {
  const group = new THREE.Group();
  group.name = "massing3d";
  const pickMeshes: Massing3DBuild["pickMeshes"] = [];

  const catByLayer = new Map<string, Category>(
    layers.map((l) => [l.name, l.classification.category]),
  );

  const [minX, minY] = extents.min;
  const [maxX, maxY] = extents.max;
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const span = Math.max(maxX - minX, maxY - minY, 10);

  // ---- lights -------------------------------------------------------------
  const hemi = new THREE.HemisphereLight(0xffffff, 0x9aa1a6, 1.1);
  hemi.position.set(0, 0, 1);
  group.add(hemi);
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(cx + span * 0.5, cy - span * 0.35, span * 0.8);
  sun.target.position.set(cx, cy, 0);
  group.add(sun, sun.target);

  // ---- ground -------------------------------------------------------------
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(span * 4, span * 4),
    new THREE.MeshBasicMaterial({ map: groundTexture() }),
  );
  ground.position.set(cx, cy, -0.05);
  ground.raycast = () => {};
  group.add(ground);

  // ---- buildings ----------------------------------------------------------
  const extrudeGeos: THREE.BufferGeometry[] = [];
  const wallPositions: number[] = [];
  const wallNormals: number[] = [];
  const flatLinePositions: number[] = [];

  for (const e of entities) {
    const cat = catByLayer.get(e.layer);
    if (!cat) continue;
    if (cat === "BUILDING" && (e.kind === "polyline" || e.kind === "polygon")) {
      const closed = e.kind === "polygon" || e.closed === true;
      if (closed && e.points.length >= 6) {
        const shape = shapeFromFlatXY(e.points);
        if (shape) {
          try {
            extrudeGeos.push(new THREE.ExtrudeGeometry(shape, {
              depth: BUILDING_HEIGHT_M, bevelEnabled: false,
            }));
          } catch {
            /* degenerate footprint — skip */
          }
        }
      } else {
        appendWallSegments(wallPositions, wallNormals, e.points, BUILDING_HEIGHT_M);
      }
    } else if (FLAT_LINE_CATEGORIES.has(cat) && (e.kind === "polyline" || e.kind === "polygon")) {
      const n = Math.floor(e.points.length / 2);
      const closed = e.kind === "polygon" || e.closed === true;
      for (let i = 0; i < (closed ? n : n - 1); i++) {
        const j = (i + 1) % n;
        flatLinePositions.push(
          e.points[i * 2], e.points[i * 2 + 1], 0.02,
          e.points[j * 2], e.points[j * 2 + 1], 0.02,
        );
      }
    }
  }

  const whiteMat = new THREE.MeshLambertMaterial({ color: 0xffffff, side: THREE.DoubleSide });
  const solidGeo = mergeGeometries(extrudeGeos);
  if (solidGeo) {
    const mesh = new THREE.Mesh(solidGeo, whiteMat);
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }
  if (wallPositions.length > 0) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(wallPositions, 3));
    geo.setAttribute("normal", new THREE.Float32BufferAttribute(wallNormals, 3));
    const mesh = new THREE.Mesh(geo, whiteMat);
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }

  // ---- roads / sidewalks --------------------------------------------------
  if (flatLinePositions.length > 0) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(flatLinePositions, 3));
    const line = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ color: 0xb1b7bc, transparent: true, opacity: 0.85 }),
    );
    line.frustumCulled = false;
    group.add(line);
  }

  // ---- generated plantings ------------------------------------------------
  const trees = placements.filter((p) => p.kind === "TREE");
  const shrubs = placements.filter((p) => p.kind !== "TREE");
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const crownMat = new THREE.MeshLambertMaterial({ color: 0x5f9d68 });

  if (trees.length > 0) {
    const treesGroup = new THREE.Group();
    treesGroup.name = "trees3d";
    const trunkGeo = new THREE.CylinderGeometry(TRUNK_RADIUS_M, TRUNK_RADIUS_M, TRUNK_HEIGHT_M, 8);
    trunkGeo.rotateX(Math.PI / 2); // axis → z
    const trunks = new THREE.InstancedMesh(
      trunkGeo, new THREE.MeshLambertMaterial({ color: 0x86643f }), trees.length,
    );
    trunks.raycast = () => {}; // pick crowns only
    const crowns = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 20, 14), crownMat, trees.length);
    const one = new THREE.Vector3(1, 1, 1);
    for (let i = 0; i < trees.length; i++) {
      const p = trees[i];
      const r = Math.max(p.crownRadiusM, 0.3);
      m.compose(new THREE.Vector3(p.position[0], p.position[1], TRUNK_HEIGHT_M / 2), q, one);
      trunks.setMatrixAt(i, m);
      m.compose(
        new THREE.Vector3(p.position[0], p.position[1], TRUNK_HEIGHT_M + r * CROWN_FLATTEN),
        q,
        new THREE.Vector3(r, r, r * CROWN_FLATTEN),
      );
      crowns.setMatrixAt(i, m);
    }
    trunks.instanceMatrix.needsUpdate = true;
    crowns.instanceMatrix.needsUpdate = true;
    trunks.frustumCulled = false;
    crowns.frustumCulled = false;
    treesGroup.add(trunks, crowns);
    group.add(treesGroup);
    pickMeshes.push({ mesh: crowns, placements: trees });
  }

  if (shrubs.length > 0) {
    const shrubsGroup = new THREE.Group();
    shrubsGroup.name = "shrubs3d";
    const spheres = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 14, 10), crownMat, shrubs.length);
    for (let i = 0; i < shrubs.length; i++) {
      const p = shrubs[i];
      const r = Math.max(p.crownRadiusM, 0.25);
      m.compose(
        new THREE.Vector3(p.position[0], p.position[1], r * CROWN_FLATTEN),
        q,
        new THREE.Vector3(r, r, r * CROWN_FLATTEN),
      );
      spheres.setMatrixAt(i, m);
    }
    spheres.instanceMatrix.needsUpdate = true;
    spheres.frustumCulled = false;
    shrubsGroup.add(spheres);
    group.add(shrubsGroup);
    pickMeshes.push({ mesh: spheres, placements: shrubs });
  }

  // ---- existing vegetation ------------------------------------------------
  const veg: { x: number; y: number; r: number }[] = [];
  for (const e of entities) {
    const cat = catByLayer.get(e.layer);
    if (cat !== "EXISTING_TREE" && cat !== "EXISTING_SHRUB") continue;
    let x: number | null = null;
    let y: number | null = null;
    if (e.position) {
      [x, y] = e.position;
    } else if (e.points.length >= 2) {
      let sx = 0, sy = 0;
      const n = Math.floor(e.points.length / 2);
      for (let i = 0; i < n; i++) {
        sx += e.points[i * 2];
        sy += e.points[i * 2 + 1];
      }
      x = sx / n;
      y = sy / n;
    }
    if (x == null || y == null) continue;
    const fallback = cat === "EXISTING_TREE" ? 1.4 : 0.7;
    const r = Math.min(Math.max(e.radius ?? fallback, 0.4), 5);
    veg.push({ x, y, r });
  }
  if (veg.length > 0) {
    const vegGroup = new THREE.Group();
    vegGroup.name = "existingVeg3d";
    const spheres = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1, 12, 8),
      new THREE.MeshLambertMaterial({ color: 0x39653f }),
      veg.length,
    );
    spheres.raycast = () => {};
    for (let i = 0; i < veg.length; i++) {
      const v = veg[i];
      m.compose(
        new THREE.Vector3(v.x, v.y, v.r * CROWN_FLATTEN),
        q,
        new THREE.Vector3(v.r, v.r, v.r * CROWN_FLATTEN),
      );
      spheres.setMatrixAt(i, m);
    }
    spheres.instanceMatrix.needsUpdate = true;
    spheres.frustumCulled = false;
    vegGroup.add(spheres);
    group.add(vegGroup);
  }

  return { group, pickMeshes };
}
