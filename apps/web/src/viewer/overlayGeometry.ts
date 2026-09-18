import * as THREE from "three";
import type { ChunkInfo, ConstraintsResponse, ExclusionZone, Placement } from "../api/types";
import { mergeShapeGeometries, ringOutlinePositions, shapeWithHoles, disposeObject } from "./cadGeometry";

// Z stacking (ortho top-down): cad=0, allowed=0.1, zones=0.2, chunks=0.3,
// rejected=0.35, placements=0.4..0.5, selection=0.6
export const Z = {
  allowed: 0.1,
  zones: 0.2,
  chunks: 0.3,
  rejected: 0.35,
  shrubs: 0.4,
  trees: 0.45,
  rings: 0.5,
  selection: 0.6,
} as const;

const ZONE_COLOR = 0xef4444;
const ALLOWED_COLOR = 0x22c55e;
const TREE_COLOR = 0x16a34a;
const TREE_RING_COLOR = 0x4ade80;
const SHRUB_COLOR = 0x65a30d;
const SHRUB_RING_COLOR = 0xa3e635;

/** One pickable mesh per exclusion zone (userData.zoneId set). */
export function buildZonesGroup(zones: ExclusionZone[]): THREE.Group {
  const group = new THREE.Group();
  group.name = "zones";
  const fillMat = new THREE.MeshBasicMaterial({
    color: ZONE_COLOR, transparent: true, opacity: 0.12, depthWrite: false, side: THREE.DoubleSide,
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
    outlinePositions.push(...ringOutlinePositions([shell, ...(holes ?? [])], Z.zones + 0.01));
  }
  if (outlinePositions.length > 0) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(outlinePositions, 3));
    const line = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ color: ZONE_COLOR, transparent: true, opacity: 0.55 }),
    );
    line.frustumCulled = false;
    line.raycast = () => {}; // outline not pickable
    group.add(line);
  }
  return group;
}

export function buildAllowedGroup(constraints: ConstraintsResponse): THREE.Group {
  const group = new THREE.Group();
  group.name = "allowed";
  const shapes: THREE.Shape[] = [];
  const outline: number[] = [];
  for (const poly of constraints.allowedArea.polygons) {
    if (!poly.shell || poly.shell.length < 6) continue;
    shapes.push(shapeWithHoles(poly.shell, poly.holes ?? []));
    outline.push(...ringOutlinePositions([poly.shell, ...(poly.holes ?? [])], Z.allowed + 0.01));
  }
  if (shapes.length > 0) {
    const mesh = new THREE.Mesh(
      mergeShapeGeometries(shapes, Z.allowed),
      new THREE.MeshBasicMaterial({
        color: ALLOWED_COLOR, transparent: true, opacity: 0.1, depthWrite: false, side: THREE.DoubleSide,
      }),
    );
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(outline, 3));
    const line = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ color: ALLOWED_COLOR, transparent: true, opacity: 0.5 }),
    );
    line.frustumCulled = false;
    group.add(line);
  }
  return group;
}

export interface PlacementsBuild {
  group: THREE.Group;
  /** InstancedMesh whose instanceId indexes into `placements`. */
  pickMeshes: { mesh: THREE.InstancedMesh; placements: Placement[] }[];
}

/** Crown circles as InstancedMesh (fill + brighter ring), split TREE/SHRUB. */
export function buildPlacementsGroup(placements: Placement[]): PlacementsBuild {
  const group = new THREE.Group();
  group.name = "placements";
  const trees = placements.filter((p) => p.kind === "TREE");
  const shrubs = placements.filter((p) => p.kind !== "TREE");
  const pickMeshes: PlacementsBuild["pickMeshes"] = [];

  const make = (
    list: Placement[], fill: number, ring: number, zFill: number, name: string,
  ) => {
    if (list.length === 0) return;
    const sub = new THREE.Group();
    sub.name = name;
    const circleGeo = new THREE.CircleGeometry(1, 28);
    const fillMesh = new THREE.InstancedMesh(
      circleGeo,
      new THREE.MeshBasicMaterial({ color: fill, transparent: true, opacity: 0.35, depthWrite: false }),
      list.length,
    );
    const ringGeo = new THREE.RingGeometry(0.92, 1, 28);
    const ringMesh = new THREE.InstancedMesh(
      ringGeo,
      new THREE.MeshBasicMaterial({ color: ring, transparent: true, opacity: 0.9, depthWrite: false }),
      list.length,
    );
    ringMesh.raycast = () => {}; // pick via fill only
    const m = new THREE.Matrix4();
    for (let i = 0; i < list.length; i++) {
      const p = list[i];
      const r = Math.max(p.crownRadiusM, 0.15);
      m.makeScale(r, r, 1);
      m.setPosition(p.position[0], p.position[1], zFill);
      fillMesh.setMatrixAt(i, m);
      m.setPosition(p.position[0], p.position[1], Z.rings);
      ringMesh.setMatrixAt(i, m);
    }
    fillMesh.instanceMatrix.needsUpdate = true;
    ringMesh.instanceMatrix.needsUpdate = true;
    fillMesh.frustumCulled = false;
    ringMesh.frustumCulled = false;
    sub.add(fillMesh, ringMesh);
    group.add(sub);
    pickMeshes.push({ mesh: fillMesh, placements: list });
  };

  make(trees, TREE_COLOR, TREE_RING_COLOR, Z.trees, "trees");
  make(shrubs, SHRUB_COLOR, SHRUB_RING_COLOR, Z.shrubs, "shrubs");
  return { group, pickMeshes };
}

export function buildRejectedGroup(candidates: [number, number][]): THREE.Group {
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
        color: 0x64748b, size: 3, sizeAttenuation: false, transparent: true, opacity: 0.4,
      }),
    );
    pts.frustumCulled = false;
    group.add(pts);
  }
  return group;
}

export function buildChunkGridGroup(chunks: ChunkInfo[]): THREE.Group {
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
      new THREE.LineBasicMaterial({ color: 0x475569, transparent: true, opacity: 0.5 }),
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
