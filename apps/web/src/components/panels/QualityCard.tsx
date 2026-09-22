import { useState } from "react";
import type {
  AssessmentDimension, AssessmentKey, IterationSummary, JudgeIssueV2,
  JudgeParameterChange, JudgeReport,
} from "../../api/types";
import { useApp } from "../../state/store";
import { Badge, MetricBar, Section } from "../ui";

const ASSESSMENT_LABELS: Record<AssessmentKey, string> = {
  coverage: "Coverage",
  distribution: "Distribution",
  clustering: "Clustering",
  utilization: "Utilization",
  robustnessToUncertainty: "Robustness",
};

function isV2Issue(i: string | JudgeIssueV2): i is JudgeIssueV2 {
  return typeof i === "object" && i != null && "description" in i;
}

/** Normalizes v1/v2 judge shapes into what the card renders. */
function normalizeJudge(it: IterationSummary) {
  const j: JudgeReport | null = it.judge;
  const components = j?.deterministicComponents ?? null;
  // Component bars: v2 deterministicComponents; v1/old records fall back to
  // the validator metrics (deterministic too — never the LLM's own score).
  const bars: { label: string; value: number; invert?: boolean }[] = components
    ? [
        { label: "Coverage", value: components.coverage },
        { label: "Distribution", value: components.distribution },
        { label: "Utilization", value: components.utilizationBalance },
        { label: "Cluster penalty", value: components.clusterPenalty, invert: true },
      ]
    : [
        { label: "Coverage", value: it.metrics.coverageScore },
        { label: "Distribution", value: it.metrics.distributionScore },
      ];
  // "Spatial quality" = deterministic mean (penalties inverted). NOT an LLM number.
  const contributions = components
    ? [components.coverage, components.distribution, components.utilizationBalance, 1 - components.clusterPenalty]
    : [it.metrics.coverageScore, it.metrics.distributionScore];
  const overall = contributions.reduce((a, b) => a + b, 0) / contributions.length;

  const issues: (string | JudgeIssueV2)[] = j?.issues ?? [];
  const changes: JudgeParameterChange[] = j?.parameterChanges
    ?? Object.entries(j?.recommendedParameterChanges ?? {})
      .filter(([, v]) => v != null)
      .map(([parameter, proposedValue]) => ({
        parameter,
        currentValue: Number(it.parameters[parameter] ?? NaN),
        proposedValue: proposedValue as number,
        reason: "",
        evidenceIds: [],
      }));
  const summary = j?.summary ?? j?.reasoningSummary ?? "";
  const assessment = j?.assessment ?? null;
  return { judge: j, bars, overall, issues, changes, summary, assessment };
}

function DecisionChip({ judge }: { judge: JudgeReport }) {
  if (judge.decision === "ACCEPT") return <Badge tone="green">ACCEPT</Badge>;
  if (judge.decision === "RERUN") return <Badge tone="amber">RERUN</Badge>;
  return <Badge tone="violet" title="The judge abstained — a human should look at this plan">needs human review</Badge>;
}

function DimensionRow({ name, dim }: { name: AssessmentKey; dim: AssessmentDimension }) {
  const tone = dim.status === "GOOD" ? "green" : dim.status === "IMPROVABLE" ? "amber" : "red";
  return (
    <div className="flex items-center gap-1.5 py-0.5">
      <span className="w-24 shrink-0 text-2xs text-ink-400">{ASSESSMENT_LABELS[name]}</span>
      <Badge tone={tone}>{dim.status}</Badge>
      {dim.evidence.length > 0 && (
        <span className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-500" title={dim.evidence.join(", ")}>
          {dim.evidence.join(" ")}
        </span>
      )}
    </div>
  );
}

function ReviewDetails({ it }: { it: IterationSummary }) {
  const { issues, changes, summary, assessment } = normalizeJudge(it);
  return (
    <div className="mt-2 space-y-2 border-t border-ink-700 pt-2">
      {assessment && (
        <div>
          <div className="mb-0.5 text-2xs uppercase tracking-wider text-ink-500">Assessment</div>
          {(Object.entries(assessment) as [AssessmentKey, AssessmentDimension][]).map(([k, dim]) =>
            dim ? <DimensionRow key={k} name={k} dim={dim} /> : null,
          )}
        </div>
      )}

      {issues.length > 0 && (
        <div>
          <div className="mb-0.5 text-2xs uppercase tracking-wider text-ink-500">Issues</div>
          <div className="space-y-1">
            {issues.map((iss, i) =>
              isV2Issue(iss) ? (
                <div key={i} className="flex items-start gap-1.5 text-2xs">
                  <Badge tone={iss.severity === "HIGH" ? "red" : iss.severity === "MEDIUM" ? "amber" : "grey"}>
                    {iss.severity}
                  </Badge>
                  <span className="min-w-0 flex-1 text-ink-300">
                    {iss.description}
                    {iss.evidenceIds.length > 0 && (
                      <span className="ml-1 font-mono text-ink-500">[{iss.evidenceIds.join(", ")}]</span>
                    )}
                  </span>
                </div>
              ) : (
                <div key={i} className="text-2xs text-ink-300">• {iss}</div>
              ),
            )}
          </div>
        </div>
      )}

      {changes.length > 0 && (
        <div>
          <div className="mb-0.5 text-2xs uppercase tracking-wider text-ink-500">Parameter changes</div>
          <table className="w-full">
            <tbody>
              {changes.map((ch) => (
                <tr key={ch.parameter} className="border-b border-ink-700/50 align-top last:border-0">
                  <td className="py-0.5 pr-1 font-mono text-2xs text-ink-400">{ch.parameter}</td>
                  <td className="whitespace-nowrap py-0.5 pr-1 font-mono text-2xs text-ink-300">
                    {Number.isFinite(ch.currentValue) ? ch.currentValue : "—"}
                    <span className="text-ink-600"> → </span>
                    <span className="text-emerald-700 dark:text-emerald-300">{ch.proposedValue}</span>
                  </td>
                  <td className="py-0.5 text-2xs text-ink-500">{ch.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {summary && <p className="text-2xs leading-relaxed text-ink-400">{summary}</p>}
    </div>
  );
}

/**
 * Compact post-generation quality review card (PLAN sidebar).
 * Hard pass comes from the deterministic validator; component bars come from
 * deterministicComponents (computed in code) — never from the LLM.
 */
export function QualityCard() {
  const iterations = useApp((s) => s.iterations);
  const active = useApp((s) => s.activeIteration);
  const [expanded, setExpanded] = useState(false);

  const it = active ?? iterations.find((x) => x.selected) ?? iterations[iterations.length - 1];
  if (!it) return null;

  const validation = active?.iterationId === it.iterationId ? active?.validation : undefined;
  const hardPassed = validation ? validation.valid : it.metrics.hardViolations === 0;
  const { judge, bars, overall, issues } = normalizeJudge(it);

  return (
    <Section title="Quality review">
      <div data-testid="quality-card">
        <div className="flex items-center gap-1.5">
          {hardPassed ? (
            <span className="text-2xs font-semibold text-emerald-600 dark:text-emerald-400">
              ✓ Hard constraints passed
            </span>
          ) : (
            <span className="text-2xs font-semibold text-red-600 dark:text-red-400">
              ✕ {it.metrics.hardViolations} hard violation{it.metrics.hardViolations === 1 ? "" : "s"}
            </span>
          )}
        </div>

        <div className="mt-2 space-y-1">
          <MetricBar
            label="Spatial quality"
            value={overall}
            title="Deterministic mean of the component metrics below (not an LLM score)"
          />
          {bars.map((b) => (
            <MetricBar key={b.label} label={b.label} value={b.value} invert={b.invert} />
          ))}
        </div>

        {it.judgeSkipped === "hard_violations" ? (
          <div className="mt-2 text-2xs leading-relaxed text-ink-400">
            Judge skipped: hard violations were repaired deterministically
            {it.deterministicRepair ? ` (removed ${it.deterministicRepair.removed})` : ""}.
          </div>
        ) : judge ? (
          <>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <DecisionChip judge={judge} />
              <Badge tone="grey">{judge.mode === "llm" ? "LLM" : "heuristic"}</Badge>
            </div>
            <div className="mt-1.5 flex items-center gap-2 text-2xs text-ink-400">
              <span>
                {issues.length > 0
                  ? `${issues.length} improvement opportunit${issues.length === 1 ? "y" : "ies"}`
                  : "no open issues"}
              </span>
              <button
                className="underline decoration-dotted hover:text-ink-200"
                onClick={() => setExpanded((x) => !x)}
              >
                {expanded ? "Hide review" : "View review"}
              </button>
            </div>
            {expanded && <ReviewDetails it={it} />}
          </>
        ) : (
          <div className="mt-2 text-2xs text-ink-500">no judge report for this iteration</div>
        )}
      </div>
    </Section>
  );
}
