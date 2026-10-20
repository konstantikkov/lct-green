import { useState } from "react";
import { normalizeCheckResult } from "../api/types";
import type { ExclusionZone, Explanation, Placement } from "../api/types";
import { isDebug } from "../debug";
import { select } from "../state/actions";
import { useApp, type Selection } from "../state/store";
import { alsoCitedText, alsoCitedTitle, CheckResultBadge } from "./regulatory";
import { Badge, KV, Spinner } from "./ui";
import { categoryLabel, KIND_LABELS, plural, sourceStatusLabel } from "../labels";

const DISTANCE_LABELS: Record<string, string> = {
  building: "Здание",
  road: "Дорога",
  sidewalk: "Тротуар",
  utility: "Инженерные сети",
  boundary: "Граница участка",
  existing_tree: "Существующее дерево",
  nearest_planting: "Ближайшая посадка",
};

function SourceBadge({ source }: { source: "verified" | "assumption" }) {
  return source === "assumption"
    ? <Badge tone="amber" title="Расстояние — непроверенное допущение (демо-значение)">ДОПУЩЕНИЕ</Badge>
    : <Badge tone="green" title="Подтверждено нормативным источником">ПОДТВЕРЖДЕНО</Badge>;
}

/**
 * Russian one-paragraph summary built from the structured checks, not from the
 * backend's free-text `summary` (which is English). Same facts, and it stays
 * correct whatever language the API speaks.
 */
function russianSummary(e: Explanation): string {
  const results = e.checks.map((c) => normalizeCheckResult(c.result));
  const enforced = results.filter((r) => r !== "NOT_APPLICABLE").length;
  const failed = results.filter((r) => r === "FAIL").length;
  const unverified = results.filter((r) => r === "UNVERIFIED").length;
  const [x, y] = e.position;
  let s = `Положение (${x.toFixed(1)}; ${y.toFixed(1)}) м. `
    + `Проверено ${enforced} ${plural(enforced, "правило", "правила", "правил")}: `;
  if (failed > 0) {
    s += `${failed} ${plural(failed, "нарушение", "нарушения", "нарушений")}.`;
  } else if (unverified > 0) {
    s += `нарушений нет, но ${unverified} ${plural(unverified, "проверка не верифицирована", "проверки не верифицированы", "проверок не верифицировано")}`
      + " — соответствие нормам не установлено.";
  } else {
    s += "все выполнены по подтверждённым значениям.";
  }
  if (e.checks.some((c) => /крона/i.test(c.reason ?? ""))) {
    s += " Крона шире 5 м: по примечанию 1 к таблице 9.1 СП 42.13330 отступы нужно увеличить"
      + " (на сколько — норма не указывает), требуется ручная проверка.";
  }
  return s;
}

function ChecksList({ explanation }: { explanation: Explanation }) {
  return (
    <div className="space-y-1.5">
      {explanation.checks.map((c, i) => {
        const r = normalizeCheckResult(c.result); // handles legacy "passed"/"failed"/"n/a" too
        return (
        <div key={i} className="border border-ink-700 bg-ink-900/60 p-1.5">
          <div className="flex items-center gap-1.5">
            <span className={
              r === "PASS"
                ? "text-emerald-600 dark:text-emerald-400"
                : r === "FAIL"
                  ? "text-red-600 dark:text-red-400"
                  : "text-ink-500"
            }>
              {r === "PASS" ? "✓" : r === "FAIL" ? "✕" : "–"}
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-200" title={c.ruleId}>
              {c.ruleId}
            </span>
            <CheckResultBadge result={c.result} />
            <SourceBadge source={c.effectiveSource} />
          </div>
          <div className="mt-1 flex items-center gap-2 font-mono text-2xs text-ink-300">
            <span className="text-ink-500">{categoryLabel(c.obstacleCategory)}</span>
            {c.actualM != null && c.requiredM != null ? (
              <span>
                {c.actualM.toFixed(2)} м <span className="text-ink-500">{c.comparator}</span>{" "}
                {c.requiredM.toFixed(2)} м
              </span>
            ) : (
              <span className="text-ink-500">нет таких объектов</span>
            )}
          </div>
          <div className="mt-0.5 truncate text-2xs text-ink-500" title={c.source.title}>
            {c.source.document}
            {c.source.clause ? ` · ${c.source.clause}` : ""} · {sourceStatusLabel(c.source.status)}
          </div>
          {c.source.alsoCited && c.source.alsoCited.length > 0 && (
            <div className="mt-0.5 text-2xs leading-snug text-ink-500" title={alsoCitedTitle(c.source.alsoCited)}>
              также: {alsoCitedText(c.source.alsoCited)}
            </div>
          )}
          {c.reason && (
            <div className="mt-0.5 text-2xs leading-snug text-amber-700 dark:text-amber-300/90">{c.reason}</div>
          )}
        </div>
        );
      })}
    </div>
  );
}

function PlacementInspector({ placement }: { placement: Placement }) {
  const species = useApp((s) => s.species);
  const iteration = useApp((s) => s.activeIteration);
  const explanation = useApp((s) => s.explanation);
  const loading = useApp((s) => s.explanationLoading);
  const error = useApp((s) => s.explanationError);

  const sp = species?.find((x) => x.id === placement.speciesId);
  const distances = Object.entries(placement.distances);

  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-ink-100">
            {sp?.displayNameRu || sp?.displayName || placement.speciesId}
          </span>
          <Badge tone={placement.kind === "TREE" ? "green" : "blue"}>{KIND_LABELS[placement.kind] ?? placement.kind}</Badge>
          {placement.manuallyMoved && (
            <Badge tone="amber" title="Позиция изменена вручную; итерация пересчитана детерминированно">
              Перемещено вручную
            </Badge>
          )}
        </div>
        <KV k="id" v={placement.id} />
        <KV
          k="положение"
          v={`${placement.position[0].toFixed(2)}, ${placement.position[1].toFixed(2)} м`}
        />
        <KV k="радиус кроны" v={`${placement.crownRadiusM.toFixed(2)} м`} />
        <KV k="итерация" v={iteration ? `#${iteration.index}` : "—"} />
        <KV k="оценка" v={placement.score.toFixed(3)} />
      </div>

      {distances.length > 0 && (
        <div>
          <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Расстояния</div>
          <table className="w-full">
            <tbody>
              {distances.map(([key, val]) => (
                <tr key={key} className="border-b border-ink-700/50 last:border-0">
                  <td className="py-0.5 text-2xs text-ink-400">{DISTANCE_LABELS[key] ?? key}</td>
                  <td className="py-0.5 text-right font-mono text-2xs text-ink-100">
                    {val != null ? `${val.toFixed(2)} м` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div>
        <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Проверки по нормам</div>
        {loading && (
          <div className="flex items-center gap-2 text-2xs text-ink-500"><Spinner /> загрузка обоснования…</div>
        )}
        {error && <div className="text-2xs text-red-600 dark:text-red-400">{error}</div>}
        {explanation && (
          <>
            <p className="mb-2 text-2xs leading-relaxed text-ink-300">{russianSummary(explanation)}</p>
            {explanation.llmNarrative && (
              <p className="mb-2 border-l-2 border-ink-600 pl-2 text-2xs italic leading-relaxed text-ink-400">
                {explanation.llmNarrative}
              </p>
            )}
            <ChecksList explanation={explanation} />
          </>
        )}
      </div>
    </div>
  );
}

function ZoneInspector({ zone }: { zone: ExclusionZone }) {
  const rules = useApp((s) => s.rules);
  const rule = rules?.find((r) => r.id === zone.ruleId);
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 flex items-center gap-2">
          <span className="text-xs font-semibold text-ink-100">Запретная зона</span>
          <SourceBadge source={zone.effectiveSource} />
        </div>
        <KV k="id" v={zone.id} />
        <KV k="правило" v={zone.ruleId} />
        <KV k="препятствие" v={categoryLabel(zone.obstacleCategory)} mono={false} />
        <KV k="исходный слой" v={zone.sourceLayer} mono={false} />
        <KV k="отступ" v={`${zone.distanceM.toFixed(2)} м`} />
      </div>
      <div>
        <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Нормативный источник</div>
        <div className="border border-ink-700 bg-ink-900/60 p-1.5 text-2xs">
          <div className="text-ink-200">{zone.source.document}</div>
          {zone.source.clause && <div className="mt-0.5 text-ink-400">пункт: {zone.source.clause}</div>}
          <div className="mt-0.5 text-ink-500">статус: {sourceStatusLabel(zone.source.status)}</div>
          {rule && (
            <>
              <div className="mt-1 text-ink-400">{rule.source.title}</div>
              {rule.assumption && zone.effectiveSource === "assumption" && (
                <div className="mt-1 border-t border-ink-700 pt-1 text-amber-700 dark:text-amber-300/90">
                  {rule.assumption.note}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** Debug-only tab: pretty-printed raw JSON of the selected object. */
function RawInspector({ selection }: { selection: NonNullable<Selection> }) {
  const explanation = useApp((s) => s.explanation);
  const raw = selection.type === "placement"
    ? { placement: selection.placement, explanation }
    : { zone: selection.zone };
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-2xs leading-relaxed text-ink-300">
      {JSON.stringify(raw, null, 2)}
    </pre>
  );
}

function SelectionBody({ selection }: { selection: NonNullable<Selection> }) {
  const [tab, setTab] = useState<"details" | "raw">("details");
  return (
    <>
      {isDebug && (
        <div className="mb-2 flex gap-1">
          {(["details", "raw"] as const).map((t) => (
            <button
              key={t}
              className={`rounded-sm border px-1.5 py-0.5 text-2xs uppercase tracking-wider ${
                tab === t
                  ? "border-ink-500 bg-ink-800 text-ink-100"
                  : "border-transparent text-ink-500 hover:text-ink-300"
              }`}
              onClick={() => setTab(t)}
            >
              {t === "details" ? "Подробно" : "JSON"}
            </button>
          ))}
        </div>
      )}
      {tab === "raw" && isDebug
        ? <RawInspector selection={selection} />
        : selection.type === "placement"
          ? <PlacementInspector placement={selection.placement} />
          : <ZoneInspector zone={selection.zone} />}
    </>
  );
}

/** Selection details only — reused by the mobile bottom sheet's Inspector tab. */
export function InspectorContent() {
  const selection = useApp((s) => s.selection);
  if (!selection) {
    return (
      <div className="p-3 text-xs text-ink-500">
        Нажмите на дерево или запретную зону на плане, чтобы посмотреть подробности.
      </div>
    );
  }
  return (
    <div className="p-2">
      <SelectionBody selection={selection} />
    </div>
  );
}

export function Inspector() {
  const selection = useApp((s) => s.selection);
  if (!selection) return null;

  return (
    <aside data-sidebar className="relative hidden min-h-0 max-h-[32vh] w-full shrink-0 flex-col border-t border-ink-600 bg-ink-900 md:flex" data-testid="inspector">
      <div className="flex h-8 shrink-0 items-center justify-between border-b border-ink-600 px-2">
        <span className="text-2xs font-semibold uppercase tracking-wider text-ink-300">Инспектор</span>
        <button
          className="px-1 text-ink-500 hover:text-ink-100"
          onClick={() => select(null)}
          aria-label="Закрыть инспектор"
          title="Закрыть"
        >
          ✕
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <SelectionBody selection={selection} />
      </div>
    </aside>
  );
}
