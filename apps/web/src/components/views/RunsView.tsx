import { useState } from "react";
import type { IterationSummary, JudgeIssueV2 } from "../../api/types";
import { loadIteration, setNav } from "../../state/actions";
import { useApp } from "../../state/store";
import { Badge, Spinner } from "../ui";

function issueText(i: string | JudgeIssueV2): string {
  return typeof i === "string" ? i : i.description;
}

function decisionTone(d: string): "green" | "amber" | "violet" {
  if (d === "ACCEPT") return "green";
  if (d === "RERUN") return "amber";
  return "violet";
}

function JudgeDetails({ it }: { it: IterationSummary }) {
  const j = it.judge;
  if (it.judgeSkipped === "hard_violations") {
    return (
      <div className="px-1 py-1 text-2xs text-ink-500">
        Judge skipped — hard violations repaired deterministically
        {it.deterministicRepair ? ` (removed ${it.deterministicRepair.removed})` : ""}.
      </div>
    );
  }
  if (!j) return <div className="px-1 py-1 text-2xs text-ink-500">no judge report</div>;

  const summary = j.summary ?? j.reasoningSummary ?? "";
  const issues = j.issues ?? [];
  const v2Changes = j.parameterChanges ?? [];
  const v1Changes = Object.entries(j.recommendedParameterChanges ?? {});

  return (
    <div className="space-y-1.5 border-l border-ink-600 py-1 pl-2 text-2xs">
      {summary && <p className="text-ink-300">{summary}</p>}
      {issues.length > 0 && (
        <ul className="list-inside list-disc text-amber-700/90 dark:text-amber-300/90">
          {issues.map((iss, i) => <li key={i}>{issueText(iss)}</li>)}
        </ul>
      )}
      {v2Changes.length > 0 ? (
        <div>
          <div className="mb-0.5 uppercase tracking-wider text-ink-500">Parameter changes</div>
          {v2Changes.map((ch) => (
            <div key={ch.parameter} className="flex items-center gap-1 font-mono text-ink-300">
              <span className="text-ink-400">{ch.parameter}</span>
              <span className="text-ink-500">{Number.isFinite(ch.currentValue) ? ch.currentValue : "—"}</span>
              <span className="text-ink-600">→</span>
              <span className="text-emerald-700 dark:text-emerald-300">{ch.proposedValue}</span>
              {ch.reason && <span className="ml-1 truncate text-ink-500">{ch.reason}</span>}
            </div>
          ))}
        </div>
      ) : v1Changes.length > 0 ? (
        <div>
          <div className="mb-0.5 uppercase tracking-wider text-ink-500">Recommended changes</div>
          {v1Changes.map(([param, next]) => {
            const old = it.parameters[param];
            return (
              <div key={param} className="flex items-center gap-1 font-mono text-ink-300">
                <span className="text-ink-400">{param}</span>
                <span className="text-ink-500">{old != null ? String(old) : "—"}</span>
                <span className="text-ink-600">→</span>
                <span className="text-emerald-700 dark:text-emerald-300">{next != null ? String(next) : "—"}</span>
              </div>
            );
          })}
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2 font-mono text-ink-500">
        {j.score?.overall != null && <span>overall {j.score.overall.toFixed(2)}</span>}
        {j.confidence != null && <span>conf {j.confidence.toFixed(2)}</span>}
        {j.score?.confidence != null && j.confidence == null && <span>conf {j.score.confidence.toFixed(2)}</span>}
        {(j.model ?? j.llmModel) && <span>{j.model ?? j.llmModel}</span>}
        {j.promptVersion && <span>prompt {j.promptVersion}</span>}
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
        <span className="w-7 font-mono text-2xs text-ink-400">
          #{it.index}{it.selected ? "★" : ""}
        </span>
        <span className="flex-1 font-mono text-2xs text-ink-200">
          {it.metrics.treeCount}t/{it.metrics.shrubCount}s
        </span>
        <span
          className={`font-mono text-2xs ${it.metrics.hardViolations > 0 ? "text-red-600 dark:text-red-400" : "text-ink-500"}`}
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
            <Badge tone={decisionTone(it.judge.decision)}>
              {it.judge.decision === "MANUAL_REVIEW" ? "REVIEW" : it.judge.decision}
            </Badge>
            <Badge tone="grey">{it.judge.mode === "llm" ? "LLM" : "heuristic"}</Badge>
          </>
        )}
        {it.judgeSkipped === "hard_violations" && (
          <Badge tone="grey" title="Judge skipped: repaired deterministically">skipped</Badge>
        )}
        <button
          className="px-0.5 text-ink-500 hover:text-ink-200"
          onClick={(e) => { e.stopPropagation(); setExpanded((x) => !x); }}
          title="Details"
        >
          {expanded ? "▾" : "▸"}
        </button>
      </div>
      {expanded && (
        <div className="px-1 pb-1.5">
          <JudgeDetails it={it} />
          <div className="mt-1 font-mono text-2xs text-ink-500">
            seed {it.seed} · {Object.entries(it.parameters).map(([k, v]) => `${k}=${v}`).join(" · ")}
          </div>
        </div>
      )}
    </div>
  );
}

/** Compact list — reused by the mobile bottom sheet's Runs tab. */
export function RunsList() {
  const iterations = useApp((s) => s.iterations);
  if (iterations.length === 0) {
    return <div className="p-3 text-xs text-ink-500">No runs yet — generate a plan first.</div>;
  }
  return (
    <div className="p-1">
      <div className="px-1 pb-1 text-2xs text-ink-500">★ = selected final solution · click row to view on canvas</div>
      {iterations.map((it) => <IterationRow key={it.iterationId} it={it} />)}
    </div>
  );
}

/** RUNS view: iteration list + side-by-side metric comparison. */
export function RunsView() {
  const iterations = useApp((s) => s.iterations);

  return (
    <div className="h-full w-full overflow-y-auto bg-ink-950" data-testid="runs-view">
      <div className="mx-auto max-w-3xl px-4 py-6">
        <h1 className="text-sm font-semibold uppercase tracking-wider text-ink-200">Runs</h1>
        {iterations.length === 0 ? (
          <div className="mt-4 text-xs text-ink-500">
            No runs yet.{" "}
            <button className="underline decoration-dotted hover:text-ink-200" onClick={() => setNav("plan")}>
              Go to the plan
            </button>{" "}
            and generate a planting plan first.
          </div>
        ) : (
          <>
            {iterations.length > 1 && (
              <div className="mt-4 overflow-x-auto border border-ink-600 bg-ink-900">
                <table className="w-full text-left">
                  <thead>
                    <tr className="text-2xs uppercase tracking-wider text-ink-500">
                      <th className="px-2 py-1.5 font-medium">Iteration</th>
                      <th className="px-2 py-1.5 text-right font-medium">Trees</th>
                      <th className="px-2 py-1.5 text-right font-medium">Shrubs</th>
                      <th className="px-2 py-1.5 text-right font-medium">Violations</th>
                      <th className="px-2 py-1.5 text-right font-medium">Coverage</th>
                      <th className="px-2 py-1.5 text-right font-medium">Distribution</th>
                      <th className="px-2 py-1.5 text-right font-medium">Canopy m²</th>
                    </tr>
                  </thead>
                  <tbody>
                    {iterations.map((it) => (
                      <tr key={it.iterationId} className="border-t border-ink-700/60 font-mono text-2xs text-ink-200">
                        <td className="px-2 py-1">#{it.index}{it.selected ? " ★" : ""}</td>
                        <td className="px-2 py-1 text-right">{it.metrics.treeCount}</td>
                        <td className="px-2 py-1 text-right">{it.metrics.shrubCount}</td>
                        <td className={`px-2 py-1 text-right ${it.metrics.hardViolations > 0 ? "text-red-600 dark:text-red-400" : ""}`}>
                          {it.metrics.hardViolations}
                        </td>
                        <td className="px-2 py-1 text-right">{Math.round(it.metrics.coverageScore * 100)}%</td>
                        <td className="px-2 py-1 text-right">{Math.round(it.metrics.distributionScore * 100)}%</td>
                        <td className="px-2 py-1 text-right">
                          {it.metrics.canopyAreaM2.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="mt-4 border border-ink-600 bg-ink-900">
              <RunsList />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
