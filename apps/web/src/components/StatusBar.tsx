import { useEffect, useRef } from "react";
import { hud } from "../viewer/hud";
import { useApp } from "../state/store";

export function StatusBar() {
  const coordsRef = useRef<HTMLSpanElement>(null);
  const zoomRef = useRef<HTMLSpanElement>(null);
  const doc = useApp((s) => s.document);
  const run = useApp((s) => s.run);
  const runError = useApp((s) => s.runError);

  useEffect(() => {
    hud.coordsEl = coordsRef.current;
    hud.zoomEl = zoomRef.current;
    return () => {
      hud.coordsEl = null;
      hud.zoomEl = null;
    };
  }, []);

  const activeStage = run?.stages.find((s) => s.status === "active");
  const stageMsg = runError
    ? `error: ${runError}`
    : activeStage
      ? `${activeStage.stage}${activeStage.detail ? ` — ${activeStage.detail}` : ""}`
      : run?.state === "COMPLETED"
        ? "run completed"
        : "";

  return (
    <footer className="flex h-7 shrink-0 items-center gap-4 border-t border-ink-600 bg-ink-900 px-3 font-mono text-2xs text-ink-300">
      <span ref={coordsRef} className="w-56">X — m   Y — m</span>
      <span ref={zoomRef} className="w-24 text-ink-400" />
      <span className="text-ink-400">
        {doc ? `${doc.entityCount.toLocaleString()} entities` : "—"}
      </span>
      <span className={`flex-1 truncate text-right ${runError ? "text-red-600 dark:text-red-400" : "text-ink-400"}`}>
        {stageMsg}
      </span>
    </footer>
  );
}
