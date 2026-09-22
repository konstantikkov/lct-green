import { useRef } from "react";
import { setMobileTab, setSheetSnap } from "../state/actions";
import { useApp, type MobileTab, type SheetSnap } from "../state/store";
import { InspectorContent } from "./Inspector";
import { ConstraintsPanel } from "./panels/ConstraintsPanel";
import { GeneratePanel } from "./panels/GeneratePanel";
import { LayersPanel } from "./panels/LayersPanel";
import { QualityCard } from "./panels/QualityCard";
import { RunsList } from "./views/RunsView";

const TABS: { id: MobileTab; label: string }[] = [
  { id: "layers", label: "Layers" },
  { id: "constraints", label: "Constraints" },
  { id: "generate", label: "Generate" },
  { id: "iterations", label: "Runs" },
  { id: "inspector", label: "Inspector" },
];

/**
 * Snap transforms are expressed relative to the sheet's own height (88vh/dvh)
 * so the browser recomputes them on rotation / URL-bar collapse for free.
 * peek  — handle + active tab title only (~56px + safe-area inset)
 * half  — 45vh of content
 * full  — the whole sheet
 */
const SNAP_TRANSFORMS: Record<SheetSnap, string> = {
  peek: "translateY(calc(100% - 56px - env(safe-area-inset-bottom)))",
  half: "translateY(calc(100% - 45vh))",
  full: "translateY(0px)",
};

interface DragState {
  pointerId: number;
  startY: number;
  /** translateY (px) at drag start. */
  startTranslate: number;
  /** peek height in px (56 + safe-area padding), cached at drag start. */
  peekPx: number;
  moved: boolean;
  startTime: number;
}

function TabContent({ tab }: { tab: MobileTab }) {
  const iterations = useApp((s) => s.iterations);
  switch (tab) {
    case "layers":
      return <LayersPanel />;
    case "constraints":
      return <ConstraintsPanel />;
    case "generate":
      return iterations.length > 0
        ? <><GeneratePanel /><QualityCard /></>
        : <GeneratePanel />;
    case "iterations":
      return iterations.length > 0
        ? <RunsList />
        : <div className="p-3 text-xs text-ink-500">No runs yet — run "Generate" first.</div>;
    case "inspector":
      return <InspectorContent />;
  }
}

/**
 * Mobile-only (< md) bottom sheet hosting the desktop side panels as tabs.
 * Hand-rolled drag: pointer events move the sheet via translateY; on release
 * it snaps to the nearest of peek/half/full. Tapping the handle toggles
 * peek <-> half.
 */
export function BottomSheet() {
  const snap = useApp((s) => s.sheetSnap);
  const tab = useApp((s) => s.mobileTab);
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);

  const applySnap = (next: SheetSnap) => {
    // Write the target transform imperatively too: if `next` equals the
    // current snap React skips the re-render and the style must still land.
    const el = sheetRef.current;
    if (el) el.style.transform = SNAP_TRANSFORMS[next];
    setSheetSnap(next);
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = sheetRef.current;
    if (!el || dragRef.current) return;
    const rect = el.getBoundingClientRect();
    dragRef.current = {
      pointerId: e.pointerId,
      startY: e.clientY,
      // fixed bottom-0: untranslated top is (viewport height - sheet height)
      startTranslate: rect.top - (window.innerHeight - el.offsetHeight),
      peekPx: 56 + (parseFloat(getComputedStyle(el).paddingBottom) || 0),
      moved: false,
      startTime: performance.now(),
    };
    e.currentTarget.setPointerCapture(e.pointerId);
    el.style.transition = "none";
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    const el = sheetRef.current;
    if (!d || !el || e.pointerId !== d.pointerId) return;
    const dy = e.clientY - d.startY;
    if (Math.abs(dy) > 6) d.moved = true;
    const t = Math.min(Math.max(d.startTranslate + dy, 0), el.offsetHeight - d.peekPx);
    el.style.transform = `translateY(${t}px)`;
  };

  const endDrag = (e: React.PointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const d = dragRef.current;
    const el = sheetRef.current;
    if (!d || !el || e.pointerId !== d.pointerId) return;
    dragRef.current = null;
    el.style.transition = "";

    // Tap on the handle: peek -> half, anything higher -> peek.
    if (!cancelled && !d.moved && performance.now() - d.startTime < 300) {
      applySnap(snap === "peek" ? "half" : "peek");
      return;
    }

    // Snap to the state whose height is nearest to where the drag ended.
    const visible = window.innerHeight - el.getBoundingClientRect().top;
    const candidates: [SheetSnap, number][] = [
      ["peek", d.peekPx],
      ["half", window.innerHeight * 0.45],
      ["full", el.offsetHeight],
    ];
    let best: SheetSnap = "peek";
    let bestDelta = Infinity;
    for (const [s, h] of candidates) {
      const delta = Math.abs(visible - h);
      if (delta < bestDelta) {
        bestDelta = delta;
        best = s;
      }
    }
    applySnap(best);
  };

  const activeLabel = TABS.find((t) => t.id === tab)?.label ?? "";

  return (
    <div
      ref={sheetRef}
      className="fixed inset-x-0 bottom-0 z-30 flex h-[88vh] flex-col rounded-t-xl border-x border-t border-ink-600 bg-ink-900 shadow-[0_-8px_28px_rgba(0,0,0,0.25)] transition-transform duration-300 ease-out will-change-transform md:hidden"
      style={{
        height: "88dvh", // overrides the vh fallback where dvh is supported
        transform: SNAP_TRANSFORMS[snap],
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
    >
      {/* Drag handle — the only drag surface, so tab bar / content scroll freely. */}
      <div
        className="flex h-14 shrink-0 cursor-grab touch-none select-none flex-col items-center justify-center gap-1.5"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={(e) => endDrag(e, false)}
        onPointerCancel={(e) => endDrag(e, true)}
      >
        <div className="h-1 w-9 rounded-full bg-ink-500" />
        <div className="text-2xs font-semibold uppercase tracking-wider text-ink-300">{activeLabel}</div>
      </div>

      {/* Tab bar — horizontally scrollable row of compact tabs. */}
      <div
        className="flex shrink-0 gap-1 overflow-x-auto border-b border-ink-600 px-2 pb-2"
        style={{ WebkitOverflowScrolling: "touch" }}
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`shrink-0 whitespace-nowrap rounded-sm border px-2.5 py-1.5 text-xs transition-colors ${
              t.id === tab
                ? "border-ink-500 bg-ink-800 text-ink-100"
                : "border-transparent text-ink-400 hover:text-ink-200"
            }`}
            onClick={() => {
              setMobileTab(t.id);
              if (snap === "peek") applySnap("half");
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
        style={{ WebkitOverflowScrolling: "touch" }}
      >
        <TabContent tab={tab} />
      </div>
    </div>
  );
}
