import * as THREE from "three";
import type { ChunkInfo, ConstraintsResponse, ExclusionZone, Placement, PolygonWithHoles } from "../api/types";
import { mergeShapeGeometries, ringOutlinePositions, shapeWithHoles, disposeObject } from "./cadGeometry";
import { makeShrubSymbolTexture, makeTreeSymbolTexture, SYMBOL_PLANE_HALF } from "./plantSymbols";
import type { Theme } from "../theme";

// Z stacking (ortho top-down): cad=0, allowed=0.1, lawn=0.15, zones=0.2,
// chunks=0.3, rejected=0.35, placements=0.4..0.5, selection=0.6
export const Z = {
  allowed: 0.1,
  lawn: 0.15,
  zones: 0.2,
  chunks: 0.3,
  rejected: 0.35,
  shrubs: 0.4,
  trees: 0.45,
  selection: 0.6,
} as const;

interface OverlayPalette {
  zone: number;
  zoneFillOpacity: number;
  zoneLineOpacity: number;
  allowed: number;
  allowedFillOpacity: number;
  allowedLineOpacity: number;
  lawn: number;
  lawnFillOpacity: number;
  rejected: number;
  chunkGrid: number;
}

/** Append src to dst without spread — spreading huge vertex arrays into
 *  push() overflows the call stack on complex (vegetation-derived) polygons. */
function appendAll(dst: number[], src: number[]): void {
  for (let i = 0; i < src.length; i++) dst.push(src[i]);
}

const PALETTES: Record<Theme, OverlayPalette> = {
  light: {
    zone: 0xdc2626, zoneFillOpacity: 0.09, zoneLineOpacity: 0.4,
    allowed: 0x16a34a, allowedFillOpacity: 0.1, allowedLineOpacity: 0.45,
    lawn: 0x65a30d, lawnFillOpacity: 0.22,
    rejected: 0x8a93a0, chunkGrid: 0xaab2bc,
  },
  dark: {
    zone: 0xef4444, zoneFillOpacity: 0.12, zoneLineOpacity: 0.55,
    allowed: 0x22c55e, allowedFillOpacity: 0.1, allowedLineOpacity: 0.5,
    lawn: 0x84cc16, lawnFillOpacity: 0.2,
    rejected: 0x64748b, chunkGrid: 0x475569,
  },
};

/** One pickable mesh per exclusion zone (userData.zoneId set). */
export function buildZonesGroup(zones: ExclusionZone[], theme: Theme): THREE.Group {
  const pal = PALETTES[theme];
  const group = new THREE.Group();
  group.name = "zones";
  const fillMat = new THREE.MeshBasicMaterial({
    color: pal.zone, transparent: true, opacity: pal.zoneFillOpacity, depthWrite: false, side: THREE.DoubleSide,
  });
  const outlinePositions: number[] = [];
  for (const zone of zones) {
    const { shell, holes } = zone.polygon;
    if (!shell || shell.length < 6) continue;
    const shape = shapeWithHoles(shell, holes ?? []);
    const geo = mergeShapeGeometries([shape], Z.zones);
    const mesh = new THREE.Mesh(geo, fillMat);
    mesh.userData.zoneId = zone.id;
    mesh.frustumCulled = false;
    group.add(mesh);
    appendAll(outlinePositions, ringOutlinePositions([shell, ...(holes ?? [])], Z.zones + 0.01));
  }
  if (outlinePositions.length > 0) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(outlinePositions, 3));
    const line = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ color: pal.zone, transparent: true, opacity: pal.zoneLineOpacity }),
    );
    line.frustumCulled = false;
    line.raycast = () => {}; // outline not pickable
    group.add(line);
  }
  return group;
}

export function buildAllowedGroup(constraints: ConstraintsResponse, theme: Theme): THREE.Group {
  const pal = PALETTES[theme];
  const group = new THREE.Group();
  group.name = "allowed";
  const shapes: THREE.Shape[] = [];
  const outline: number[] = [];
  for (const poly of constraints.allowedArea.polygons) {
    if (!poly.shell || poly.shell.length < 6) continue;
    shapes.push(shapeWithHoles(poly.shell, poly.holes ?? []));
    appendAll(outline, ringOutlinePositions([poly.shell, ...(poly.holes ?? [])], Z.allowed + 0.01));
  }
  if (shapes.length > 0) {
    const mesh = new THREE.Mesh(
      mergeShapeGeometries(shapes, Z.allowed),
      new THREE.MeshBasicMaterial({
        color: pal.allowed, transparent: true, opacity: pal.allowedFillOpacity, depthWrite: false, side: THREE.DoubleSide,
      }),
    );
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(outline, 3));
    const line = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ color: pal.allowed, transparent: true, opacity: pal.allowedLineOpacity }),
    );
    line.frustumCulled = false;
    group.add(line);
  }
  return group;
}

/** Recommended lawn (ТЗ §2 п. 3): filled polygons with a thin outline. */
export function buildLawnGroup(polygons: PolygonWithHoles[], theme: Theme): THREE.Group {
  const pal = PALETTES[theme];
  const group = new THREE.Group();
  group.name = "lawn";
  const shapes: THREE.Shape[] = [];
  const outline: number[] = [];
  for (const poly of polygons) {
    if (!poly.shell || poly.shell.length < 6) continue;
    shapes.push(shapeWithHoles(poly.shell, poly.holes ?? []));
    appendAll(outline, ringOutlinePositions([poly.shell, ...(poly.holes ?? [])], Z.lawn + 0.01));
  }
  if (shapes.length === 0) return group;
  const mesh = new THREE.Mesh(
    mergeShapeGeometries(shapes, Z.lawn),
    new THREE.MeshBasicMaterial({
      color: pal.lawn, transparent: true, opacity: pal.lawnFillOpacity, depthWrite: false, side: THREE.DoubleSide,
    }),
  );
  mesh.frustumCulled = false;
  mesh.raycast = () => {};
  group.add(mesh);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(outline, 3));
  const line = new THREE.LineSegments(
    geo,
    new THREE.LineBasicMaterial({ color: pal.lawn, transparent: true, opacity: 0.6 }),
  );
  line.frustumCulled = false;
  line.raycast = () => {};
  group.add(line);
  return group;
}

export interface PlacementsBuild {
  group: THREE.Group;
  /** InstancedMesh whose instanceId indexes into `placements`. */
  pickMeshes: { mesh: THREE.InstancedMesh; placements: Placement[] }[];
}

/**
 * CAD-style plant glyphs: canvas textures on instanced planes, scaled to
 * crownRadiusM per instance. Trees = scalloped-crown symbol, shrubs = double
 * circle. instanceId raycast picking preserved (one InstancedMesh per kind).
 */
export function buildPlacementsGroup(placements: Placement[], theme: Theme): PlacementsBuild {
  const group = new THREE.Group();
  group.name = "placements";
  const trees = placements.filter((p) => p.kind === "TREE");
  const shrubs = placements.filter((p) => p.kind !== "TREE");
  const pickMeshes: PlacementsBuild["pickMeshes"] = [];

  const make = (list: Placement[], tex: THREE.CanvasTexture, z: number, name: string) => {
    if (list.length === 0) {
      tex.dispose();
      return;
    }
    const sub = new THREE.Group();
    sub.name = name;
    const half = SYMBOL_PLANE_HALF;
    const geo = new THREE.PlaneGeometry(half * 2, half * 2);
    const mat = new THREE.MeshBasicMaterial({
      map: tex, transparent: true, alphaTest: 0.02, depthWrite: false,
    });
    const mesh = new THREE.InstancedMesh(geo, mat, list.length);
    const m = new THREE.Matrix4();
    for (let i = 0; i < list.length; i++) {
      const p = list[i];
      const r = Math.max(p.crownRadiusM, 0.15);
      m.makeScale(r, r, 1);
      m.setPosition(p.position[0], p.position[1], z);
      mesh.setMatrixAt(i, m);
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.frustumCulled = false;
    sub.add(mesh);
    group.add(sub);
    pickMeshes.push({ mesh, placements: list });
  };

  make(trees, makeTreeSymbolTexture(theme), Z.trees, "trees");
  make(shrubs, makeShrubSymbolTexture(theme), Z.shrubs, "shrubs");

  // Manually-moved plantings get a small dashed ring around the symbol.
  const moved = placements.filter((p) => p.manuallyMoved);
  if (moved.length > 0) {
    const sub = new THREE.Group();
    sub.name = "movedMarks";
    const color = theme === "dark" ? 0xfbbf24 : 0xd97706;
    for (const p of moved) {
      const r = Math.max(p.crownRadiusM, 0.15) * 1.25;
      const pts: number[] = [];
      const SEG = 48;
      for (let i = 0; i <= SEG; i++) {
        const a = (i / SEG) * Math.PI * 2;
        pts.push(p.position[0] + Math.cos(a) * r, p.position[1] + Math.sin(a) * r, Z.selection - 0.02);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      const line = new THREE.Line(
        geo,
        new THREE.LineDashedMaterial({
          color, dashSize: Math.max(r * 0.18, 0.05), gapSize: Math.max(r * 0.12, 0.03),
          transparent: true, opacity: 0.9,
        }),
      );
      line.computeLineDistances();
      line.raycast = () => {};
      line.frustumCulled = false;
      sub.add(line);
    }
    group.add(sub);
  }
  return { group, pickMeshes };
}

export function buildRejectedGroup(candidates: [number, number][], theme: Theme): THREE.Group {
  const group = new THREE.Group();
  group.name = "rejected";
  if (candidates.length > 0) {
    const positions: number[] = [];
    for (const [x, y] of candidates) positions.push(x, y, Z.rejected);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    const pts = new THREE.Points(
      geo,
      new THREE.PointsMaterial({
        color: PALETTES[theme].rejected, size: 3, sizeAttenuation: false, transparent: true, opacity: 0.4,
      }),
    );
    pts.frustumCulled = false;
    group.add(pts);
  }
  return group;
}

export function buildChunkGridGroup(chunks: ChunkInfo[], theme: Theme): THREE.Group {
  const group = new THREE.Group();
  group.name = "chunks";
  const positions: number[] = [];
  for (const c of chunks) {
    const [x0, y0, x1, y1] = c.bbox;
    positions.push(
      x0, y0, Z.chunks, x1, y0, Z.chunks,
      x1, y0, Z.chunks, x1, y1, Z.chunks,
      x1, y1, Z.chunks, x0, y1, Z.chunks,
      x0, y1, Z.chunks, x0, y0, Z.chunks,
    );
  }
  if (positions.length > 0) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    const line = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ color: PALETTES[theme].chunkGrid, transparent: true, opacity: 0.5 }),
    );
    line.frustumCulled = false;
    group.add(line);
  }
  return group;
}

/** Replaces `slot`'s current child (if any) with `next`, disposing the old one. */
export function swapChild(parent: THREE.Group, current: THREE.Object3D | null, next: THREE.Object3D | null): THREE.Object3D | null {
  if (current) {
    parent.remove(current);
    disposeObject(current);
  }
  if (next) parent.add(next);
  return next;
}
