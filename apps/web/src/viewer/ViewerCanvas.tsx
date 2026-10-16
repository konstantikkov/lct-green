import { useEffect, useRef, useState } from "react";
import { SceneManager, type HoverHit } from "./SceneManager";
import { DragChecker, dragAnnotationHtml } from "./dragCheck";
import { hud } from "./hud";
import { useApp, getState, type ViewMode } from "../state/store";
import { clearMoveToast, movePlacement, select, setMassingDetail, setViewMode } from "../state/actions";
import { getTheme, useTheme } from "../theme";

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

/** Segmented-control button; ≥32px touch target on mobile, compact on md+. */
function Seg({ active, label, title, onClick }: {
  active: boolean; label: string; title: string; onClick: () => void;
}) {
  return (
    <button
      className={`h-8 min-w-8 px-2.5 text-2xs font-semibold transition-colors md:h-6 ${
        active
          ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-800/70 dark:text-emerald-100"
          : "bg-ink-900 text-ink-400 hover:text-ink-100"
      }`}
      onClick={onClick}
      title={title}
    >
      {label}
    </button>
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

function ViewerToggles({ mode, detail }: { mode: ViewMode; detail: boolean }) {
  return (
    <div className="absolute right-2 top-2 z-10 flex flex-col items-end gap-1.5">
      <div className="flex overflow-hidden rounded-sm border border-ink-600 shadow-sm">
        <Seg active={mode === "2d"} label="CAD" title="Исходный план и посадки"
          onClick={() => setViewMode("2d")} />
        <Seg active={mode === "3d"} label="3D-редактор" title="Сцена Blender с редактируемыми посадками"
          onClick={() => setViewMode("3d")} />
      </div>
      {mode === "3d" && (
        <div className="flex overflow-hidden rounded-sm border border-ink-600 shadow-sm">
          <Seg active={detail} label="CAD-подложка" title="Показать или скрыть исходные линии под сценой"
            onClick={() => setMassingDetail(!detail)} />
        </div>
      )}
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

  const doc = useApp((s) => s.document);
  const constraints = useApp((s) => s.constraints);
  const iteration = useApp((s) => s.activeIteration);
  const chunks = useApp((s) => s.chunks);
  const overlays = useApp((s) => s.overlays);
  const layerVisibility = useApp((s) => s.layerVisibility);
  const selection = useApp((s) => s.selection);
  const viewMode = useApp((s) => s.viewMode);
  const massingDetail = useApp((s) => s.massingDetail);
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
  useEffect(() => {
    mgrRef.current?.setSelectedPlacement(selection?.type === "placement" ? selection.placement : null);
  }, [selection]);

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden">
      <canvas ref={canvasRef} className="block h-full w-full touch-none" />
      <ViewerToggles mode={viewMode} detail={massingDetail} />
      {viewMode === "3d" && <div className="pointer-events-none absolute left-3 top-3 z-10 max-w-[55%] rounded border border-ink-600 bg-ink-900/90 px-3 py-2 text-xs text-ink-200" data-testid="editor-preview-status">{previewStatus}<div className="mt-1 text-2xs text-ink-400">Клик по кругу → перетащите посадку · F — весь участок</div></div>}
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
  );
}
