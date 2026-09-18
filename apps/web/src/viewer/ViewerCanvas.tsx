import { useEffect, useRef } from "react";
import { SceneManager, type HoverHit } from "./SceneManager";
import { hud } from "./hud";
import { useApp, getState, type ViewMode } from "../state/store";
import { select, setViewMode } from "../state/actions";
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
      ? `${esc(sp.displayNameRu)} <span class="text-ink-400 italic">${esc(sp.displayName)}</span>`
      : esc(p.speciesId);
    return (
      `<div class="mb-1 text-xs font-semibold text-ink-100">${title}</div>` +
      row("id", esc(p.id)) +
      row("crown", `${p.crownRadiusM.toFixed(2)} m`) +
      row("score", p.score.toFixed(3)) +
      row("iteration", it ? `#${it.index}` : "—")
    );
  }
  const z = hit.zone;
  return (
    `<div class="mb-1 text-xs font-semibold text-ink-100">Exclusion zone</div>` +
    row("rule", esc(z.ruleId)) +
    row("distance", `${z.distanceM.toFixed(2)} m`) +
    row("document", esc(z.source.document))
  );
}

function ModeToggle({ mode }: { mode: ViewMode }) {
  const seg = (m: ViewMode, label: string) => (
    <button
      className={`h-6 px-2.5 text-2xs font-semibold transition-colors ${
        mode === m
          ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-800/70 dark:text-emerald-100"
          : "bg-ink-900 text-ink-400 hover:text-ink-100"
      }`}
      onClick={() => setViewMode(m)}
      title={m === "2d" ? "Orthographic plan view" : "3D massing view"}
    >
      {label}
    </button>
  );
  return (
    <div className="absolute right-2 top-2 z-10 flex overflow-hidden rounded-sm border border-ink-600 shadow-sm">
      {seg("2d", "2D")}
      {seg("3d", "3D")}
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
  useEffect(() => {
    mgrRef.current?.setSelectedPlacement(selection?.type === "placement" ? selection.placement : null);
  }, [selection]);

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden">
      <canvas ref={canvasRef} className="block h-full w-full" />
      <ModeToggle mode={viewMode} />
      <div
        ref={tooltipRef}
        style={{ display: "none" }}
        className="pointer-events-none absolute z-20 min-w-[160px] max-w-[260px] rounded-sm border border-ink-600 bg-ink-900/95 px-2 py-1.5 text-2xs text-ink-200 shadow-lg shadow-black/10 dark:shadow-black/40"
      />
      <div className="pointer-events-none absolute bottom-2 right-2 font-mono text-2xs text-ink-500">
        {viewMode === "2d"
          ? "F — fit · drag — pan · wheel — zoom"
          : "F — fit · drag — orbit · right-drag — pan · wheel — dolly"}
      </div>
    </div>
  );
}
