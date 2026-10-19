import * as THREE from "three";
import { useEffect, useRef, useState } from "react";
import { Btn } from "../components/ui";

export type Panorama = {
  jobId: string; x: number; y: number; heading: number; eyeHeight: number; quality: string;
  imageUrl: string; state: string; progressPercent?: number; remainingSeconds?: number; error?: string;
  createdAt?: number; iterationId?: string | null;
};

/** Neighbouring points shown as ground rings, like street-level imagery. */
const NEIGHBOUR_RANGE_M = 150;
const deg = THREE.MathUtils.degToRad;

function clearHotspots(group: THREE.Group) {
  for (const child of [...group.children]) {
    group.remove(child);
    child.traverse(o => { const m = o as THREE.Mesh; if (m.isMesh) { m.geometry.dispose(); (m.material as THREE.Material).dispose(); } });
  }
}

/** Equirectangular image centre = `heading` (CAD bearing, 0 = +Y, clockwise).
 *  three.js samples the background at u = atan2(z, x): bearing b appears in
 *  the direction (cos(b - heading), ·, sin(b - heading)). */
function direction(bearing: number, pitch: number, heading: number) {
  const rel = deg(bearing - heading);
  return new THREE.Vector3(Math.cos(deg(pitch)) * Math.cos(rel), Math.sin(deg(pitch)), Math.cos(deg(pitch)) * Math.sin(rel));
}

export function PanoramaViewer({ items, startId, onClose }: { items: Panorama[]; startId: string; onClose: () => void }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [currentId, setCurrentId] = useState(startId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [bearing, setBearing] = useState(0);
  const view = useRef({ yaw: 0, pitch: 0, fov: 75 });
  const three = useRef<{ renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera; hotspots: THREE.Group } | null>(null);
  const current = items.find(i => i.jobId === currentId) ?? items[0];
  const close = useRef(onClose);
  close.current = onClose;
  const initialYaw = useRef(current?.heading ?? 0);

  useEffect(() => {
    const canvas = canvasRef.current!, host = hostRef.current!;
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    const scene = new THREE.Scene(), hotspots = new THREE.Group();
    scene.add(hotspots);
    const camera = new THREE.PerspectiveCamera(75, 1, .1, 2000);
    three.current = { renderer, scene, camera, hotspots };
    view.current.yaw = initialYaw.current;
    let frame = 0;
    const draw = () => {
      frame = requestAnimationFrame(draw);
      const t = three.current;
      if (!t) return;
      const { yaw, pitch, fov } = view.current;
      const heading = Number(t.scene.userData.heading ?? 0);
      camera.fov = fov; camera.updateProjectionMatrix();
      camera.lookAt(direction(yaw, pitch, heading));
      renderer.render(scene, camera);
    };
    const resize = () => {
      const { clientWidth: w, clientHeight: h } = host;
      renderer.setSize(w, h, false); camera.aspect = w / Math.max(h, 1); camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize); observer.observe(host); resize(); draw();
    return () => {
      cancelAnimationFrame(frame); observer.disconnect();
      (scene.background as THREE.Texture | null)?.dispose();
      clearHotspots(hotspots);
      renderer.dispose(); three.current = null;
    };
  }, []);

  // Image of the current point; the list refreshes while other points render,
  // so effects key on ids, never on the array identity.
  const imageUrl = current?.imageUrl, heading = current?.heading ?? 0;
  useEffect(() => {
    const t = three.current;
    if (!t || !imageUrl) return;
    let cancelled = false;
    setLoading(true); setError("");
    new THREE.TextureLoader().loadAsync(imageUrl).then(loaded => {
      if (cancelled) { loaded.dispose(); return; }
      let texture: THREE.Texture = loaded;
      const max = t.renderer.capabilities.maxTextureSize, image = loaded.image as HTMLImageElement;
      if (image.width > max) {
        // Phones often cap textures at 4096 px; downscale rather than fail.
        const canvas = document.createElement("canvas");
        canvas.width = max; canvas.height = max / 2;
        canvas.getContext("2d")!.drawImage(image, 0, 0, canvas.width, canvas.height);
        texture = new THREE.CanvasTexture(canvas); loaded.dispose();
      }
      texture.mapping = THREE.EquirectangularReflectionMapping;
      texture.colorSpace = THREE.SRGBColorSpace;
      (t.scene.background as THREE.Texture | null)?.dispose();
      t.scene.background = texture;
      t.scene.userData.heading = heading;
      setLoading(false);
    }).catch(() => { if (!cancelled) { setLoading(false); setError("Не удалось загрузить панораму"); } });
    return () => { cancelled = true; };
  }, [imageUrl, heading]);

  // Neighbour rings on the ground at their true distance and bearing.
  const neighbourKey = items.map(i => `${i.jobId}:${i.x}:${i.y}`).join("|");
  useEffect(() => {
    const t = three.current;
    if (!t || !current) return;
    clearHotspots(t.hotspots);
    for (const other of items) {
      const dx = other.x - current.x, dy = other.y - current.y, distance = Math.hypot(dx, dy);
      if (other.jobId === current.jobId || distance < .5 || distance > NEIGHBOUR_RANGE_M) continue;
      const ground = direction(THREE.MathUtils.radToDeg(Math.atan2(dx, dy)), 0, current.heading).multiplyScalar(distance);
      ground.y = -current.eyeHeight + .03;
      const size = .6 + distance * .03;
      const ring = new THREE.Mesh(new THREE.RingGeometry(size * .7, size, 40),
        new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: .85, side: THREE.DoubleSide, depthTest: false }));
      const disc = new THREE.Mesh(new THREE.CircleGeometry(size * .7, 40),
        new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: .35, side: THREE.DoubleSide, depthTest: false }));
      const marker = new THREE.Group();
      marker.add(ring, disc); marker.rotation.x = -Math.PI / 2; marker.position.copy(ground);
      marker.userData.jobId = other.jobId;
      t.hotspots.add(marker);
    }
  }, [current?.jobId, neighbourKey]);

  // Look around: drag, wheel/pinch zoom, keyboard; click a ring to move.
  useEffect(() => {
    const canvas = canvasRef.current!;
    const pointers = new Map<number, { x: number; y: number }>();
    let moved = false, pinch = 0;
    const raycaster = new THREE.Raycaster();
    const hotspotAt = (e: PointerEvent) => {
      const t = three.current; if (!t) return null;
      const rect = canvas.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1), t.camera);
      const hit = raycaster.intersectObjects(t.hotspots.children, true)[0];
      return hit ? String(hit.object.parent?.userData.jobId ?? "") || null : null;
    };
    const turn = (dx: number, dy: number) => {
      const perPixel = view.current.fov / canvas.clientHeight;
      view.current.yaw = (view.current.yaw - dx * perPixel + 360) % 360;
      view.current.pitch = Math.max(-85, Math.min(85, view.current.pitch + dy * perPixel));
      setBearing(view.current.yaw);
    };
    const zoom = (factor: number) => { view.current.fov = Math.max(25, Math.min(100, view.current.fov * factor)); };
    const down = (e: PointerEvent) => { canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, { x: e.clientX, y: e.clientY }); moved = false; pinch = 0; };
    const move = (e: PointerEvent) => {
      const last = pointers.get(e.pointerId);
      if (!last) { canvas.style.cursor = hotspotAt(e) ? "pointer" : "grab"; return; }
      if (pointers.size === 2) {
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinch) zoom(pinch / d);
        pinch = d; moved = true; return;
      }
      const dx = e.clientX - last.x, dy = e.clientY - last.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
      // Dragging moves the image with the pointer, as in street-level viewers.
      turn(dx, dy);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    };
    const up = (e: PointerEvent) => {
      if (!moved && pointers.size === 1) { const target = hotspotAt(e); if (target) setCurrentId(target); }
      pointers.delete(e.pointerId);
    };
    const wheel = (e: WheelEvent) => { e.preventDefault(); zoom(Math.pow(1.0015, e.deltaY)); };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
      else if (e.key === "ArrowLeft") turn(40, 0);
      else if (e.key === "ArrowRight") turn(-40, 0);
      else if (e.key === "ArrowUp") turn(0, 40);
      else if (e.key === "ArrowDown") turn(0, -40);
      else if (e.key === "+" || e.key === "=") zoom(.9);
      else if (e.key === "-") zoom(1.1);
    };
    canvas.addEventListener("pointerdown", down); canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("wheel", wheel, { passive: false }); window.addEventListener("keydown", key);
    return () => {
      canvas.removeEventListener("pointerdown", down); canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up); canvas.removeEventListener("pointercancel", up);
      canvas.removeEventListener("wheel", wheel); window.removeEventListener("keydown", key);
    };
  }, []);

  if (!current) return null;
  return <div className="fixed inset-0 z-50 flex flex-col bg-black" role="dialog" aria-modal="true" aria-label="360° панорама">
    <div ref={hostRef} className="relative min-h-0 flex-1">
      <canvas ref={canvasRef} className={`block h-full w-full touch-none transition-opacity duration-300 ${loading ? "opacity-40" : "opacity-100"}`} style={{ cursor: "grab" }} />
      <div className="absolute left-3 right-3 top-3 flex flex-wrap items-start justify-between gap-2">
        <div className="rounded bg-black/60 px-3 py-2 text-xs text-white">
          <div className="font-semibold">360° · Cycles · высота глаз {current.eyeHeight} м</div>
          <div className="text-white/70">Тяните мышью или пальцем · колесо — приближение · кольца на земле — переход к соседней точке</div>
        </div>
        <div className="flex gap-2">
          <a href={current.imageUrl} download className="rounded bg-black/60 px-3 py-2 text-xs text-white underline">Скачать equirect</a>
          <Btn onClick={onClose}>Закрыть</Btn>
        </div>
      </div>
      {loading && <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-white">Загрузка панорамы…</div>}
      {error && <div className="absolute inset-x-0 top-1/2 text-center text-sm text-red-300" role="alert">{error}</div>}
      <MiniMap items={items} current={current} bearing={bearing} onSelect={setCurrentId} />
    </div>
  </div>;
}

/** Plan of all panorama points with the current view cone (north up). */
function MiniMap({ items, current, bearing, onSelect }: { items: Panorama[]; current: Panorama; bearing: number; onSelect: (id: string) => void }) {
  if (items.length < 2) return null;
  const xs = items.map(i => i.x), ys = items.map(i => i.y);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 10);
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const size = 150, scale = (size - 30) / span;
  const at = (x: number, y: number) => [size / 2 + (x - cx) * scale, size / 2 - (y - cy) * scale];
  const [px, py] = at(current.x, current.y);
  const cone = (offset: number) => [px + 26 * Math.sin(deg(bearing + offset)), py - 26 * Math.cos(deg(bearing + offset))].join(",");
  return <svg width={size} height={size} className="absolute bottom-3 left-3 rounded bg-black/60" role="group" aria-label="Карта точек панорам">
    <polygon points={`${px},${py} ${cone(-30)} ${cone(30)}`} fill="rgba(56,189,248,.35)" />
    {items.map(item => {
      const [x, y] = at(item.x, item.y), active = item.jobId === current.jobId;
      return <circle key={item.jobId} cx={x} cy={y} r={active ? 6 : 5} fill={active ? "#f59e0b" : "#38bdf8"} stroke="white" strokeWidth={1.5}
        className="cursor-pointer" onClick={() => onSelect(item.jobId)}><title>{`Точка ${item.x.toFixed(1)}, ${item.y.toFixed(1)}`}</title></circle>;
    })}
    <text x={size - 12} y={16} fill="white" fontSize="11" textAnchor="middle">N</text>
  </svg>;
}
