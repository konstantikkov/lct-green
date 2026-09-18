import { useState } from "react";
import type { IterationSummary } from "../../api/types";
import { loadIteration } from "../../state/actions";
import { useApp } from "../../state/store";
import { Badge, Section, Spinner } from "../ui";

function JudgeDetails({ it }: { it: IterationSummary }) {
  const j = it.judge;
  if (!j) return <div className="px-1 py-1 text-2xs text-ink-500">no judge report</div>;
  const changes = Object.entries(j.recommendedParameterChanges ?? {});
  return (
    <div className="space-y-1.5 border-l border-ink-600 py-1 pl-2 text-2xs">
      <p className="text-ink-300">{j.reasoningSummary}</p>
      {j.issues.length > 0 && (
        <ul className="list-inside list-disc text-amber-300/90">
          {j.issues.map((iss, i) => <li key={i}>{iss}</li>)}
        </ul>
      )}
      {changes.length > 0 && (
        <div>
          <div className="mb-0.5 uppercase tracking-wider text-ink-500">Recommended changes</div>
          {changes.map(([param, next]) => {
            const old = it.parameters[param];
            return (
              <div key={param} className="flex items-center gap-1 font-mono text-ink-300">
                <span className="text-ink-400">{param}</span>
                <span className="text-ink-500">{old != null ? String(old) : "—"}</span>
                <span className="text-ink-600">→</span>
                <span className="text-emerald-300">{next != null ? String(next) : "—"}</span>
              </div>
            );
          })}
        </div>
      )}
      <div className="flex gap-2 font-mono text-ink-500">
        <span>overall {j.score.overall.toFixed(2)}</span>
        <span>conf {j.score.confidence.toFixed(2)}</span>
        {j.llmModel && <span>{j.llmModel}</span>}
      </div>
    </div>
  );
}

function IterationRow({ it }: { it: IterationSummary }) {
  const activeId = useApp((s) => s.activeIteration?.iterationId);
  const loadingId = useApp((s) => s.iterationLoadingId);
  const [expanded, setExpanded] = useState(false);
  const isActive = activeId === it.iterationId;

  return (
    <div className={`border-b border-ink-700/60 last:border-0 ${isActive ? "bg-ink-800/70" : ""}`}>
      <div
        className="flex cursor-pointer items-center gap-1.5 px-1 py-1.5 hover:bg-ink-800"
        onClick={() => void loadIteration(it.iterationId)}
      >
        <span className="w-6 font-mono text-2xs text-ink-400">
          #{it.index}{it.selected ? "★" : ""}
        </span>
        <span className="flex-1 font-mono text-2xs text-ink-200">
          {it.metrics.treeCount}t/{it.metrics.shrubCount}s
        </span>
        <span
          className={`font-mono text-2xs ${it.metrics.hardViolations > 0 ? "text-red-400" : "text-ink-500"}`}
          title="hard violations"
        >
          {it.metrics.hardViolations}v
        </span>
        <span className="font-mono text-2xs text-ink-400" title="coverage / distribution">
          {Math.round(it.metrics.coverageScore * 100)}/{Math.round(it.metrics.distributionScore * 100)}%
        </span>
        {loadingId === it.iterationId && <Spinner className="h-2.5 w-2.5" />}
        {it.judge && (
          <>
            <Badge tone={it.judge.decision === "ACCEPT" ? "green" : "amber"}>{it.judge.decision}</Badge>
            <Badge tone="grey">{it.judge.mode === "llm" ? "LLM" : "heuristic"}</Badge>
          </>
        )}
        <button
          className="px-0.5 text-ink-500 hover:text-ink-200"
          onClick={(e) => { e.stopPropagation(); setExpanded((x) => !x); }}
          title="Judge details"
        >
          {expanded ? "▾" : "▸"}
        </button>
      </div>
      {expanded && <JudgeDetails it={it} />}
    </div>
  );
}

export function IterationsPanel() {
  const iterations = useApp((s) => s.iterations);
  const doc = useApp((s) => s.document);
  if (!doc || iterations.length === 0) return null;

  return (
    <Section title={`Iterations (${iterations.length})`}>
      <div className="text-2xs text-ink-500">★ = selected final solution · click row to view</div>
      {iterations.map((it) => <IterationRow key={it.iterationId} it={it} />)}
    </Section>
  );
}
