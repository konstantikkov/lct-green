import { setNav } from "../state/actions";
import { useApp, useStage } from "../state/store";
import { ConstraintsPanel } from "./panels/ConstraintsPanel";
import { GeneratePanel } from "./panels/GeneratePanel";
import { LayersPanel } from "./panels/LayersPanel";
import { QualityCard } from "./panels/QualityCard";

/** Compact interpretation summary — replaces the metadata noise in the top bar. */
function InterpretationHeader() {
  const doc = useApp((s) => s.document);
  if (!doc) return null;
  return (
    <div className="border-b border-ink-600 px-2 py-2" data-testid="interpretation-summary">
      <div className="truncate text-xs font-semibold text-ink-100" title={doc.fileName}>
        {doc.fileName}
      </div>
      <div className="mt-0.5 font-mono text-2xs text-ink-500">
        {doc.entityCount.toLocaleString()} entities · {doc.layerCount} layers · units {doc.units}
      </div>
    </div>
  );
}

/** One-line current-result summary; the full list lives in the RUNS view. */
function ResultSummary() {
  const iterations = useApp((s) => s.iterations);
  if (iterations.length === 0) return null;
  const it = iterations.find((x) => x.selected) ?? iterations[iterations.length - 1];
  return (
    <div className="border-b border-ink-600 px-2 py-1.5 text-2xs text-ink-300" data-testid="result-summary">
      <span className="font-mono">
        {it.metrics.treeCount} trees · {it.metrics.shrubCount} shrubs · iteration #{it.index}
        {it.selected ? " ★" : ""}
      </span>
      {iterations.length > 1 && (
        <button
          className="ml-1.5 text-ink-400 underline decoration-dotted hover:text-ink-200"
          onClick={() => setNav("runs")}
        >
          View {iterations.length} runs →
        </button>
      )}
      <div className="mt-0.5 text-ink-500">Click a tree on the canvas to see why it is placed there.</div>
    </div>
  );
}

/** Desktop PLAN sidebar — sections appear by stage (progressive disclosure). */
export function PlanSidebar() {
  const doc = useApp((s) => s.document);
  const stage = useStage();
  if (!doc) return null;

  return (
    <aside className="hidden w-[280px] shrink-0 flex-col overflow-y-auto border-r border-ink-600 bg-ink-900 md:flex">
      <InterpretationHeader />
      <LayersPanel />
      <ConstraintsPanel />
      <GeneratePanel />
      {stage === "generated" && (
        <>
          <ResultSummary />
          <QualityCard />
        </>
      )}
    </aside>
  );
}
