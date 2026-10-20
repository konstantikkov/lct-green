import * as THREE from "three";
import { useEffect, useMemo, useRef, useState } from "react";
import { Btn } from "../components/ui";
import { ageLabel } from "./growth";

/** One standing point; `links` are ids of neighbours with a clear line of sight. */
export type Panorama = {
  id: string; jobId: string; index: number; x: number; y: number; heading: number; eyeHeight: number;
  quality: string; tour: boolean; links: string[]; imageUrl: string; state: string;
  progressPercent?: number; remainingSeconds?: number; error?: string; createdAt?: number; iterationId?: string | null;
  /** years after planting (null/absent = mature) */
  ageYears?: number | null;
  season?: Season;
};

export type Season = "spring" | "summer" | "autumn" | "winter";
export const SEASON_LABELS: Record<Season, string> = { spring: "весна", summer: "лето", autumn: "осень", winter: "зима" };

/** «лето · через 15 лет» etc. for the header and the panel list. */
export function panoramaConditions(p: Pick<Panorama, "ageYears" | "season">): string {
  const age = p.ageYears == null ? "зрелые посадки" : p.ageYears === 0 ? "при посадке" : ageLabel(p.ageYears);
  return `${SEASON_LABELS[p.season ?? "summer"]} · ${age}`;
}

type Neighbour = { item: Panorama; bearing: number; distance: number };

const deg = THREE.MathUtils.degToRad;
const COMPASS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"];
/** Points of other renders within this distance are reachable too. */
const NEARBY_M = 40;

const bearingTo = (from: Panorama, to: { x: number; y: number }) =>
  (THREE.MathUtils.radToDeg(Math.atan2(to.x - from.x, to.y - from.y)) + 360) % 360;
const angleDiff = (a: number, b: number) => Math.abs(((a - b + 540) % 360) - 180);

/** Equirectangular image centre = `heading` (CAD bearing, 0 = +Y, clockwise).
 *  three.js samples the background at u = atan2(z, x): bearing b appears in
 *  the direction (cos(b - heading), ·, sin(b - heading)). */
function direction(bearing: number, pitch: number, heading: number) {
  const rel = deg(bearing - heading);
  return new THREE.Vector3(Math.cos(deg(pitch)) * Math.cos(rel), Math.sin(deg(pitch)), Math.cos(deg(pitch)) * Math.sin(rel));
}

/** Street View-style moves: tour links plus nearby points of other renders,
 *  at most one per 45° sector. */
function neighboursOf(current: Panorama, items: Panorama[]): Neighbour[] {
  const ready = items.filter(i => i.state === "COMPLETED" && i.id !== current.id);
  const linked = ready.filter(i => current.links.includes(i.id));
  const nearby = ready.filter(i => i.jobId !== current.jobId && Math.hypot(i.x - current.x, i.y - current.y) <= NEARBY_M);
  const sectors = new Map<number, Neighbour>();
  for (const item of [...linked, ...nearby]) {
    const distance = Math.hypot(item.x - current.x, item.y - current.y);
    if (distance < .5) continue;
    const bearing = bearingTo(current, item), sector = Math.floor(((bearing + 22.5) % 360) / 45);
    const known = sectors.get(sector);
    if (!known || distance < known.distance) sectors.set(sector, { item, bearing, distance });
  }
  return [...sectors.values()];
}

function disposeTree(object: THREE.Object3D) {
  object.traverse(o => { const m = o as THREE.Mesh; if (m.isMesh) { m.geometry.dispose(); (m.material as THREE.Material).dispose(); } });
}

/** Flat chevron pointing along +X, lying in the XZ plane. */
function chevronGeometry() {
  const shape = new THREE.Shape();
  shape.moveTo(.55, 0); shape.lineTo(-.25, .5); shape.lineTo(-.05, 0); shape.lineTo(-.25, -.5); shape.lineTo(.55, 0);
  return new THREE.ShapeGeometry(shape).rotateX(Math.PI / 2);
}

export function PanoramaViewer({ items, startId, onClose }: { items: Panorama[]; startId: string; onClose: () => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [currentId, setCurrentId] = useState(startId);
  const [shownId, setShownId] = useState<string | null>(null);
  const [fading, setFading] = useState(true);
  const [error, setError] = useState("");
  const [bearing, setBearing] = useState(0);
  const [hint, setHint] = useState(true);
  const view = useRef({ yaw: 0, pitch: -5, fov: 75, zoom: 1 });
  const three = useRef<{
    renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera;
    arrows: THREE.Group; cursor: THREE.Mesh; textures: Map<string, Promise<THREE.Texture>>;
  } | null>(null);
  const close = useRef(onClose);
  close.current = onClose;

  const ready = useMemo(() => items.filter(i => i.state === "COMPLETED"), [items]);
  const current = ready.find(i => i.id === currentId) ?? ready.find(i => i.id === startId) ?? ready[0];
  const shown = ready.find(i => i.id === shownId) ?? null;
  const neighbours = useMemo(() => (shown ? neighboursOf(shown, items) : []), [shown, items]);
  // Event handlers are bound once; they read the latest render through this ref.
  const latest = useRef({ shown, neighbours, ready, go: (_id: string) => {} });
  latest.current = { shown, neighbours, ready, go: id => { if (!fading) setCurrentId(id); } };

  // Renderer, camera and the draw loop live as long as the dialog.
  useEffect(() => {
    const canvas = canvasRef.current!, host = hostRef.current!;
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    const scene = new THREE.Scene(), arrows = new THREE.Group();
    const cursor = new THREE.Mesh(new THREE.RingGeometry(.32, .42, 40).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: .75, depthTest: false }));
    cursor.visible = false;
    scene.add(arrows, cursor);
    const camera = new THREE.PerspectiveCamera(75, 1, .05, 2000);
    three.current = { renderer, scene, camera, arrows, cursor, textures: new Map() };
    view.current.yaw = current?.heading ?? 0;
    setBearing(view.current.yaw);
    let frame = 0;
    const draw = () => {
      frame = requestAnimationFrame(draw);
      const { yaw, pitch, fov, zoom } = view.current;
      camera.fov = fov * zoom; camera.updateProjectionMatrix();
      camera.lookAt(direction(yaw, pitch, Number(scene.userData.heading ?? 0)));
      renderer.render(scene, camera);
    };
    const resize = () => {
      renderer.setSize(host.clientWidth, host.clientHeight, false);
      camera.aspect = host.clientWidth / Math.max(host.clientHeight, 1); camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize); observer.observe(host); resize(); draw();
    const timer = setTimeout(() => setHint(false), 8000);
    return () => {
      cancelAnimationFrame(frame); observer.disconnect(); clearTimeout(timer);
      three.current?.textures.forEach(p => void p.then(t => t.dispose()).catch(() => {}));
      disposeTree(arrows); disposeTree(cursor); renderer.dispose(); three.current = null;
    };
  }, []);

  function texture(url: string) {
    const t = three.current!;
    let pending = t.textures.get(url);
    if (!pending) {
      pending = new THREE.TextureLoader().loadAsync(url).then(loaded => {
        let result: THREE.Texture = loaded;
        const max = t.renderer.capabilities.maxTextureSize, image = loaded.image as HTMLImageElement;
        if (image.width > max) {
          // Phones often cap textures at 4096 px; downscale rather than fail.
          const canvas = document.createElement("canvas");
          canvas.width = max; canvas.height = max / 2;
          canvas.getContext("2d")!.drawImage(image, 0, 0, canvas.width, canvas.height);
          result = new THREE.CanvasTexture(canvas); loaded.dispose();
        }
        result.mapping = THREE.EquirectangularReflectionMapping;
        result.colorSpace = THREE.SRGBColorSpace;
        return result;
      });
      pending.catch(() => t.textures.delete(url));
      t.textures.set(url, pending);
      // Keep the shown image and a few neighbours; drop the oldest others.
      for (const [key, old] of t.textures) {
        if (t.textures.size <= 8) break;
        if (key === url || key === latest.current.shown?.imageUrl) continue;
        t.textures.delete(key); void old.then(tex => tex.dispose()).catch(() => {});
      }
    }
    return pending;
  }

  // Moving: zoom towards the target while fading, then swap the image.
  useEffect(() => {
    const t = three.current;
    if (!t || !current || current.id === shownId) return;
    let cancelled = false;
    setFading(true); setError("");
    t.cursor.visible = false;
    const started = performance.now();
    const tween = () => {
      if (cancelled) return;
      const k = Math.min(1, (performance.now() - started) / 260);
      view.current.zoom = 1 - .3 * k * k;
      if (k < 1) requestAnimationFrame(tween);
    };
    if (shownId) tween();
    Promise.all([texture(current.imageUrl), new Promise(r => setTimeout(r, shownId ? 260 : 0))]).then(([tex]) => {
      if (cancelled) return;
      t.scene.background = tex;
      t.scene.userData.heading = current.heading;
      view.current.zoom = 1;
      setShownId(current.id); setFading(false);
    }).catch(() => { if (!cancelled) { view.current.zoom = 1; setFading(false); setError("Не удалось загрузить панораму"); } });
    return () => { cancelled = true; };
  }, [current?.id]);

  // Arrows to neighbours; their images load in the background for instant moves.
  useEffect(() => {
    const t = three.current;
    if (!t || !shown) return;
    disposeTree(t.arrows);
    t.arrows.clear();
    const lift = shown.eyeHeight, reach = Math.max(3.2, lift * 1.4), size = Math.max(1, lift / 2.2);
    for (const n of neighbours) {
      const arrow = new THREE.Mesh(chevronGeometry(),
        new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: .9, side: THREE.DoubleSide, depthTest: false }));
      const at = direction(n.bearing, 0, shown.heading).multiplyScalar(reach);
      arrow.position.set(at.x, -lift + .02, at.z);
      arrow.rotation.y = -Math.atan2(at.z, at.x);
      arrow.scale.setScalar(size);
      arrow.renderOrder = 5;
      arrow.userData.id = n.item.id;
      t.arrows.add(arrow);
      void texture(n.item.imageUrl).catch(() => {});
    }
  }, [shown?.id, neighbours]);

  // Look around, click arrows or the ground, keyboard moves.
  useEffect(() => {
    const canvas = canvasRef.current!;
    const pointers = new Map<number, { x: number; y: number }>();
    let moved = false, pinch = 0;
    const raycaster = new THREE.Raycaster();
    const cast = (e: { clientX: number; clientY: number }) => {
      const rect = canvas.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1), three.current!.camera);
      return raycaster;
    };
    const arrowAt = (e: PointerEvent) => {
      const hit = cast(e).intersectObjects(three.current!.arrows.children)[0];
      return hit ? String(hit.object.userData.id) : null;
    };
    /** Ground point under the pointer: viewer-local and in CAD metres. */
    const groundAt = (e: PointerEvent) => {
      const { shown } = latest.current;
      if (!shown || !three.current) return null;
      const ray = cast(e).ray;
      if (ray.direction.y > -.01) return null;
      const local = ray.direction.clone().multiplyScalar(-shown.eyeHeight / ray.direction.y);
      const distance = Math.hypot(local.x, local.z);
      if (distance > 80) return null;
      const absolute = Math.atan2(local.z, local.x) + deg(shown.heading);
      return { local, x: shown.x + distance * Math.sin(absolute), y: shown.y + distance * Math.cos(absolute) };
    };
    const highlight = (id: string | null) => three.current?.arrows.children.forEach(a =>
      ((a as THREE.Mesh).material as THREE.MeshBasicMaterial).color.set(a.userData.id === id ? 0xfbbf24 : 0xffffff));
    const turn = (dx: number, dy: number) => {
      const perPixel = view.current.fov * view.current.zoom / canvas.clientHeight;
      view.current.yaw = (view.current.yaw - dx * perPixel + 360) % 360;
      view.current.pitch = Math.max(-85, Math.min(85, view.current.pitch + dy * perPixel));
      setBearing(view.current.yaw);
    };
    const rotate = (degrees: number) => { view.current.yaw = (view.current.yaw + degrees + 360) % 360; setBearing(view.current.yaw); };
    const zoom = (factor: number) => { view.current.fov = Math.max(25, Math.min(100, view.current.fov * factor)); };
    /** Forward/back: the neighbour closest to the view direction (±60°). */
    const step = (back: boolean) => {
      const want = (view.current.yaw + (back ? 180 : 0)) % 360;
      const best = latest.current.neighbours.filter(n => angleDiff(n.bearing, want) <= 60)
        .sort((a, b) => angleDiff(a.bearing, want) - angleDiff(b.bearing, want))[0];
      if (best) latest.current.go(best.item.id);
    };
    const down = (e: PointerEvent) => {
      canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      moved = false; pinch = 0; setHint(false);
    };
    const move = (e: PointerEvent) => {
      const last = pointers.get(e.pointerId), t = three.current;
      if (!last) {
        if (e.pointerType !== "mouse" || !t) return;
        const arrow = arrowAt(e), ground = arrow ? null : groundAt(e);
        highlight(arrow);
        t.cursor.visible = !!ground;
        if (ground) t.cursor.position.set(ground.local.x, ground.local.y + .02, ground.local.z);
        canvas.style.cursor = arrow ? "pointer" : "grab";
        return;
      }
      if (pointers.size === 2) {
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinch) zoom(pinch / d);
        pinch = d; moved = true; return;
      }
      const dx = e.clientX - last.x, dy = e.clientY - last.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) { moved = true; canvas.style.cursor = "grabbing"; }
      // Dragging moves the image with the pointer, as in street-level viewers.
      turn(dx, dy);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    };
    const up = (e: PointerEvent) => {
      if (!moved && pointers.size === 1) {
        const arrow = arrowAt(e);
        if (arrow) latest.current.go(arrow);
        else {
          // Click on the ground: go to the point nearest to the clicked spot.
          const ground = groundAt(e), { shown, ready } = latest.current;
          const target = ground && ready.filter(i => i.id !== shown?.id)
            .map(i => ({ i, d: Math.hypot(i.x - ground.x, i.y - ground.y) }))
            .sort((a, b) => a.d - b.d)[0];
          if (target && target.d <= 15) latest.current.go(target.i.id);
        }
      }
      canvas.style.cursor = "grab";
      pointers.delete(e.pointerId);
    };
    const leave = () => { if (three.current) three.current.cursor.visible = false; highlight(null); };
    const wheel = (e: WheelEvent) => { e.preventDefault(); zoom(Math.pow(1.0015, e.deltaY)); };
    const key = (e: KeyboardEvent) => {
      // Russian layout letters sit on the same keys (W/A/S/D/F = Ц/Ф/Ы/В/А).
      const k = e.key.toLowerCase();
      if (k === "escape") { if (!document.fullscreenElement) close.current(); return; }
      if (k === "arrowup" || k === "w" || k === "ц") step(false);
      else if (k === "arrowdown" || k === "s" || k === "ы") step(true);
      else if (k === "arrowleft" || k === "a" || k === "ф") rotate(-15);
      else if (k === "arrowright" || k === "d" || k === "в") rotate(15);
      else if (k === "+" || k === "=") zoom(.9);
      else if (k === "-") zoom(1.1);
      else if (k === "f" || k === "а") void toggleFullscreen();
      else return;
      e.preventDefault(); setHint(false);
    };
    canvas.addEventListener("pointerdown", down); canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("pointerleave", leave);
    canvas.addEventListener("wheel", wheel, { passive: false }); window.addEventListener("keydown", key);
    return () => {
      canvas.removeEventListener("pointerdown", down); canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up); canvas.removeEventListener("pointercancel", up);
      canvas.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("wheel", wheel); window.removeEventListener("keydown", key);
    };
  }, []);

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await rootRef.current?.requestFullscreen();
    } catch { /* not allowed (e.g. iOS Safari): the dialog already fills the window */ }
  }

  if (!current) return null;
  const place = shown ?? current;
  const total = items.filter(i => i.jobId === place.jobId).length;
  const compass = COMPASS[Math.round(bearing / 45) % 8];
  return <div ref={rootRef} className="fixed inset-0 z-50 flex flex-col bg-black" role="dialog" aria-modal="true" aria-label="360° панорама">
    <div ref={hostRef} className="relative min-h-0 flex-1">
      <canvas ref={canvasRef} className={`block h-full w-full touch-none transition-opacity duration-200 ${fading ? "opacity-30" : "opacity-100"}`} style={{ cursor: "grab" }} />
      <div className="pointer-events-none absolute left-3 right-3 top-3 flex flex-wrap items-start justify-between gap-2">
        <div className="rounded bg-black/60 px-3 py-2 text-xs text-white">
          <div className="font-semibold">{place.tour ? `Тур · точка ${place.index + 1} из ${total}` : "Панорама 360°"} · высота глаз {place.eyeHeight} м · {panoramaConditions(place)}</div>
          <div className="text-white/70">Взгляд: {compass} · {Math.round(bearing)}° · переходов отсюда: {neighbours.length}</div>
        </div>
        <div className="pointer-events-auto flex gap-2">
          <a href={place.imageUrl} download className="rounded bg-black/60 px-3 py-2 text-xs text-white underline">Скачать</a>
          <Btn onClick={() => void toggleFullscreen()} title="Полный экран (F)">Полный экран</Btn>
          <Btn onClick={onClose} title="Закрыть (Esc)">Закрыть</Btn>
        </div>
      </div>
      {hint && <div className="pointer-events-none absolute inset-x-0 bottom-24 mx-auto w-fit max-w-[90%] rounded bg-black/65 px-4 py-2 text-center text-xs text-white">
        Тяните, чтобы осмотреться · стрелки на земле или клик по земле — переход · W/↑ вперёд, S/↓ назад, A/D поворот · колесо — приближение
      </div>}
      {fading && !error && <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-white">Загрузка…</div>}
      {error && <div className="absolute inset-x-0 top-1/2 text-center text-sm text-red-300" role="alert">{error}</div>}
      <MiniMap items={items} current={place} bearing={bearing} onSelect={id => latest.current.go(id)} />
    </div>
  </div>;
}

/** Plan of panorama points and links with the current view cone (north up). */
function MiniMap({ items, current, bearing, onSelect }: { items: Panorama[]; current: Panorama; bearing: number; onSelect: (id: string) => void }) {
  if (items.length < 2) return null;
  const xs = items.map(i => i.x), ys = items.map(i => i.y);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 10);
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const size = 200, scale = (size - 32) / span;
  const at = (x: number, y: number) => [size / 2 + (x - cx) * scale, size / 2 - (y - cy) * scale];
  const [px, py] = at(current.x, current.y);
  const cone = (offset: number) => [px + 30 * Math.sin(deg(bearing + offset)), py - 30 * Math.cos(deg(bearing + offset))].join(",");
  const byId = new Map(items.map(i => [i.id, i]));
  return <svg viewBox={`0 0 ${size} ${size}`} className="absolute bottom-3 left-3 h-36 w-36 rounded bg-black/60 sm:h-[200px] sm:w-[200px]" role="group" aria-label="Карта точек панорам">
    {items.flatMap(item => item.links.filter(id => id > item.id && byId.has(id)).map(id => {
      const [x1, y1] = at(item.x, item.y), [x2, y2] = at(byId.get(id)!.x, byId.get(id)!.y);
      return <line key={`${item.id}:${id}`} x1={x1} y1={y1} x2={x2} y2={y2} stroke="rgba(255,255,255,.35)" strokeWidth={1.5} />;
    }))}
    <polygon points={`${px},${py} ${cone(-30)} ${cone(30)}`} fill="rgba(251,191,36,.35)" />
    {items.map(item => {
      const [x, y] = at(item.x, item.y), active = item.id === current.id, done = item.state === "COMPLETED";
      return <circle key={item.id} cx={x} cy={y} r={active ? 6.5 : 5} fill={active ? "#f59e0b" : done ? "#38bdf8" : "#6b7280"} stroke="white" strokeWidth={1.5}
        className={done ? "cursor-pointer" : ""} onClick={() => done && onSelect(item.id)}>
        <title>{done ? `Точка ${item.index + 1}` : "Ещё рендерится"}</title></circle>;
    })}
    <text x={size - 12} y={16} fill="white" fontSize="12" textAnchor="middle">N</text>
  </svg>;
}
