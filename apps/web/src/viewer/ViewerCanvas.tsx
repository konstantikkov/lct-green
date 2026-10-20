import {RenderWorkflow} from "../components/RenderWorkflow";
import { useEffect, useMemo, useRef, useState } from "react";
import type { PanoramaTools } from "../components/PanoramaPanel";
import { SceneManager, type HoverHit } from "./SceneManager";
import { DragChecker, dragAnnotationHtml } from "./dragCheck";
import { hud } from "./hud";
import { useApp, getState } from "../state/store";
import { clearMoveToast, movePlacement, select, setMassingDetail, setViewMode } from "../state/actions";
import { getTheme, useTheme } from "../theme";
import { AGE_STOPS, ageLabel } from "./growth";
import { SEASON_LABELS, type Season } from "./PanoramaViewer";

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Builds tooltip HTML for a hover hit (values escaped). */
function tooltipHtml(hit: NonNullable<HoverHit>): string {
  const row = (k: string, v: string) =>
    `<div class="flex justify-between gap-3"><span class="text-ink-400">${k}</span><span class="font-mono">${v}</span></div>`;
  if (hit.kind === "placement") {
    const p = hit.placement;
    const s = getState();
    const sp = s.species?.find((x) => x.id === p.speciesId);
    const it = s.activeIteration;
    const title = sp
      ? esc(sp.displayNameRu || sp.displayName)
      : esc(p.speciesId);
    return (
      `<div class="mb-1 text-xs font-semibold text-ink-100">${title}</div>` +
      row("id", esc(p.id)) +
      row("радиус кроны", `${p.crownRadiusM.toFixed(2)} м`) +
      row("оценка", p.score.toFixed(3)) +
      row("итерация", it ? `№${it.index}` : "—")
    );
  }
  const z = hit.zone;
  return (
    `<div class="mb-1 text-xs font-semibold text-ink-100">Запретная зона</div>` +
    row("правило", esc(z.ruleId)) +
    row("отступ", `${z.distanceM.toFixed(2)} м`) +
    row("документ", esc(z.source.document))
  );
}

/** Places the drag annotation near the cursor — above it when possible so
 *  neither the mouse pointer nor a finger covers the text. */
function placeDragTip(tip: HTMLDivElement, box: HTMLDivElement, px: number, py: number) {
  const bw = box.clientWidth;
  const bh = box.clientHeight;
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  let x = px + 16;
  if (x + tw > bw - 4) x = Math.max(px - tw - 12, 4);
  let y = py - th - 16;
  if (y < 4) y = Math.min(py + 20, Math.max(bh - th - 4, 4));
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}

/** Auto-dismissing status line after a manual move ("Перемещено — …"). */
function MoveToast() {
  const toast = useApp((s) => s.moveToast);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => clearMoveToast(), 4000);
    return () => clearTimeout(t);
  }, [toast]);
  if (!toast) return null;
  const tones = {
    ok: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/90 dark:text-emerald-200",
    warn: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-600 dark:bg-amber-950/90 dark:text-amber-200",
    error: "border-red-300 bg-red-50 text-red-800 dark:border-red-700 dark:bg-red-950/90 dark:text-red-200",
  } as const;
  return (
    <div
      className={`pointer-events-none absolute bottom-3 left-1/2 z-20 -translate-x-1/2 whitespace-nowrap rounded-sm border px-3 py-1.5 text-xs shadow-lg shadow-black/10 dark:shadow-black/40 ${tones[toast.tone]}`}
      data-testid="move-toast"
    >
      {toast.text}
    </div>
  );
}

/** React wrapper around the imperative SceneManager. */
export function ViewerCanvas() {
  const [previewStatus,setPreviewStatus]=useState("Подготовка 3D-сцены…");
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const dragTipRef = useRef<HTMLDivElement>(null);
  const mgrRef = useRef<SceneManager | null>(null);
  const checkerRef = useRef<DragChecker | null>(null);
  if (!checkerRef.current) checkerRef.current = new DragChecker();

  const [reconstruction,setReconstruction]=useState({buildingHeightM:9,showFences:false});
  const [renderAspect,setRenderAspect]=useState<number|null>(16/9);
  const [frameSize,setFrameSize]=useState({width:0,height:0});
  useEffect(()=>{const el=containerRef.current;if(!el)return;const observer=new ResizeObserver(([entry])=>setFrameSize({width:entry.contentRect.width,height:entry.contentRect.height}));observer.observe(el);return()=>observer.disconnect();},[]);
  const doc = useApp((s) => s.document);
  useEffect(()=>{const initial={buildingHeightM:9,showFences:false};setReconstruction(initial);mgrRef.current?.setReconstruction(initial);},[doc?.projectId]);
  const constraints = useApp((s) => s.constraints);
  const iteration = useApp((s) => s.activeIteration);
  const chunks = useApp((s) => s.chunks);
  const overlays = useApp((s) => s.overlays);
  const layerVisibility = useApp((s) => s.layerVisibility);
  const selection = useApp((s) => s.selection);
  const viewMode = useApp((s) => s.viewMode);
  const massingDetail = useApp((s) => s.massingDetail);
  const species = useApp((s) => s.species);
  const [ageStop, setAgeStop] = useState(AGE_STOPS.length - 1);
  const plantAge = AGE_STOPS[ageStop];
  const [season, setSeason] = useState<Season>("summer");
  const theme = useTheme();

  useEffect(() => {
    if (!canvasRef.current) return;
    const mgr = new SceneManager(canvasRef.current, {
      onPreviewStatus:setPreviewStatus,
      onPickPlacement: (p) => select({ type: "placement", placement: p }),
      onPickZone: (zoneId) => {
        const zone = getState().constraints?.exclusionZones.find((z) => z.id === zoneId);
        if (zone) select({ type: "zone", zone });
      },
      onPickNothing: () => select(null),
      onCursor: (x, y) => hud.setCursor(x, y),
      onZoom: (z) => hud.setZoom(z),
      // Tooltip written straight to the DOM — no React re-renders on hover.
      onHover: (hit, px, py) => {
        const tip = tooltipRef.current;
        const box = containerRef.current;
        if (!tip || !box) return;
        if (!hit) {
          tip.style.display = "none";
          return;
        }
        tip.innerHTML = tooltipHtml(hit);
        tip.style.display = "block";
        const bw = box.clientWidth;
        const bh = box.clientHeight;
        const tw = tip.offsetWidth;
        const th = tip.offsetHeight;
        let x = px + 14;
        let y = py + 14;
        if (x + tw > bw - 4) x = Math.max(px - tw - 10, 4);
        if (y + th > bh - 4) y = Math.max(py - th - 10, 4);
        tip.style.left = `${x}px`;
        tip.style.top = `${y}px`;
      },
      // Drag-move (iteration 6): the floating annotation is written straight
      // to the DOM (like the hover tooltip); live checks are throttled to
      // ~4/s by DragChecker with a trailing call and stale-response guard.
      onPlacementDrag: (p, x, y, px, py) => {
        const tip = dragTipRef.current;
        const box = containerRef.current;
        if (!tip || !box) return;
        if (tip.style.display !== "block") {
          tip.innerHTML = dragAnnotationHtml(null); // "Проверка…"
          tip.style.display = "block";
        }
        tip.dataset.px = String(px);
        tip.dataset.py = String(py);
        placeDragTip(tip, box, px, py);
        const projectId = getState().document?.projectId;
        if (!projectId) return;
        checkerRef.current?.request(
          { projectId, x, y, kind: p.kind, speciesId: p.speciesId, excludeId: p.id },
          (res) => {
            if (tip.style.display !== "block") return; // drag already ended
            tip.innerHTML = dragAnnotationHtml(res);
            mgrRef.current?.setDragInvalid(res.verdict === "VIOLATIONS");
            // Re-anchor: the panel height changes with its content.
            placeDragTip(tip, box, Number(tip.dataset.px), Number(tip.dataset.py));
          },
        );
      },
      onPlacementDrop: (p, x, y) => {
        checkerRef.current?.reset();
        if (dragTipRef.current) dragTipRef.current.style.display = "none";
        void movePlacement(p, [x, y]);
      },
      onPlacementDragCancel: () => {
        checkerRef.current?.reset();
        if (dragTipRef.current) dragTipRef.current.style.display = "none";
      },
    }, getTheme());
    mgrRef.current = mgr;
    return () => {
      mgr.dispose();
      mgrRef.current = null;
    };
  }, []);

  useEffect(()=>{setMassingDetail(false);},[]);

  // Rebuild CAD geometry only when the entity set changes (new upload) —
  // classification overrides replace the document object but keep `entities`.
  useEffect(() => { mgrRef.current?.setDocument(doc); }, [doc?.entities]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (doc) mgrRef.current?.updateLayerInfo(doc.layers); }, [doc?.layers]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { mgrRef.current?.setConstraints(constraints); }, [constraints]);
  useEffect(() => { mgrRef.current?.setIteration(iteration); }, [iteration]);
  useEffect(() => { mgrRef.current?.setChunks(chunks); }, [chunks]);
  useEffect(() => { mgrRef.current?.setOverlays(overlays); }, [overlays]);
  useEffect(() => { mgrRef.current?.setLayerVisibility(layerVisibility); }, [layerVisibility]);
  useEffect(() => { mgrRef.current?.setTheme(theme); }, [theme]);
  useEffect(() => { mgrRef.current?.setMode(viewMode); }, [viewMode]);
  useEffect(() => { mgrRef.current?.setMassingDetail(massingDetail); }, [massingDetail]);
  useEffect(() => { mgrRef.current?.setPlantAge(plantAge, species); }, [plantAge, species, iteration]);
  useEffect(() => { mgrRef.current?.setSeason(season); }, [season, species, iteration]);
  useEffect(() => {
    mgrRef.current?.setSelectedPlacement(selection?.type === "placement" ? selection.placement : null);
  }, [selection]);

  // Stable identity: the panorama panel registers markers/pickers in effects.
  const panoramaTools = useMemo<PanoramaTools>(() => ({
    viewpoint: () => mgrRef.current?.panoramaViewpoint() ?? null,
    pick: picker => mgrRef.current?.setPointPicker(picker),
    markers: (points, onClick) => mgrRef.current?.setPanoramaMarkers(points, onClick),
  }), []);

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden md:flex-row">
    <div ref={containerRef} className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
      {viewMode==="3d"&&renderAspect&&frameSize.width>0&&<div className="pointer-events-none absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 border border-white/70" style={{width:Math.min(frameSize.width,frameSize.height*renderAspect),height:Math.min(frameSize.height,frameSize.width/renderAspect),boxShadow:"0 0 0 3000px rgba(0,0,0,.32)"}}><span className="absolute bottom-2 left-2 text-xs text-white">Границы рендера</span></div>}
      <canvas ref={canvasRef} className="block h-full w-full touch-none" />
      <div role="group" aria-label="Вид проекта" className="absolute right-3 top-3 z-20 flex rounded border border-ink-600 bg-ink-900/95 p-1 shadow-sm">
        {([ ["2d", "CAD"], ["3d", "3D-редактор"] ] as const).map(([mode, label]) => (
          <button key={mode} type="button" aria-pressed={viewMode === mode} onClick={() => setViewMode(mode)} className={`rounded px-3 py-1.5 text-xs transition-colors ${viewMode === mode ? "bg-ink-600 text-ink-100" : "text-ink-400 hover:bg-ink-800 hover:text-ink-100"}`}>{label}</button>
        ))}
      </div>
      {viewMode === "3d" && iteration && <div className="absolute right-3 top-14 z-20 w-56 rounded border border-ink-600 bg-ink-900/95 px-3 py-2 text-xs text-ink-200 shadow-sm" data-testid="plant-age">
        <label className="flex items-center justify-between gap-2"><span>Возраст посадок</span><span className="font-semibold">{ageLabel(plantAge)}</span></label>
        <input type="range" min={0} max={AGE_STOPS.length - 1} step={1} value={ageStop} aria-label="Возраст посадок"
          onChange={(e) => setAgeStop(Number(e.target.value))} className="mt-1 w-full accent-emerald-500" />
        <div className="flex justify-between text-2xs text-ink-400"><span>посадка</span><span>5</span><span>15</span><span>30 лет</span><span>зрелые</span></div>
        <div role="group" aria-label="Сезон" className="mt-2 flex rounded border border-ink-600 p-0.5">
          {(Object.keys(SEASON_LABELS) as Season[]).map((k) => (
            <button key={k} type="button" aria-pressed={season === k} onClick={() => setSeason(k)}
              className={`flex-1 rounded px-1 py-0.5 text-2xs ${season === k ? "bg-ink-600 text-ink-100" : "text-ink-400 hover:bg-ink-800"}`}>{SEASON_LABELS[k]}</button>
          ))}
        </div>
        <p className="mt-1 text-2xs text-ink-400" title="Размеры саженцев и годы до зрелости — условные садоводческие значения rules/species.yaml">
          Только визуализация по условной модели роста. Проверка норм и экспорт DXF всегда используют размеры зрелых растений; круги показывают зрелую крону.
        </p>
      </div>}
      {viewMode === "3d" && <div className="pointer-events-none absolute bottom-8 left-3 z-10 max-w-[calc(100%-1.5rem)] md:bottom-auto md:top-3 md:max-w-[42%] rounded border border-ink-600 bg-ink-900/90 px-3 py-2 text-xs text-ink-200" data-testid="editor-preview-status">{previewStatus}<div className="mt-1 text-2xs text-ink-400">Клик по кругу → перетащите посадку · F — весь участок</div><div className="mt-1 text-2xs text-ink-400">Выберите угол камеры, затем сгенерируйте рендер ниже</div></div>}
      <div
        ref={tooltipRef}
        style={{ display: "none" }}
        className="pointer-events-none absolute z-20 min-w-[160px] max-w-[260px] rounded-sm border border-ink-600 bg-ink-900/95 px-2 py-1.5 text-2xs text-ink-200 shadow-lg shadow-black/10 dark:shadow-black/40"
      />
      <div
        ref={dragTipRef}
        style={{ display: "none" }}
        data-testid="drag-check-panel"
        className="pointer-events-none absolute z-30 min-w-[200px] max-w-[300px] rounded-sm border border-ink-600 bg-ink-900/95 px-2 py-1.5 text-2xs text-ink-200 shadow-lg shadow-black/10 dark:shadow-black/40"
      />
      <MoveToast />
      <div className="pointer-events-none absolute bottom-2 right-2 hidden font-mono text-2xs text-ink-500 md:block">
        {viewMode === "2d"
          ? "F — вписать · перетаскивание — сдвиг · колесо — масштаб · выбранную посадку можно перетащить"
          : "F — вписать · перетаскивание — вращение · правая кнопка — сдвиг · колесо — приближение · выбранную посадку можно перетащить"}
      </div>
    </div>
    {doc&&viewMode === "3d"&&<RenderWorkflow appearance={{season,ageYears:plantAge}} onAppearanceChange={settings=>{setSeason(settings.season);setAgeStop(AGE_STOPS.findIndex(age=>age===settings.ageYears));}} key={doc.projectId} projectId={doc.projectId} reconstructionSettings={reconstruction} onReconstructionChange={settings=>{setReconstruction(settings);setPreviewStatus("Подготовка геометрии CAD…");mgrRef.current?.setReconstruction(settings);}} onAspectChange={setRenderAspect} sceneReady={previewStatus.startsWith("Макет CAD")} capture={(aspect,longEdge)=>{if(!mgrRef.current)throw Error("Сцена недоступна");return mgrRef.current.capturePhotoInput(aspect,longEdge);}} camera={view=>mgrRef.current?.setPresentationCamera(view)} panorama={panoramaTools}/>}
    </div>
  );
}
