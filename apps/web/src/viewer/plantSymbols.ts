import * as THREE from "three";
import type { Theme } from "../theme";

/**
 * Canvas-generated CAD landscape-plan glyphs, rendered onto instanced planes.
 *
 * Texture layout: 256×256, symbol centered. The nominal crown radius maps to
 * SYMBOL_R px, so a plane of half-size (TEX_HALF / SYMBOL_R) world units,
 * scaled per-instance by crownRadiusM, shows the crown at true size (the
 * scalloped bumps overshoot the nominal radius slightly, as on real plans).
 */
const TEX_SIZE = 256;
const TEX_HALF = TEX_SIZE / 2;
const SYMBOL_R = 100;

/** World half-size of a unit (crownRadius = 1 m) symbol plane. */
export const SYMBOL_PLANE_HALF = TEX_HALF / SYMBOL_R;

export const PLANT_STROKE: Record<Theme, string> = {
  light: "#2f6b3a",
  dark: "#4ade80",
};

const PLANT_FILL: Record<Theme, string> = {
  light: "rgba(47, 107, 58, 0.14)",
  dark: "rgba(74, 222, 128, 0.16)",
};

function makeCanvas(): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const canvas = document.createElement("canvas");
  canvas.width = TEX_SIZE;
  canvas.height = TEX_SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2d canvas unavailable");
  ctx.translate(TEX_HALF, TEX_HALF);
  ctx.lineCap = "round";
  return [canvas, ctx];
}

function toTexture(canvas: HTMLCanvasElement): THREE.CanvasTexture {
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  tex.needsUpdate = true;
  return tex;
}

/** Scalloped "cloud" crown path: n outward arcs between points on radius r. */
function cloudPath(ctx: CanvasRenderingContext2D, r: number, n: number) {
  const bulge = r * 0.16;
  ctx.beginPath();
  for (let i = 0; i <= n; i++) {
    const a0 = (i / n) * Math.PI * 2 - Math.PI / 2;
    const x = Math.cos(a0) * r;
    const y = Math.sin(a0) * r;
    if (i === 0) {
      ctx.moveTo(x, y);
    } else {
      const aMid = ((i - 0.5) / n) * Math.PI * 2 - Math.PI / 2;
      const cx = Math.cos(aMid) * (r + bulge * 2.2);
      const cy = Math.sin(aMid) * (r + bulge * 2.2);
      ctx.quadraticCurveTo(cx, cy, x, y);
    }
  }
  ctx.closePath();
}

/** Classic tree glyph: scalloped crown + center dot + 4 radial ticks. */
export function makeTreeSymbolTexture(theme: Theme): THREE.CanvasTexture {
  const [canvas, ctx] = makeCanvas();
  const stroke = PLANT_STROKE[theme];

  cloudPath(ctx, SYMBOL_R * 0.92, 10);
  ctx.fillStyle = PLANT_FILL[theme];
  ctx.fill();
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 6;
  ctx.stroke();

  // 4 short radial ticks
  ctx.lineWidth = 5;
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * 16, Math.sin(a) * 16);
    ctx.lineTo(Math.cos(a) * 46, Math.sin(a) * 46);
    ctx.stroke();
  }

  // center dot
  ctx.beginPath();
  ctx.arc(0, 0, 8, 0, Math.PI * 2);
  ctx.fillStyle = stroke;
  ctx.fill();

  return toTexture(canvas);
}

/** Shrub glyph: smaller double circle + center dot. */
export function makeShrubSymbolTexture(theme: Theme): THREE.CanvasTexture {
  const [canvas, ctx] = makeCanvas();
  const stroke = PLANT_STROKE[theme];

  ctx.beginPath();
  ctx.arc(0, 0, SYMBOL_R * 0.94, 0, Math.PI * 2);
  ctx.fillStyle = PLANT_FILL[theme];
  ctx.fill();
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 6;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(0, 0, SYMBOL_R * 0.66, 0, Math.PI * 2);
  ctx.lineWidth = 4;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(0, 0, 7, 0, Math.PI * 2);
  ctx.fillStyle = stroke;
  ctx.fill();

  return toTexture(canvas);
}
