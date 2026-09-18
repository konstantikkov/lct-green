import * as THREE from "three";
import type { Category, EntityRender, LayerInfo, Placement, PolygonWithHoles } from "../api/types";
import type { Theme } from "../theme";
import { shapeWithHoles } from "./cadGeometry";

/**
 * Stylized architectural massing model (z-up, meters):
 *  - gradient sky dome + distance fog matched to the horizon color
 *  - hemisphere fill + one warm directional sun with PCF-soft shadows
 *  - buildings extruded with deterministic per-building heights, roof caps,
 *    ground pads and faint edge lines
 *  - roads / sidewalks / curbs as flat ribbons, allowed area as green patches
 *  - per-species crown shapes (instanced) with deterministic per-instance
 *    scale / height / rotation / color variation seeded from the placement id
 *  - existing vegetation as darker olive blobs (instanced, capped)
 */

const VEG_INSTANCE_CAP = 4000;

/** Day (light theme) vs dusk (dark theme) looks for the massing model. */
interface MassingPalette {
  horizon: number;                       // fog + far sky + renderer clear
  sky: [string, string, string, string]; // gradient stops zenith → horizon
  ground: [string, string];              // radial gradient inner → outer
  hemi: { sky: number; ground: number; intensity: number };
  sun: { color: number; intensity: number };
  ribbons: { asphalt: number; sidewalk: number; curb: number };
  allowedPatch: number;
  wall: number;
  roof: number;
  pad: number;
  edge: number;
  edgeOpacity: number;
  /** Extra HSL lightness for crowns so vegetation reads under dim light. */
  crownLightnessShift: number;
}

export const MASSING_PALETTES: Record<Theme, MassingPalette> = {
  light: {
    horizon: 0xf6f2ea,
    sky: ["#cfe0ee", "#e8eef0", "#f6f2ea", "#f6f2ea"],
    ground: ["#f5f5ee", "#dfe4d8"],
    hemi: { sky: 0xdfeaf4, ground: 0x9aa08b, intensity: 1.0 },
    sun: { color: 0xffe9c9, intensity: 1.9 },
    ribbons: { asphalt: 0xb9bdc1, sidewalk: 0xd6d9db, curb: 0xc6cacd },
    allowedPatch: 0xdcead8,
    wall: 0xf4f1ea,
    roof: 0xfbfaf6,
    pad: 0xe6e8e0,
    edge: 0x51565c,
    edgeOpacity: 0.22,
    crownLightnessShift: 0,
  },
  dark: {
    horizon: 0x151c27,
    sky: ["#060b14", "#0c1420", "#151c27", "#151c27"],
    ground: ["#212620", "#161a16"],
    hemi: { sky: 0x2c3a52, ground: 0x11150f, intensity: 0.9 },
    sun: { color: 0xa9c0e8, intensity: 1.15 },       // cool moonlight
    ribbons: { asphalt: 0x363b42, sidewalk: 0x484e56, curb: 0x40454c },
    allowedPatch: 0x1c2e20,
    wall: 0x3a4049,
    roof: 0x454c56,
    pad: 0x24292f,
    edge: 0x9db1c9,
    edgeOpacity: 0.3,
    crownLightnessShift: 0.05,
  },
};

export interface Massing3DBuild {
  group: THREE.Group;
  /** Crown/shrub InstancedMeshes for picking (instanceId → placements[i]). */
  pickMeshes: { mesh: THREE.InstancedMesh; placements: Placement[] }[];
  /** Applied to the scene while the 3D view is active. */
  fog: THREE.Fog;
}

// ---------------------------------------------------------------------------
// Deterministic per-id variation (no Math.random)
// ---------------------------------------------------------------------------

/** FNV-1a 32-bit string hash. */
function hash32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Deterministic float in [0,1) from a hash and a salt (murmur3-style mix). */
function rand01(h: number, salt: number): number {
  let x = (h + Math.imul(salt + 1, 0x9e3779b9)) >>> 0;
  x ^= x >>> 16;
  x = Math.imul(x, 0x21f0aaad) >>> 0;
  x ^= x >>> 15;
  x = Math.imul(x, 0x735a2d97) >>> 0;
  x ^= x >>> 15;
  return (x >>> 0) / 4294967296;
}

function clamp01(v: number): number {
  return Math.min(Math.max(v, 0), 1);
}

interface Hsl {
  h: number;
  s: number;
  l: number;
}

/** Base HSL with a deterministic hue/saturation/lightness jitter. */
function jitterColor(out: THREE.Color, base: Hsl, h32: number, salt: number): void {
  out.setHSL(
    base.h + (rand01(h32, salt) - 0.5) * 0.05,
    clamp01(base.s + (rand01(h32, salt + 1) - 0.5) * 0.12),
    clamp01(base.l + (rand01(h32, salt + 2) - 0.5) * 0.14),
  );
}

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

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

/** Merges ShapeGeometries at per-item z with flat +Z normals (for Lambert). */
function flatShapesGeometry(items: { shape: THREE.Shape; z: number }[]): THREE.BufferGeometry | null {
  const positions: number[] = [];
  const indices: number[] = [];
  let offset = 0;
  for (const { shape, z } of items) {
    let geo: THREE.ShapeGeometry;
    try {
      geo = new THREE.ShapeGeometry(shape);
    } catch {
      continue; // degenerate polygon — skip
    }
    const pos = geo.getAttribute("position");
    const idx = geo.getIndex();
    for (let i = 0; i < pos.count; i++) positions.push(pos.getX(i), pos.getY(i), z);
    if (idx) for (let i = 0; i < idx.count; i++) indices.push(idx.getX(i) + offset);
    offset += pos.count;
    geo.dispose();
  }
  if (positions.length === 0) return null;
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const normals = new Float32Array(positions.length);
  for (let i = 2; i < normals.length; i += 3) normals[i] = 1;
  g.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
  g.setIndex(indices);
  return g;
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

/** Absolute polygon area (shoelace) of a flat [x,y,...] ring, m². */
function polygonArea(flat: number[]): number {
  let a = 0;
  const n = Math.floor(flat.length / 2);
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    a += flat[i * 2] * flat[j * 2 + 1] - flat[j * 2] * flat[i * 2 + 1];
  }
  return Math.abs(a) / 2;
}

/** Vertical wall quads (two triangles each) along an open polyline. */
function appendWallSegments(
  positions: number[], normals: number[], flat: number[], height: number,
): void {
  const n = Math.floor(flat.length / 2);
  for (let i = 0; i < n - 1; i++) {
    const x0 = flat[i * 2], y0 = flat[i * 2 + 1];
    const x1 = flat[i * 2 + 2], y1 = flat[i * 2 + 3];
    const dx = x1 - x0, dy = y1 - y0;
    const len = Math.hypot(dx, dy);
    if (len < 1e-6) continue;
    const nx = dy / len, ny = -dx / len;
    positions.push(
      x0, y0, 0, x1, y1, 0, x1, y1, height,
      x0, y0, 0, x1, y1, height, x0, y0, height,
    );
    for (let k = 0; k < 6; k++) normals.push(nx, ny, 0);
  }
}

/** Flat ribbon (two triangles per segment) along a polyline, ~2*halfW wide. */
function appendRibbon(
  positions: number[], flat: number[], closed: boolean, halfW: number, z: number,
): void {
  const n = Math.floor(flat.length / 2);
  if (n < 2) return;
  const segCount = closed ? n : n - 1;
  for (let i = 0; i < segCount; i++) {
    const j = (i + 1) % n;
    let x0 = flat[i * 2], y0 = flat[i * 2 + 1];
    let x1 = flat[j * 2], y1 = flat[j * 2 + 1];
    const dx = x1 - x0, dy = y1 - y0;
    const len = Math.hypot(dx, dy);
    if (len < 1e-6) continue;
    const ux = dx / len, uy = dy / len;
    // Extend both ends slightly so joints at corners overlap instead of gapping.
    const ext = halfW * 0.6;
    x0 -= ux * ext; y0 -= uy * ext;
    x1 += ux * ext; y1 += uy * ext;
    const nx = uy * halfW, ny = -ux * halfW;
    positions.push(
      x0 + nx, y0 + ny, z, x1 + nx, y1 + ny, z, x1 - nx, y1 - ny, z,
      x0 + nx, y0 + ny, z, x1 - nx, y1 - ny, z, x0 - nx, y0 - ny, z,
    );
  }
}

// ---------------------------------------------------------------------------
// Crown geometry authoring — unit footprint radius 1 in XY, base at z ≈ 0
// ---------------------------------------------------------------------------

/** Ellipsoid crown: XY radius 1, vertical semi-axis `vertical`, sits at z=0. */
function ellipsoidCrown(vertical: number, widthSeg = 14, heightSeg = 10): THREE.BufferGeometry {
  const g = new THREE.SphereGeometry(1, widthSeg, heightSeg);
  g.scale(1, 1, vertical);
  g.translate(0, 0, vertical * 0.92); // slight sink onto the trunk top
  return g;
}

/** Conifer crown: cone ~2.2× crown radius tall, base at z=0. */
function coneCrown(): THREE.BufferGeometry {
  const g = new THREE.ConeGeometry(1, 2.2, 10, 1);
  g.rotateX(Math.PI / 2); // +Y axis → +Z (z-up)
  g.translate(0, 0, 1.1 - 0.1); // base slightly below the attachment point
  return g;
}

/** Low multi-lobe shrub blob within unit footprint, squashed by `vertical`. */
function blobCrown(vertical: number): THREE.BufferGeometry {
  const lobes: [number, number, number, number][] = [
    [0, 0, 0.5, 0.72],
    [0.42, 0.16, 0.38, 0.5],
    [-0.34, 0.3, 0.42, 0.55],
    [0.02, -0.4, 0.4, 0.48],
  ];
  const parts = lobes.map(([x, y, z, r]) => {
    const s = new THREE.SphereGeometry(r, 10, 7);
    s.translate(x, y, z);
    return s;
  });
  const merged = mergeGeometries(parts)!;
  merged.scale(1, 1, vertical);
  return merged;
}

// ---------------------------------------------------------------------------
// Species catalogue
// ---------------------------------------------------------------------------

interface PlantSpec {
  makeCrown: () => THREE.BufferGeometry;
  /** Multiplier on placement crownRadiusM for the XY footprint. */
  radial: number;
  /** 0 = no trunk (shrubs sit on the ground). */
  trunkHeightM: number;
  trunkRadiusM: number;
  crownHsl: Hsl;
}

const SPECIES_SPECS: Record<string, PlantSpec> = {
  // tall ellipsoid crown (height ≈ 1.4× width), mid-green
  tree_linden: {
    makeCrown: () => ellipsoidCrown(1.4, 14, 11), radial: 1,
    trunkHeightM: 2.3, trunkRadiusM: 0.17, crownHsl: { h: 0.3, s: 0.4, l: 0.42 },
  },
  // broad, slightly flattened sphere, deeper green
  tree_maple: {
    makeCrown: () => ellipsoidCrown(0.85, 16, 12), radial: 1.08,
    trunkHeightM: 2.5, trunkRadiusM: 0.19, crownHsl: { h: 0.34, s: 0.45, l: 0.32 },
  },
  // small ovoid on a slender trunk, warm green
  tree_rowan: {
    makeCrown: () => ellipsoidCrown(1.2, 12, 9), radial: 0.85,
    trunkHeightM: 2.9, trunkRadiusM: 0.1, crownHsl: { h: 0.23, s: 0.44, l: 0.42 },
  },
  // conifer cone, dark bluish green
  tree_larch: {
    makeCrown: coneCrown, radial: 1,
    trunkHeightM: 1.7, trunkRadiusM: 0.15, crownHsl: { h: 0.41, s: 0.28, l: 0.29 },
  },
  // slightly taller blob
  shrub_lilac: {
    makeCrown: () => blobCrown(1.1), radial: 1,
    trunkHeightM: 0, trunkRadiusM: 0, crownHsl: { h: 0.29, s: 0.38, l: 0.4 },
  },
  // low cushion
  shrub_cotoneaster: {
    makeCrown: () => blobCrown(0.55), radial: 1.05,
    trunkHeightM: 0, trunkRadiusM: 0, crownHsl: { h: 0.33, s: 0.4, l: 0.28 },
  },
  // mid-height blob
  shrub_mockorange: {
    makeCrown: () => blobCrown(0.85), radial: 1,
    trunkHeightM: 0, trunkRadiusM: 0, crownHsl: { h: 0.27, s: 0.4, l: 0.44 },
  },
};

const FALLBACK_TREE: PlantSpec = {
  makeCrown: () => ellipsoidCrown(1.05, 14, 10), radial: 1,
  trunkHeightM: 2.4, trunkRadiusM: 0.16, crownHsl: { h: 0.31, s: 0.38, l: 0.4 },
};
const FALLBACK_SHRUB: PlantSpec = {
  makeCrown: () => blobCrown(0.8), radial: 1,
  trunkHeightM: 0, trunkRadiusM: 0, crownHsl: { h: 0.3, s: 0.38, l: 0.38 },
};

const TRUNK_HSL: Hsl = { h: 0.08, s: 0.25, l: 0.32 };
const EXISTING_VEG_HSL: Hsl = { h: 0.22, s: 0.3, l: 0.3 }; // dark olive

// ---------------------------------------------------------------------------
// Sky / ground textures
// ---------------------------------------------------------------------------

function skyTexture(stops: MassingPalette["sky"]): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 2;
  canvas.height = 512;
  const ctx = canvas.getContext("2d")!;
  const grad = ctx.createLinearGradient(0, 0, 0, 512);
  grad.addColorStop(0, stops[0]);    // zenith
  grad.addColorStop(0.5, stops[1]);
  grad.addColorStop(0.62, stops[2]); // horizon band
  grad.addColorStop(1, stops[3]);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 2, 512);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function groundTexture(stops: MassingPalette["ground"]): THREE.CanvasTexture {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  // Grey-green tint with a soft radial AO falloff.
  const grad = ctx.createRadialGradient(size / 2, size / 2, size * 0.1, size / 2, size / 2, size * 0.7);
  grad.addColorStop(0, stops[0]);
  grad.addColorStop(1, stops[1]);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// ---------------------------------------------------------------------------
// Ribbons (roads / sidewalks / curbs)
// ---------------------------------------------------------------------------

type RibbonBucket = "asphalt" | "sidewalk" | "curb";

const RIBBON_STYLE: Partial<Record<Category, { halfW: number; z: number; bucket: RibbonBucket }>> = {
  ROAD: { halfW: 1.5, z: 0.06, bucket: "asphalt" },
  PARKING: { halfW: 1.3, z: 0.06, bucket: "asphalt" },
  ACCESS_ROUTE: { halfW: 1.3, z: 0.07, bucket: "asphalt" },
  SIDEWALK: { halfW: 0.9, z: 0.1, bucket: "sidewalk" },
  CURB: { halfW: 0.18, z: 0.12, bucket: "curb" },
};

/** Deterministic building height: 6–24 m, bigger footprints taller. */
function buildingHeight(id: string, areaM2: number): number {
  const h = hash32(id);
  const size = Math.min(Math.sqrt(Math.max(areaM2, 0)) * 0.32, 13);
  return Math.min(Math.max(6 + size + rand01(h, 11) * 5, 6), 24);
}

// ---------------------------------------------------------------------------
// Main build
// ---------------------------------------------------------------------------

export function buildMassing3D(
  entities: EntityRender[],
  layers: LayerInfo[],
  placements: Placement[],
  extents: { min: [number, number]; max: [number, number] },
  allowedPolygons: PolygonWithHoles[] = [],
  theme: Theme = "light",
): Massing3DBuild {
  const pal = MASSING_PALETTES[theme];
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
  const w = Math.max(maxX - minX, 10);
  const h = Math.max(maxY - minY, 10);
  const span = Math.max(w, h);

  // ---- sky + fog ----------------------------------------------------------
  const fog = new THREE.Fog(pal.horizon, span * 1.6, span * 5.2);
  const skyGeo = new THREE.SphereGeometry(1, 24, 16);
  skyGeo.rotateX(Math.PI / 2); // poles onto the z axis (zenith = +Z)
  const sky = new THREE.Mesh(
    skyGeo,
    new THREE.MeshBasicMaterial({
      map: skyTexture(pal.sky), side: THREE.BackSide, fog: false, depthWrite: false, toneMapped: false,
    }),
  );
  sky.scale.setScalar(span * 4.5);
  sky.position.set(cx, cy, 0);
  sky.renderOrder = -1;
  sky.frustumCulled = false;
  sky.raycast = () => {};
  group.add(sky);

  // ---- lights + shadows ---------------------------------------------------
  const hemi = new THREE.HemisphereLight(pal.hemi.sky, pal.hemi.ground, pal.hemi.intensity);
  hemi.position.set(0, 0, 1);
  group.add(hemi);

  const sun = new THREE.DirectionalLight(pal.sun.color, pal.sun.intensity);
  const sunDist = Math.min(span, 600);
  sun.position.set(cx + sunDist * 0.5, cy - sunDist * 0.4, sunDist * 0.62); // ~45° warm sun
  sun.target.position.set(cx, cy, 0);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  const shadowHalf = Math.min(Math.hypot(w, h) * 0.55, 450);
  sun.shadow.camera.left = -shadowHalf;
  sun.shadow.camera.right = shadowHalf;
  sun.shadow.camera.top = shadowHalf;
  sun.shadow.camera.bottom = -shadowHalf;
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = sunDist * 2.2;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.5;
  group.add(sun, sun.target);

  // ---- ground -------------------------------------------------------------
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(span * 4, span * 4),
    new THREE.MeshLambertMaterial({ map: groundTexture(pal.ground) }),
  );
  ground.position.set(cx, cy, -0.05);
  ground.receiveShadow = true;
  ground.raycast = () => {};
  group.add(ground);

  // ---- allowed-area patches -----------------------------------------------
  const allowedShapes = allowedPolygons
    .filter((p) => p.shell && p.shell.length >= 6)
    .map((p) => ({ shape: shapeWithHoles(p.shell, p.holes ?? []), z: 0.03 }));
  const allowedGeo = flatShapesGeometry(allowedShapes);
  if (allowedGeo) {
    const mesh = new THREE.Mesh(
      allowedGeo,
      new THREE.MeshLambertMaterial({ color: pal.allowedPatch, side: THREE.DoubleSide }),
    );
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }

  // ---- buildings + ribbons ------------------------------------------------
  const extrudeGeos: THREE.BufferGeometry[] = [];
  const roofItems: { shape: THREE.Shape; z: number }[] = [];
  const padItems: { shape: THREE.Shape; z: number }[] = [];
  const edgePositions: number[] = [];
  const wallPositions: number[] = [];
  const wallNormals: number[] = [];
  const ribbonPositions: Record<RibbonBucket, number[]> = { asphalt: [], sidewalk: [], curb: [] };

  for (const e of entities) {
    const cat = catByLayer.get(e.layer);
    if (!cat) continue;
    const ribbon = RIBBON_STYLE[cat];
    if (cat === "BUILDING" && (e.kind === "polyline" || e.kind === "polygon")) {
      const closed = e.kind === "polygon" || e.closed === true;
      if (closed && e.points.length >= 6) {
        const shape = shapeFromFlatXY(e.points);
        if (shape) {
          const height = buildingHeight(e.id, polygonArea(e.points));
          try {
            const geo = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false });
            const edges = new THREE.EdgesGeometry(geo, 25);
            const ep = edges.getAttribute("position");
            for (let i = 0; i < ep.count; i++) {
              edgePositions.push(ep.getX(i), ep.getY(i), ep.getZ(i));
            }
            edges.dispose();
            extrudeGeos.push(geo);
            roofItems.push({ shape, z: height + 0.02 });
            padItems.push({ shape, z: 0.05 });
          } catch {
            /* degenerate footprint — skip */
          }
        }
      } else {
        const height = 6 + rand01(hash32(e.id), 12) * 6;
        appendWallSegments(wallPositions, wallNormals, e.points, height);
      }
    } else if (ribbon && (e.kind === "polyline" || e.kind === "polygon")) {
      const closed = e.kind === "polygon" || e.closed === true;
      appendRibbon(ribbonPositions[ribbon.bucket], e.points, closed, ribbon.halfW, ribbon.z);
    }
  }

  const wallMat = new THREE.MeshLambertMaterial({ color: pal.wall, side: THREE.DoubleSide });
  const solidGeo = mergeGeometries(extrudeGeos);
  if (solidGeo) {
    const mesh = new THREE.Mesh(solidGeo, wallMat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }
  if (wallPositions.length > 0) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(wallPositions, 3));
    geo.setAttribute("normal", new THREE.Float32BufferAttribute(wallNormals, 3));
    const mesh = new THREE.Mesh(geo, wallMat);
    mesh.castShadow = true;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }
  const roofGeo = flatShapesGeometry(roofItems);
  if (roofGeo) {
    const mesh = new THREE.Mesh(
      roofGeo,
      new THREE.MeshLambertMaterial({ color: pal.roof, side: THREE.DoubleSide }),
    );
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }
  const padGeo = flatShapesGeometry(padItems);
  if (padGeo) {
    const mesh = new THREE.Mesh(
      padGeo,
      new THREE.MeshLambertMaterial({ color: pal.pad, side: THREE.DoubleSide }),
    );
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }
  if (edgePositions.length > 0) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(edgePositions, 3));
    const lines = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ color: pal.edge, transparent: true, opacity: pal.edgeOpacity }),
    );
    lines.frustumCulled = false;
    lines.raycast = () => {};
    group.add(lines);
  }

  for (const bucket of Object.keys(ribbonPositions) as RibbonBucket[]) {
    const positions = ribbonPositions[bucket];
    if (positions.length === 0) continue;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    const normals = new Float32Array(positions.length);
    for (let i = 2; i < normals.length; i += 3) normals[i] = 1;
    geo.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
    const mesh = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: pal.ribbons[bucket] }));
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }

  // ---- generated plantings (per-species instanced crowns) ------------------
  const bySpecies = new Map<string, Placement[]>();
  for (const p of placements) {
    const key = SPECIES_SPECS[p.speciesId]
      ? p.speciesId
      : p.kind === "TREE" ? "__tree" : "__shrub";
    let list = bySpecies.get(key);
    if (!list) bySpecies.set(key, (list = []));
    list.push(p);
  }

  const treesGroup = new THREE.Group();
  treesGroup.name = "trees3d";
  const shrubsGroup = new THREE.Group();
  shrubsGroup.name = "shrubs3d";

  // Shared scratch objects — no allocation inside instance loops.
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const qIdentity = new THREE.Quaternion();
  const pos = new THREE.Vector3();
  const scl = new THREE.Vector3();
  const color = new THREE.Color();
  const Z_AXIS = new THREE.Vector3(0, 0, 1);

  // One shared white Lambert; per-instance color carried by instanceColor.
  const plantMat = new THREE.MeshLambertMaterial({ color: 0xffffff });

  const specFor = (key: string): PlantSpec =>
    SPECIES_SPECS[key] ?? (key === "__tree" ? FALLBACK_TREE : FALLBACK_SHRUB);
  let treeCount = 0;
  for (const [key, list] of bySpecies) {
    if (specFor(key).trunkHeightM > 0) treeCount += list.length;
  }
  let trunks: THREE.InstancedMesh | null = null;
  let trunkIdx = 0;
  if (treeCount > 0) {
    // Unit trunk: base radius 1 tapering to 0.55, height 1, base at z=0.
    const trunkGeo = new THREE.CylinderGeometry(0.55, 1, 1, 7, 1);
    trunkGeo.rotateX(Math.PI / 2);
    trunkGeo.translate(0, 0, 0.5);
    trunks = new THREE.InstancedMesh(trunkGeo, plantMat, treeCount);
    trunks.castShadow = true;
    trunks.frustumCulled = false;
    trunks.raycast = () => {}; // pick crowns only
    treesGroup.add(trunks);
  }

  for (const [key, list] of bySpecies) {
    const spec = specFor(key);
    const isTree = list[0].kind === "TREE";
    const crowns = new THREE.InstancedMesh(spec.makeCrown(), plantMat, list.length);
    crowns.castShadow = true;
    crowns.frustumCulled = false;
    for (let i = 0; i < list.length; i++) {
      const p = list[i];
      const seed = hash32(p.id);
      const scale = 0.85 + 0.3 * rand01(seed, 1);   // ±15%
      const heightJitter = 0.92 + 0.16 * rand01(seed, 2);
      const r = Math.max(p.crownRadiusM, 0.3) * spec.radial * scale;
      const trunkH = spec.trunkHeightM > 0
        ? spec.trunkHeightM * (0.85 + 0.3 * rand01(seed, 3))
        : 0;
      q.setFromAxisAngle(Z_AXIS, rand01(seed, 4) * Math.PI * 2);
      pos.set(p.position[0], p.position[1], trunkH);
      scl.set(r, r, r * heightJitter);
      m.compose(pos, q, scl);
      crowns.setMatrixAt(i, m);
      jitterColor(color, spec.crownHsl, seed, 5);
      if (pal.crownLightnessShift) color.offsetHSL(0, 0, pal.crownLightnessShift);
      crowns.setColorAt(i, color);

      if (trunks && trunkH > 0) {
        const tr = Math.max(spec.trunkRadiusM * (0.85 + 0.3 * rand01(seed, 8)), 0.05);
        pos.set(p.position[0], p.position[1], 0);
        scl.set(tr, tr, trunkH + r * 0.4); // embed the top into the crown
        m.compose(pos, qIdentity, scl);
        trunks.setMatrixAt(trunkIdx, m);
        jitterColor(color, TRUNK_HSL, seed, 9);
        trunks.setColorAt(trunkIdx, color);
        trunkIdx++;
      }
    }
    crowns.instanceMatrix.needsUpdate = true;
    if (crowns.instanceColor) crowns.instanceColor.needsUpdate = true;
    (isTree ? treesGroup : shrubsGroup).add(crowns);
    pickMeshes.push({ mesh: crowns, placements: list });
  }
  if (trunks) {
    trunks.count = trunkIdx;
    trunks.instanceMatrix.needsUpdate = true;
    if (trunks.instanceColor) trunks.instanceColor.needsUpdate = true;
  }
  if (treesGroup.children.length > 0) group.add(treesGroup);
  if (shrubsGroup.children.length > 0) group.add(shrubsGroup);

  // ---- existing vegetation --------------------------------------------------
  const veg: { x: number; y: number; r: number; id: string }[] = [];
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
    veg.push({ x, y, r, id: e.id });
  }
  if (veg.length > 0) {
    const count = Math.min(veg.length, VEG_INSTANCE_CAP);
    if (count < veg.length) {
      console.info(`[massing3d] existing vegetation capped at ${count} of ${veg.length} instances`);
    }
    const vegGroup = new THREE.Group();
    vegGroup.name = "existingVeg3d";
    // Slightly smaller, darker olive rounded blobs — visually distinct from
    // the generated plantings (no trunks, muted color).
    const blobs = new THREE.InstancedMesh(ellipsoidCrown(0.9, 10, 7), plantMat, count);
    blobs.castShadow = true;
    blobs.frustumCulled = false;
    blobs.raycast = () => {};
    for (let i = 0; i < count; i++) {
      const v = veg[i];
      const seed = hash32(v.id);
      const scale = 0.85 + 0.3 * rand01(seed, 1);
      const heightJitter = 0.92 + 0.16 * rand01(seed, 2);
      const r = v.r * 0.9 * scale;
      q.setFromAxisAngle(Z_AXIS, rand01(seed, 4) * Math.PI * 2);
      pos.set(v.x, v.y, 0);
      scl.set(r, r, r * heightJitter);
      m.compose(pos, q, scl);
      blobs.setMatrixAt(i, m);
      jitterColor(color, EXISTING_VEG_HSL, seed, 5);
      if (pal.crownLightnessShift) color.offsetHSL(0, 0, pal.crownLightnessShift);
      blobs.setColorAt(i, color);
    }
    blobs.instanceMatrix.needsUpdate = true;
    if (blobs.instanceColor) blobs.instanceColor.needsUpdate = true;
    vegGroup.add(blobs);
    group.add(vegGroup);
  }

  return { group, pickMeshes, fog };
}
