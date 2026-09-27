import { useState } from "react";
import type { RunStage, Strategy } from "../../api/types";
import { isDebug } from "../../debug";
import { generateNow, setGenOptions, setOverlay } from "../../state/actions";
import { useApp } from "../../state/store";
import { Btn, Checkbox, Section, Select, Spinner, TextInput } from "../ui";

const STRATEGIES: { value: Strategy; label: string }[] = [
  { value: "balanced", label: "Сбалансированная" },
  { value: "maximum_trees", label: "Максимум деревьев" },
  { value: "maximum_canopy", label: "Максимум кроны" },
  { value: "street_greening", label: "Уличное озеленение" },
];

const STAGE_LABELS: Partial<Record<RunStage["stage"], string>> = {
  GENERATING_CANDIDATES: "Генерация кандидатов",
  OPTIMIZING: "Оптимизация",
  VALIDATING: "Проверка по нормам",
  BUILDING_CONSTRAINTS: "Построение ограничений",
  JUDGING: "Оценка качества",
  RERUNNING: "Повторный прогон",
};

/** Also rendered inside the wizard's Генерация step. */
export function StageChecklist() {
  const run = useApp((s) => s.run);
  if (!run) return null;
  return (
    <div className="mt-2 space-y-0.5 border-t border-ink-700 pt-2">
      {run.stages.map((st, i) => (
        <div key={i} className="flex items-center gap-1.5 text-2xs">
          <span className="w-3.5 text-center">
            {st.status === "done" && <span className="text-emerald-600 dark:text-emerald-400">✓</span>}
            {st.status === "active" && <Spinner className="h-2.5 w-2.5" />}
            {st.status === "error" && <span className="text-red-600 dark:text-red-400">✕</span>}
            {st.status === "pending" && <span className="text-ink-600">·</span>}
          </span>
          <span className={
            st.status === "active" ? "text-ink-100"
            : st.status === "done" ? "text-ink-300"
            : st.status === "error" ? "text-red-700 dark:text-red-300"
            : "text-ink-500"
          }>
            {STAGE_LABELS[st.stage] ?? st.stage}
            {st.detail ? <span className="text-ink-500"> — {st.detail}</span> : null}
          </span>
        </div>
      ))}
      {run.currentIteration > 0 && (
        <div className="pt-0.5 font-mono text-2xs text-ink-500">итерация {run.currentIteration}</div>
      )}
    </div>
  );
}

/** Seed, iterations, judge toggle and debug overlays live behind this fold. */
function AdvancedSettings() {
  const [open, setOpen] = useState(false);
  const opts = useApp((s) => s.genOptions);
  const capabilities = useApp((s) => s.capabilities);
  const overlays = useApp((s) => s.overlays);

  return (
    <div className="mt-2">
      <button
        data-testid="advanced-toggle"
        className="flex w-full items-center gap-1 text-2xs text-ink-400 hover:text-ink-200"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="inline-block w-3">{open ? "▾" : "▸"}</span>
        Дополнительные настройки
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5 border-l border-ink-700 pl-2.5">
          <div className="grid grid-cols-2 gap-x-2 gap-y-1.5">
            <label className="text-2xs text-ink-400">
              Seed (зерно генератора)
              <TextInput value={opts.seed} onChange={(v) => setGenOptions({ seed: v })} className="mt-0.5" />
            </label>
            <label className="text-2xs text-ink-400">
              Макс. итераций
              <TextInput value={opts.maxIterations} onChange={(v) => setGenOptions({ maxIterations: v })} className="mt-0.5" />
            </label>
          </div>
          <Checkbox
            checked={opts.useLlmJudge}
            onChange={(v) => setGenOptions({ useLlmJudge: v })}
            label={
              <span>
                LLM-судья
                {capabilities && (!capabilities.localLlm.enabled || !capabilities.localLlm.reachable) && (
                  <span className="ml-1 text-ink-500" title="Локальная LLM недоступна — будет использована эвристика">
                    (эвристика)
                  </span>
                )}
              </span>
            }
          />
          <div className="border-t border-ink-700/70 pt-1.5">
            <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Отладочные слои</div>
            <div className="space-y-1">
              <Checkbox
                checked={overlays.rejectedCandidates}
                onChange={(v) => setOverlay("rejectedCandidates", v)}
                label="Отклонённые кандидаты"
              />
              {isDebug && (
                <Checkbox
                  checked={overlays.chunkGrid}
                  onChange={(v) => setOverlay("chunkGrid", v)}
                  label="Сетка фрагментов"
                />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function GeneratePanel() {
  const doc = useApp((s) => s.document);
  const species = useApp((s) => s.species);
  const speciesError = useApp((s) => s.speciesError);
  const runActive = useApp((s) => s.runActive);
  const runError = useApp((s) => s.runError);
  const opts = useApp((s) => s.genOptions);

  if (!doc) return null;

  // null = "all species" until the user narrows the selection.
  const allIds = (species ?? []).map((sp) => sp.id);
  const selected = new Set(opts.speciesIds ?? allIds);
  const canGenerate = selected.size > 0 && !runActive;

  const toggleSpecies = (id: string) => {
    const next = new Set(opts.speciesIds ?? allIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setGenOptions({ speciesIds: [...next] });
  };

  return (
    <Section title="Генерация">
      <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Породы</div>
      {!species && !speciesError && (
        <div className="flex items-center gap-2 text-2xs text-ink-500"><Spinner /> загрузка пород…</div>
      )}
      {speciesError && <div className="text-2xs text-red-600 dark:text-red-400">породы: {speciesError}</div>}
      {species && (
        <div className="max-h-40 space-y-0.5 overflow-y-auto pr-1">
          {species.map((sp) => (
            <label
              key={sp.id}
              className="flex cursor-pointer items-center gap-1.5 py-px text-xs text-ink-200 hover:text-ink-100"
            >
              <input
                type="checkbox"
                checked={selected.has(sp.id)}
                onChange={() => toggleSpecies(sp.id)}
                className="h-3 w-3 accent-emerald-600"
              />
              <span className={sp.kind === "TREE" ? "text-emerald-600 dark:text-emerald-400" : "text-lime-600 dark:text-lime-400"}>
                {sp.kind === "TREE" ? "◉" : "◎"}
              </span>
              <span className="min-w-0 flex-1 truncate" title={sp.displayName}>{sp.displayNameRu || sp.displayName}</span>
              <span className="font-mono text-2xs text-ink-500">r{sp.parameters.crownRadiusM}</span>
              {sp.regulatoryStatus === "unknown" && (
                <span className="text-2xs text-amber-600 dark:text-amber-400" title="Нормативный статус породы не проверен">?</span>
              )}
            </label>
          ))}
        </div>
      )}

      <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1.5">
        <label className="text-2xs text-ink-400">
          Стратегия
          <Select
            value={opts.strategy}
            onChange={(v) => setGenOptions({ strategy: v as Strategy })}
            options={STRATEGIES}
            className="mt-0.5"
          />
        </label>
        <label className="text-2xs text-ink-400">
          Целевое количество
          <TextInput
            value={opts.targetCount}
            onChange={(v) => setGenOptions({ targetCount: v })}
            placeholder="Авто"
            className="mt-0.5"
          />
        </label>
      </div>

      <AdvancedSettings />

      <Btn
        variant="primary"
        className="mt-2.5 h-8 w-full text-[13px]"
        disabled={!canGenerate}
        onClick={() => generateNow()}
        title={selected.size === 0 ? "Выберите хотя бы одну породу" : undefined}
      >
        {runActive ? <><Spinner /> Генерация…</> : "Сгенерировать план посадок"}
      </Btn>

      {runError && (
        <div className="mt-2 border border-red-300 bg-red-50 p-1.5 text-2xs text-red-700 dark:border-red-800 dark:bg-red-950/50 dark:text-red-300">{runError}</div>
      )}
      <StageChecklist />
    </Section>
  );
}
