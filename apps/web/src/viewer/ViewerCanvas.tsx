import { useEffect, useRef } from "react";
import { SceneManager } from "./SceneManager";
import { hud } from "./hud";
import { useApp } from "../state/store";
import { select } from "../state/actions";
import { getState } from "../state/store";

/** React wrapper around the imperative SceneManager. */
export function ViewerCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mgrRef = useRef<SceneManager | null>(null);

  const doc = useApp((s) => s.document);
  const constraints = useApp((s) => s.constraints);
  const iteration = useApp((s) => s.activeIteration);
  const chunks = useApp((s) => s.chunks);
  const overlays = useApp((s) => s.overlays);
  const layerVisibility = useApp((s) => s.layerVisibility);
  const selection = useApp((s) => s.selection);

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
    });
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
  useEffect(() => {
    mgrRef.current?.setSelectedPlacement(selection?.type === "placement" ? selection.placement : null);
  }, [selection]);

  return (
    <div className="relative h-full w-full overflow-hidden">
      <canvas ref={canvasRef} className="block h-full w-full" />
      <div className="pointer-events-none absolute bottom-2 right-2 font-mono text-2xs text-ink-500">
        F — fit to extents · drag — pan · wheel — zoom
      </div>
    </div>
  );
}
