import { useEffect, useRef } from "react";
import { SceneManager, type HoverHit } from "./SceneManager";
import { hud } from "./hud";
import { useApp, getState, type ViewMode } from "../state/store";
import { select, setMassingDetail, setViewMode } from "../state/actions";
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

function ViewerToggles({ mode, detail }: { mode: ViewMode; detail: boolean }) {
  return (
    <div className="absolute right-2 top-2 z-10 flex flex-col items-end gap-1.5">
      <div className="flex overflow-hidden rounded-sm border border-ink-600 shadow-sm">
        <Seg active={mode === "2d"} label="2D" title="План сверху"
          onClick={() => setViewMode("2d")} />
        <Seg active={mode === "3d"} label="3D" title="Объёмный вид"
          onClick={() => setViewMode("3d")} />
      </div>
      {mode === "3d" && (
        <div className="flex overflow-hidden rounded-sm border border-ink-600 shadow-sm">
          <Seg active={!detail} label="Просто"
            title="Только объёмы: земля, дороги, здания, посадки"
            onClick={() => setMassingDetail(false)} />
          <Seg active={detail} label="Подробно"
            title="Плюс линии исходного чертежа, фонари и ограждения"
            onClick={() => setMassingDetail(true)} />
        </div>
      )}
    </div>
  );
}

/** React wrapper around the imperative SceneManager. */
export function ViewerCanvas() {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const mgrRef = useRef<SceneManager | null>(null);

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
      <div
        ref={tooltipRef}
        style={{ display: "none" }}
        className="pointer-events-none absolute z-20 min-w-[160px] max-w-[260px] rounded-sm border border-ink-600 bg-ink-900/95 px-2 py-1.5 text-2xs text-ink-200 shadow-lg shadow-black/10 dark:shadow-black/40"
      />
      <div className="pointer-events-none absolute bottom-2 right-2 hidden font-mono text-2xs text-ink-500 md:block">
        {viewMode === "2d"
          ? "F — вписать · перетаскивание — сдвиг · колесо — масштаб"
          : "F — вписать · перетаскивание — вращение · правая кнопка — сдвиг · колесо — приближение"}
      </div>
    </div>
  );
}
