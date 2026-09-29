import { useState } from "react";
import type {
  AssessmentDimension, AssessmentKey, IterationSummary, JudgeIssueV2,
  JudgeParameterChange, JudgeReport,
} from "../../api/types";
import { parseRegulatoryVerdict } from "../../api/types";
import { useApp } from "../../state/store";
import { RegulatoryVerdictBlock } from "../regulatory";
import { Badge, MetricBar, Section } from "../ui";
import { DIMENSION_STATUS_LABELS, plural, SEVERITY_LABELS } from "../../labels";

const ASSESSMENT_LABELS: Record<AssessmentKey, string> = {
  coverage: "Покрытие",
  distribution: "Распределение",
  clustering: "Кластеризация",
  utilization: "Использование",
  robustnessToUncertainty: "Устойчивость",
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
        { label: "Покрытие", value: components.coverage },
        { label: "Распределение", value: components.distribution },
        { label: "Использование", value: components.utilizationBalance },
        { label: "Скученность", value: components.clusterPenalty, invert: true },
      ]
    : [
        { label: "Покрытие", value: it.metrics.coverageScore },
        { label: "Распределение", value: it.metrics.distributionScore },
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

/** Judge decision chip — optimization meaning ONLY, never legal compliance. */
function DecisionChip({ judge }: { judge: JudgeReport }) {
  if (judge.decision === "ACCEPT") {
    return <Badge tone="green" title="Судья не видит смысла в дополнительных итерациях оптимизации">Оптимизация: завершена</Badge>;
  }
  if (judge.decision === "RERUN") {
    return <Badge tone="amber" title="Судья рекомендует ещё одну итерацию оптимизации">Оптимизация: можно улучшить</Badge>;
  }
  return <Badge tone="violet" title="Судья воздержался — план стоит посмотреть человеку">Требуется ручная проверка</Badge>;
}

function DimensionRow({ name, dim }: { name: AssessmentKey; dim: AssessmentDimension }) {
  const tone = dim.status === "GOOD" ? "green" : dim.status === "IMPROVABLE" ? "amber" : "red";
  return (
    <div className="flex items-center gap-1.5 py-0.5">
      <span className="w-24 shrink-0 text-2xs text-ink-400">{ASSESSMENT_LABELS[name]}</span>
      <Badge tone={tone}>{DIMENSION_STATUS_LABELS[dim.status] ?? dim.status}</Badge>
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
          <div className="mb-0.5 text-2xs uppercase tracking-wider text-ink-500">Оценка по критериям</div>
          {(Object.entries(assessment) as [AssessmentKey, AssessmentDimension][]).map(([k, dim]) =>
            dim ? <DimensionRow key={k} name={k} dim={dim} /> : null,
          )}
        </div>
      )}

      {issues.length > 0 && (
        <div>
          <div className="mb-0.5 text-2xs uppercase tracking-wider text-ink-500">Замечания</div>
          <div className="space-y-1">
            {issues.map((iss, i) =>
              isV2Issue(iss) ? (
                <div key={i} className="flex items-start gap-1.5 text-2xs">
                  <Badge tone={iss.severity === "HIGH" ? "red" : iss.severity === "MEDIUM" ? "amber" : "grey"}>
                    {SEVERITY_LABELS[iss.severity] ?? iss.severity}
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
          <div className="mb-0.5 text-2xs uppercase tracking-wider text-ink-500">Изменения параметров</div>
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
    <Section title="Оценка качества">
      <div data-testid="quality-card">
        {/* Deterministic regulatory verdict — separate from the judge below. */}
        <RegulatoryVerdictBlock verdict={parseRegulatoryVerdict(it.regulatory)} />

        <div className="mt-2 flex items-center gap-1.5">
          {hardPassed ? (
            <span className="text-2xs font-semibold text-emerald-600 dark:text-emerald-400">
              ✓ Жёсткие ограничения соблюдены
            </span>
          ) : (
            <span className="text-2xs font-semibold text-red-600 dark:text-red-400">
              ✕ {it.metrics.hardViolations} {plural(it.metrics.hardViolations, "жёсткое нарушение", "жёстких нарушения", "жёстких нарушений")}
            </span>
          )}
        </div>

        <div className="mt-2 space-y-1">
          <MetricBar
            label="Общая оценка"
            value={overall}
            title="Качество размещения: среднее детерминированных метрик ниже (не оценка LLM)"
          />
          {bars.map((b) => (
            <MetricBar key={b.label} label={b.label} value={b.value} invert={b.invert} />
          ))}
        </div>

        {it.judgeSkipped === "hard_violations" ? (
          <div className="mt-2 text-2xs leading-relaxed text-ink-400">
            Судья пропущен: жёсткие нарушения исправлены детерминированно
            {it.deterministicRepair ? ` (удалено посадок: ${it.deterministicRepair.removed})` : ""}.
          </div>
        ) : judge ? (
          <>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <DecisionChip judge={judge} />
              <Badge tone="grey">{judge.mode === "llm" ? "LLM" : "эвристика"}</Badge>
            </div>
            <div className="mt-0.5 text-2xs italic text-ink-500">
              оценка качества размещения, НЕ юридическое соответствие
            </div>
            <div className="mt-1.5 flex items-center gap-2 text-2xs text-ink-400">
              <span>
                {issues.length > 0
                  ? `${issues.length} ${plural(issues.length, "предложение", "предложения", "предложений")} по улучшению`
                  : "замечаний нет"}
              </span>
              <button
                className="underline decoration-dotted hover:text-ink-200"
                onClick={() => setExpanded((x) => !x)}
              >
                {expanded ? "Скрыть разбор" : "Показать разбор"}
              </button>
            </div>
            {expanded && <ReviewDetails it={it} />}
          </>
        ) : (
          <div className="mt-2 text-2xs text-ink-500">для этой итерации нет отчёта судьи</div>
        )}
      </div>
    </Section>
  );
}
