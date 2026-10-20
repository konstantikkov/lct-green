import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import {
  ALL_CATEGORIES, parseRegulatoryVerdict, type Category, type IterationSummary,
  type JudgeParameterChange, type JudgeReport, type LayerInfo, type RuleCandidate,
  type Strategy,
} from "../api/types";
import {
  buildConstraints, generateNow, loadAudit, openAudit, overrideLayerCategory,
  setGenOptions, setNav, setWorkspaceTab,
} from "../state/actions";
import {
  closeWizard, setWizardStep, wizardApproveCandidate, wizardEditCandidate,
  wizardParseText, wizardRejectCandidate, wizardUploadConditionDoc,
} from "../state/wizard";
import { getState, useApp, type WizardStep } from "../state/store";
import { RegulatoryVerdictBlock, RuleRoleBadge, ruleConditionNote } from "./regulatory";
import { Badge, Btn, MetricBar, Select, Spinner, TextInput } from "./ui";
import { StageChecklist } from "./panels/GeneratePanel";
import { categoryLabel } from "../labels";

/**
 * Step-by-step wizard after a fresh DXF/DWG upload:
 *   1 Ограничения → 2 Условия → 3 Генерация → 4 Итоги.
 * Opens automatically after a successful upload (never for existing projects);
 * reachable later via the «Мастер» button in the workspace tab strip.
 * Desktop: centered max-w-2xl modal; mobile: full-screen sheet.
 */

const STEPS: { id: WizardStep; label: string }[] = [
  { id: 1, label: "Ограничения" },
  { id: 2, label: "Условия" },
  { id: 3, label: "Генерация" },
  { id: 4, label: "Итоги" },
];

function Stepper({ step }: { step: WizardStep }) {
  return (
    <ol className="flex min-w-0 items-center gap-1 overflow-x-auto sm:gap-2">
      {STEPS.map((s, i) => {
        const state = s.id === step ? "active" : s.id < step ? "done" : "todo";
        return (
          <li key={s.id} className="flex shrink-0 items-center gap-1 sm:gap-2">
            {i > 0 && <span className="h-px w-3 bg-ink-600 sm:w-5" />}
            <button
              className="flex items-center gap-1.5 disabled:cursor-default"
              disabled={s.id >= step}
              onClick={() => setWizardStep(s.id)}
              title={s.id < step ? `Вернуться к шагу «${s.label}»` : undefined}
            >
              <span
                className={`flex h-[18px] w-[18px] items-center justify-center rounded-full text-2xs font-semibold ${
                  state === "active"
                    ? "bg-emerald-600 text-white"
                    : state === "done"
                      ? "bg-emerald-200 text-emerald-900 dark:bg-emerald-900 dark:text-emerald-200"
                      : "bg-ink-700 text-ink-400"
                }`}
              >
                {state === "done" ? "✓" : s.id}
              </span>
              <span
                className={`whitespace-nowrap text-2xs ${
                  state === "active" ? "font-semibold text-ink-100" : "text-ink-400"
                } ${s.id < step ? "hover:text-ink-200" : ""}`}
              >
                {s.label}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

// === Step 1: Ограничения =======================================================

function UncertainLayerRow({ layer }: { layer: LayerInfo }) {
  return (
    <div className="flex items-center gap-2 py-1">
      <span className="text-2xs text-amber-600 dark:text-amber-400">⚠</span>
      <span className="min-w-0 flex-1 truncate text-xs text-ink-200" title={layer.name}>
        {layer.name}
      </span>
      <span className="shrink-0 font-mono text-2xs text-ink-500">
        {Math.round(layer.classification.confidence * 100)}%
      </span>
      <Select
        className="!h-6 !w-44 shrink-0 text-2xs"
        value={layer.classification.category}
        onChange={(v) => void overrideLayerCategory(layer.name, v as Category)}
        options={ALL_CATEGORIES.map((c) => ({ value: c, label: categoryLabel(c) }))}
      />
    </div>
  );
}

function Step1Constraints() {
  const doc = useApp((s) => s.document);
  const constraints = useApp((s) => s.constraints);
  const constraintsLoading = useApp((s) => s.constraintsLoading);
  const constraintsError = useApp((s) => s.constraintsError);
  const rules = useApp((s) => s.rules);
  if (!doc) return null;

  const uncertain = doc.layers.filter((l) => l.classification.requiresReview);
  const activeRules = (rules ?? []).filter((r) => r.enabled && r.effectiveSource !== "inactive");
  const verified = activeRules.filter((r) => r.effectiveSource === "verified").length;
  const assumed = activeRules.filter((r) => r.effectiveSource === "assumption").length;

  return (
    <div className="space-y-3" data-testid="wizard-step-1">
      {/* Interpretation summary */}
      <section>
        <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">
          Интерпретация чертежа
        </div>
        <div className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5">
          <div className="truncate text-xs font-semibold text-ink-100" title={doc.fileName}>
            {doc.fileName}
          </div>
          <div className="mt-0.5 font-mono text-2xs text-ink-400">
            {doc.entityCount.toLocaleString()} объектов · {doc.layerCount} слоёв · единицы {doc.units}
            {" · "}
            {uncertain.length > 0 ? (
              <span className="text-amber-600 dark:text-amber-400">⚠ {uncertain.length} неуверенных слоёв</span>
            ) : (
              <span className="text-emerald-600 dark:text-emerald-400">✓ классификация уверенная</span>
            )}
          </div>
          {uncertain.length > 0 && (
            <div className="mt-2 border-t border-ink-700 pt-1.5">
              <div className="mb-0.5 text-2xs text-ink-400">
                Проверьте категории неуверенно распознанных слоёв (влияют на запретные зоны):
              </div>
              <div className="max-h-36 overflow-y-auto pr-1">
                {uncertain.map((l) => <UncertainLayerRow key={l.name} layer={l} />)}
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Constraints auto-build status */}
      <section>
        <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">
          Запретные зоны
        </div>
        <div className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5 text-2xs">
          {constraintsLoading && (
            <div className="flex items-center gap-2 text-ink-300">
              <Spinner className="h-2.5 w-2.5" /> Зоны ограничений строятся автоматически…
            </div>
          )}
          {!constraintsLoading && constraints && (
            <div className="text-ink-300">
              <span className="text-emerald-600 dark:text-emerald-400">✓</span>{" "}
              Построено: <span className="font-mono">{constraints.exclusionZones.length}</span> запретных зон,
              разрешённая площадь{" "}
              <span className="font-mono">
                {constraints.allowedArea.areaM2.toLocaleString(undefined, { maximumFractionDigits: 0 })} м²
              </span>
              . Зоны видны на холсте под мастером.
            </div>
          )}
          {!constraintsLoading && !constraints && constraintsError && (
            <div className="text-red-600 dark:text-red-400">
              Не удалось построить зоны: {constraintsError}{" "}
              <button className="underline decoration-dotted" onClick={() => void buildConstraints()}>
                Повторить
              </button>
            </div>
          )}
          {!constraintsLoading && !constraints && !constraintsError && (
            <div className="text-ink-400">Зоны ещё не построены.</div>
          )}
        </div>
      </section>

      {/* Active rules */}
      <section>
        <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">
          Действующие правила
        </div>
        <div className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="grey">{activeRules.length} активных</Badge>
            <Badge tone="green" title="Подтверждены нормативным источником">{verified} подтверждённых</Badge>
            <Badge tone="amber" title="Применяются как непроверенные допущения">{assumed} по допущениям</Badge>
          </div>
          {activeRules.length > 0 && (
            <div className="mt-1.5 max-h-32 overflow-y-auto border-t border-ink-700 pt-1.5 pr-1">
              {activeRules.map((r) => (
                <div key={r.id} className="flex items-center gap-2 py-0.5">
                  <span className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-300" title={r.id}>
                    {r.id}
                  </span>
                  {ruleConditionNote(r) && (
                    <span className="shrink-0 text-2xs text-ink-500">{ruleConditionNote(r)}</span>
                  )}
                  <RuleRoleBadge role={r.role} />
                  <span className="shrink-0 font-mono text-2xs text-ink-400">
                    {r.effectiveDistanceM != null ? `${r.effectiveDistanceM} м` : "—"}
                  </span>
                  {r.effectiveSource === "assumption"
                    ? <Badge tone="amber">ДОПУЩЕНИЕ</Badge>
                    : <Badge tone="green">ПОДТВЕРЖДЕНО</Badge>}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

// === Step 2: Условия ============================================================

const STATUS_LABEL: Record<RuleCandidate["status"], string> = {
  EXTRACTED: "Извлечено",
  NEEDS_REVIEW: "Требует проверки",
  VERIFIED: "Проверено",
  ACTIVE: "Активно",
  DEPRECATED: "Деактивировано",
  REJECTED: "Отклонено",
};

const STATUS_TONE: Record<RuleCandidate["status"], "grey" | "green" | "amber" | "red"> = {
  EXTRACTED: "grey",
  NEEDS_REVIEW: "amber",
  VERIFIED: "green",
  ACTIVE: "green",
  DEPRECATED: "grey",
  REJECTED: "red",
};

function VerdictBadge({ c }: { c: RuleCandidate }) {
  const v = c.verification;
  if (!v || !v.verdict) return <Badge tone="grey">не верифицировано</Badge>;
  const tone = v.verdict === "SUPPORTED" ? "green" : v.verdict === "UNSUPPORTED" ? "red" : "amber";
  return (
    <Badge tone={tone} title={v.notes ?? undefined}>
      {v.verdict}{v.numericLiteralFound ? "" : " · число не найдено"}
    </Badge>
  );
}

/** «На основании»: quoted mono excerpt, truncated with expand. */
function SourceExcerpt({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const LIMIT = 180;
  const long = text.length > LIMIT;
  const shown = expanded || !long ? text : `${text.slice(0, LIMIT).trimEnd()}…`;
  return (
    <div className="mt-1.5">
      <div className="text-2xs text-ink-500">На основании:</div>
      <blockquote className="mt-0.5 whitespace-pre-wrap border-l-2 border-amber-400/70 bg-amber-50/60 px-2 py-1 font-mono text-2xs leading-relaxed text-ink-300 dark:bg-amber-950/20">
        “{shown}”
      </blockquote>
      {long && (
        <button
          className="mt-0.5 text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
          onClick={() => setExpanded((x) => !x)}
        >
          {expanded ? "свернуть" : "развернуть"}
        </button>
      )}
    </div>
  );
}

function ConditionEditForm({ c, onClose }: { c: RuleCandidate; onClose: () => void }) {
  const busyId = useApp((s) => s.wizardCandidateBusyId);
  const [value, setValue] = useState(c.constraint.value != null ? String(c.constraint.value) : "");
  const [unit, setUnit] = useState(c.constraint.unit ?? "");
  const [objectTypes, setObjectTypes] = useState(c.applicability.objectTypes.join(", "));

  const submit = async () => {
    const num = value.trim() === "" ? null : Number(value);
    await wizardEditCandidate(c.id, {
      value: num != null && Number.isFinite(num) ? num : null,
      unit: unit.trim() === "" ? null : unit.trim(),
      objectTypes: objectTypes.split(",").map((s) => s.trim()).filter(Boolean),
    });
    onClose();
  };

  return (
    <div className="mt-2 space-y-1.5 rounded-sm border border-ink-600 bg-ink-850 p-2">
      <div className="grid grid-cols-2 gap-2">
        <label className="text-2xs text-ink-400">
          Значение
          <TextInput value={value} onChange={setValue} className="mt-0.5" />
        </label>
        <label className="text-2xs text-ink-400">
          Ед. изм.
          <TextInput value={unit} onChange={setUnit} placeholder="м" className="mt-0.5" />
        </label>
      </div>
      <label className="block text-2xs text-ink-400">
        Типы объектов (через запятую)
        <TextInput value={objectTypes} onChange={setObjectTypes} className="mt-0.5" />
      </label>
      <div className="flex gap-1.5 pt-0.5">
        <Btn variant="primary" className="h-6 text-2xs" disabled={busyId === c.id} onClick={() => void submit()}>
          Сохранить
        </Btn>
        <Btn variant="ghost" className="h-6 text-2xs" onClick={onClose}>Отмена</Btn>
      </div>
    </div>
  );
}

function ConditionCard({ c }: { c: RuleCandidate }) {
  const busyId = useApp((s) => s.wizardCandidateBusyId);
  const error = useApp((s) => s.wizardCandidateErrors[c.id]);
  const [editing, setEditing] = useState(false);
  const busy = busyId === c.id;
  const done = c.status === "ACTIVE";
  const rejected = c.status === "REJECTED";

  return (
    <div
      data-testid="condition-card"
      className={`rounded-sm border p-2.5 ${
        done
          ? "border-emerald-300 bg-emerald-50/50 dark:border-emerald-800 dark:bg-emerald-950/20"
          : rejected
            ? "border-ink-700 bg-ink-950/40 opacity-60"
            : "border-ink-600 bg-ink-950/40"
      }`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="min-w-0 text-xs text-ink-100">
          <span className="font-semibold">{c.constraint.subject}</span>
          <span className="text-ink-500"> → </span>
          <span className="font-semibold">{c.constraint.referenceObject}</span>
        </span>
        <span className="font-mono text-xs text-ink-200">
          {c.constraint.operator} {c.constraint.value ?? "—"} {c.constraint.unit ?? ""}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <span className="font-mono text-2xs text-ink-400">{Math.round(c.confidence * 100)}%</span>
          <VerdictBadge c={c} />
          <Badge tone={STATUS_TONE[c.status]}>{STATUS_LABEL[c.status]}</Badge>
        </span>
      </div>

      <SourceExcerpt text={c.source.sourceText} />

      {error && (
        <div className="mt-1.5 border border-red-300 bg-red-50 p-1.5 text-2xs leading-relaxed text-red-700 dark:border-red-800 dark:bg-red-950/50 dark:text-red-300">
          {error}
        </div>
      )}

      {!rejected && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {done ? (
            <span className="text-2xs font-semibold text-emerald-700 dark:text-emerald-300">
              ✓ Активировано — учитывается при генерации и аудите
            </span>
          ) : (
            <>
              <Btn
                variant="primary"
                className="h-6 text-2xs"
                disabled={busy}
                data-testid="approve-condition"
                onClick={() => void wizardApproveCandidate(c.id)}
                title="Верифицировать, подтвердить и активировать условие"
              >
                {busy ? <Spinner className="h-2.5 w-2.5" /> : null} Подтвердить
              </Btn>
              <Btn className="h-6 text-2xs" disabled={busy} onClick={() => setEditing((x) => !x)}>
                Редактировать
              </Btn>
              <Btn
                className="h-6 border-red-300 text-2xs text-red-700 dark:border-red-800 dark:text-red-300"
                disabled={busy}
                onClick={() => void wizardRejectCandidate(c.id)}
              >
                Отклонить
              </Btn>
            </>
          )}
        </div>
      )}

      {editing && !done && !rejected && <ConditionEditForm c={c} onClose={() => setEditing(false)} />}
    </div>
  );
}

/** «2 условия активированы» — russian plural. */
function ruActivated(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} условие активировано`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} условия активированы`;
  return `${n} условий активировано`;
}

function Step2Conditions() {
  const candidates = useApp((s) => s.wizardCandidates);
  const notice = useApp((s) => s.wizardNotice);
  const busy = useApp((s) => s.wizardBusy);
  const busyLabel = useApp((s) => s.wizardBusyLabel);
  const [text, setText] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const activated = candidates.filter((c) => c.status === "ACTIVE").length;

  return (
    <div className="space-y-3" data-testid="wizard-step-2">
      <p className="text-2xs leading-relaxed text-ink-400">
        Добавьте собственные ограничения — текстом или нормативным документом.
        Извлечённые условия нужно подтвердить; активированные попадут в реестр правил.
        Шаг необязателен — его можно пропустить кнопкой «Далее».
      </p>

      <div className="grid gap-3 md:grid-cols-2">
        {/* (a) free text */}
        <section className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">Текстом</div>
          <textarea
            data-testid="condition-text"
            className="h-24 w-full resize-none rounded-sm border border-ink-600 bg-ink-900 p-2 text-xs text-ink-100 placeholder-ink-500 outline-none focus:border-ink-400"
            placeholder="Опишите условие текстом… например: «Расстояние от дерева до газопровода — не менее 2 метров»"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <Btn
            variant="primary"
            className="mt-1.5 w-full"
            disabled={busy || text.trim() === ""}
            data-testid="parse-text-condition"
            onClick={() => {
              void wizardParseText(text).then((ok) => {
                if (ok) setText("");
              });
            }}
          >
            {busy ? <><Spinner /> {busyLabel ?? "Разбор…"}</> : "Разобрать условие"}
          </Btn>
        </section>

        {/* (b) PDF/DOCX */}
        <section
          className="flex flex-col rounded-sm border border-ink-600 bg-ink-950/40 p-2.5"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files?.[0];
            if (f) void wizardUploadConditionDoc(f);
          }}
        >
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">Документом</div>
          <div className="flex flex-1 flex-col items-center justify-center gap-1.5 rounded-sm border border-dashed border-ink-500 p-3">
            <div className="text-2xs text-ink-400">Перетащите PDF / DOCX сюда</div>
            <Btn disabled={busy} onClick={() => fileRef.current?.click()}>
              {busy ? <><Spinner /> {busyLabel ?? "Обработка…"}</> : "Выбрать файл…"}
            </Btn>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void wizardUploadConditionDoc(f);
                e.target.value = "";
              }}
            />
          </div>
        </section>
      </div>

      {notice && (
        <div className="rounded-sm border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-2xs leading-relaxed text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200">
          {notice}
        </div>
      )}

      {candidates.length > 0 && (
        <section className="space-y-1.5">
          <div className="flex items-baseline justify-between">
            <div className="text-2xs font-semibold uppercase tracking-wider text-ink-400">
              Извлечённые условия ({candidates.length})
            </div>
            {activated > 0 && (
              <div className="text-2xs font-semibold text-emerald-700 dark:text-emerald-300">
                {ruActivated(activated)} — будут учтены при генерации и аудите
              </div>
            )}
          </div>
          {candidates.map((c) => <ConditionCard key={c.id} c={c} />)}
        </section>
      )}
    </div>
  );
}

// === Step 3: Генерация ==========================================================

const STRATEGIES: { value: Strategy; label: string }[] = [
  { value: "balanced", label: "Сбалансированная" },
  { value: "maximum_trees", label: "Максимум деревьев" },
  { value: "maximum_canopy", label: "Максимум кроны" },
  { value: "street_greening", label: "Уличное озеленение" },
];

function ProgressBar() {
  const run = useApp((s) => s.run);
  if (!run) return null;
  const doneShare = run.stages.length > 0
    ? run.stages.filter((st) => st.status === "done").length / run.stages.length
    : 0;
  const pct = Math.max(0, Math.min(100, Math.round(run.progress ?? doneShare * 100)));
  return (
    <div data-testid="wizard-progress" className="mt-2.5">
      <div className="flex items-baseline justify-between">
        <span className="text-2xs text-ink-400">
          {run.state === "COMPLETED" ? "Готово" : run.state === "FAILED" ? "Ошибка" : "Генерация…"}
          {run.currentIteration > 0 ? ` · итерация ${run.currentIteration}` : ""}
        </span>
        <span className="font-mono text-2xs text-ink-200">{pct}%</span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-ink-700">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            run.state === "FAILED" ? "bg-red-500/80" : "bg-emerald-500/90"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function Step3Generate() {
  const species = useApp((s) => s.species);
  const opts = useApp((s) => s.genOptions);
  const runActive = useApp((s) => s.runActive);
  const runError = useApp((s) => s.runError);
  const hasResult = useApp((s) => s.iterations.length > 0);

  const allIds = (species ?? []).map((sp) => sp.id);
  const selected = new Set(opts.speciesIds ?? allIds);

  const toggleSpecies = (id: string) => {
    const next = new Set(opts.speciesIds ?? allIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setGenOptions({ speciesIds: [...next] });
  };

  return (
    <div className="space-y-3" data-testid="wizard-step-3">
      <div className="grid gap-3 md:grid-cols-2">
        <section className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">Породы</div>
          {!species && <div className="flex items-center gap-2 text-2xs text-ink-500"><Spinner /> загрузка…</div>}
          {species && (
            <div className="max-h-36 space-y-0.5 overflow-y-auto pr-1">
              {species.map((sp) => (
                <label key={sp.id} className="flex cursor-pointer items-center gap-1.5 py-px text-xs text-ink-200 hover:text-ink-100">
                  <input
                    type="checkbox"
                    checked={selected.has(sp.id)}
                    onChange={() => toggleSpecies(sp.id)}
                    className="h-3 w-3 accent-emerald-600"
                  />
                  <span className={sp.kind === "TREE" ? "text-emerald-600 dark:text-emerald-400" : "text-lime-600 dark:text-lime-400"}>
                    {sp.kind === "TREE" ? "◉" : "◎"}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{sp.displayNameRu}</span>
                </label>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">Параметры</div>
          <label className="block text-2xs text-ink-400">
            Стратегия
            <Select
              value={opts.strategy}
              onChange={(v) => setGenOptions({ strategy: v as Strategy })}
              options={STRATEGIES}
              className="mt-0.5"
            />
          </label>
          <label className="mt-1.5 block text-2xs text-ink-400">
            Целевое количество
            <TextInput
              value={opts.targetCount}
              onChange={(v) => setGenOptions({ targetCount: v })}
              placeholder="Авто"
              className="mt-0.5"
            />
          </label>
        </section>
      </div>

      <Btn
        variant="primary"
        className="h-8 w-full text-[13px]"
        disabled={runActive || selected.size === 0}
        data-testid="wizard-generate"
        onClick={() => generateNow()}
        title={selected.size === 0 ? "Выберите хотя бы одну породу" : undefined}
      >
        {runActive ? <><Spinner /> Генерация…</> : hasResult ? "Сгенерировать заново" : "Сгенерировать"}
      </Btn>

      <ProgressBar />

      {runError && (
        <div className="border border-red-300 bg-red-50 p-1.5 text-2xs text-red-700 dark:border-red-800 dark:bg-red-950/50 dark:text-red-300">
          {runError}
        </div>
      )}

      {/* Stage checklist + iteration counter (shared with the workspace panel). */}
      <StageChecklist />
    </div>
  );
}

// === Step 4: Итоги ==============================================================

function judgeChanges(judge: JudgeReport | null): JudgeParameterChange[] {
  if (!judge) return [];
  if (judge.parameterChanges && judge.parameterChanges.length > 0) return judge.parameterChanges;
  return Object.entries(judge.recommendedParameterChanges ?? {})
    .filter(([, v]) => v != null)
    .map(([parameter, proposedValue]) => ({
      parameter, currentValue: NaN, proposedValue: proposedValue as number, reason: "", evidenceIds: [],
    }));
}

function pickIteration(iterations: IterationSummary[]): IterationSummary | null {
  return iterations.find((i) => i.selected) ?? iterations[iterations.length - 1] ?? null;
}

function ComplianceReport() {
  const audit = useApp((s) => s.audit);
  const status = useApp((s) => s.auditStatus);
  const error = useApp((s) => s.auditError);

  // Auto-fetch on mount of the Итоги step (fresh runs reset the audit to idle).
  useEffect(() => {
    if (getState().auditStatus === "idle") void loadAudit();
  }, []);

  return (
    <section data-testid="wizard-report">
      <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">
        Отчёт о соответствии нормам
      </div>
      <div className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5">
        {(status === "loading" || status === "idle") && (
          <div className="flex items-center gap-2 text-2xs text-ink-400">
            <Spinner className="h-2.5 w-2.5" /> проверка каждой посадки по действующим нормам…
          </div>
        )}
        {status === "error" && (
          <div className="text-2xs text-red-600 dark:text-red-400">
            {error}{" "}
            <button className="underline decoration-dotted" onClick={() => void loadAudit()}>Повторить</button>
          </div>
        )}
        {status === "no_result" && (
          <div className="text-2xs text-ink-400">Нет результата генерации — вернитесь на шаг «Генерация».</div>
        )}
        {status === "loaded" && audit && (
          <>
            <div className="text-xs leading-relaxed text-ink-100">
              <span className="font-mono font-semibold">{audit.summary.placements}</span> посадок
              <span className="text-ink-600"> · </span>
              <span className="font-mono font-semibold text-emerald-600 dark:text-emerald-400">
                {audit.summary.compliant}
              </span>{" "}
              соответствуют
              <span className="text-ink-600"> · </span>
              <span className={`font-mono font-semibold ${audit.summary.withViolations > 0 ? "text-red-600 dark:text-red-400" : ""}`}>
                {audit.summary.withViolations}
              </span>{" "}
              с нарушениями
              <span className="ml-1.5 font-mono text-2xs text-ink-500">({audit.summary.checksTotal} проверок)</span>
            </div>
            {Object.keys(audit.byDocument).length > 0 && (
              <table className="mt-1.5 w-full border-t border-ink-700 text-left">
                <thead>
                  <tr className="text-2xs uppercase tracking-wider text-ink-500">
                    <th className="py-1 pr-2 font-medium">По документам</th>
                    <th className="py-1 pr-2 text-right font-medium">Проверок</th>
                    <th className="py-1 text-right font-medium">Нарушений</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(audit.byDocument).map(([doc, st]) => (
                    <tr key={doc} className="border-t border-ink-700/50">
                      <td className="max-w-[280px] truncate py-0.5 pr-2 text-2xs text-ink-300" title={doc}>{doc}</td>
                      <td className="py-0.5 pr-2 text-right font-mono text-2xs text-ink-400">{st.checks}</td>
                      <td className={`py-0.5 text-right font-mono text-2xs ${st.failed > 0 ? "font-semibold text-red-600 dark:text-red-400" : "text-ink-500"}`}>
                        {st.failed}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function Step4Summary() {
  const doc = useApp((s) => s.document);
  const iterations = useApp((s) => s.iterations);
  const it = pickIteration(iterations);

  if (!doc || !it) {
    return (
      <div className="py-4 text-xs text-ink-500" data-testid="wizard-step-4">
        Результата ещё нет — вернитесь на шаг «Генерация».
      </div>
    );
  }

  const comp = it.judge?.deterministicComponents ?? null;
  const bars = comp
    ? [
        { label: "Покрытие", value: comp.coverage },
        { label: "Распределение", value: comp.distribution },
        { label: "Использование", value: comp.utilizationBalance },
        { label: "Кластеризация", value: comp.clusterPenalty, invert: true },
      ]
    : [
        { label: "Покрытие", value: it.metrics.coverageScore },
        { label: "Распределение", value: it.metrics.distributionScore },
      ];
  const changes = judgeChanges(it.judge);

  return (
    <div className="space-y-3" data-testid="wizard-step-4">
      {/* Result metrics */}
      <section className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5">
        {/* TOP: deterministic regulatory verdict — separate from the judge chip below. */}
        <RegulatoryVerdictBlock verdict={parseRegulatoryVerdict(it.regulatory)} />

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-ink-700 pt-2">
          <span className="font-mono text-sm text-ink-100">
            {it.metrics.treeCount} <span className="text-2xs text-ink-400">деревьев</span>
          </span>
          <span className="font-mono text-sm text-ink-100">
            {it.metrics.shrubCount} <span className="text-2xs text-ink-400">кустарников</span>
          </span>
          <Badge tone={it.metrics.hardViolations > 0 ? "red" : "green"}>
            {it.metrics.hardViolations > 0 ? `${it.metrics.hardViolations} наруш.` : "0 нарушений"}
          </Badge>
          {it.judge && (
            <span className="ml-auto flex items-center gap-1.5">
              <Badge
                tone={it.judge.decision === "ACCEPT" ? "green" : it.judge.decision === "RERUN" ? "amber" : "violet"}
                title="Решение LLM-судьи об оптимизации — не юридический вердикт"
              >
                {it.judge.decision === "ACCEPT"
                  ? "Оптимизация: завершена"
                  : it.judge.decision === "RERUN"
                    ? "Оптимизация: можно улучшить"
                    : "Требуется ручная проверка"}
              </Badge>
              <Badge tone="grey">{it.judge.mode === "llm" ? "LLM" : "эвристика"}</Badge>
            </span>
          )}
        </div>
        {it.judge && (
          <div className="mt-0.5 text-right text-2xs italic text-ink-500">
            оценка качества размещения, НЕ юридическое соответствие
          </div>
        )}
        <div className="mt-2 space-y-1">
          {bars.map((b) => (
            <MetricBar key={b.label} label={b.label} value={b.value} invert={"invert" in b ? b.invert : false} />
          ))}
        </div>
      </section>

      <ComplianceReport />

      {changes.length > 0 && (
        <div className="rounded-sm border border-ink-600 bg-ink-950/40 p-2.5 text-2xs text-ink-400">
          <span className="font-semibold text-ink-300">Рекомендации для перегенерации:</span>{" "}
          {changes.map((ch, i) => (
            <span key={ch.parameter} className="font-mono text-ink-300">
              {i > 0 ? " · " : ""}
              {ch.parameter}{" "}
              {Number.isFinite(ch.currentValue) ? `${ch.currentValue} → ` : "→ "}
              <span className="text-emerald-700 dark:text-emerald-300">{ch.proposedValue}</span>
            </span>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        <Btn
          onClick={() => {
            closeWizard();
            openAudit();
          }}
          title="Полный постраничный аудит соответствия"
        >
          Полный аудит
        </Btn>
        <Btn
          onClick={() => setWizardStep(3)}
          title={changes.length > 0 ? "Вернуться к генерации (рекомендации судьи выше)" : "Вернуться к генерации"}
        >
          Перегенерировать
        </Btn>
        <Btn onClick={() => window.open(api.exportDxfUrl(doc.projectId), "_blank")}>Скачать DXF</Btn>
        <Btn onClick={() => window.open(api.exportReportUrl(doc.projectId), "_blank")}>Скачать отчёт</Btn>
      </div>
    </div>
  );
}

// === Shell ======================================================================

export function Wizard() {
  const open = useApp((s) => s.wizardOpen);
  const step = useApp((s) => s.wizardStep);
  const doc = useApp((s) => s.document);
  const runActive = useApp((s) => s.runActive);
  const hasResult = useApp((s) => s.iterations.length > 0);
  if (!open || !doc) return null;

  const finish = () => {
    closeWizard();
    setNav("plan");
    setWorkspaceTab("result");
  };

  // Footer «Далее» availability: steps 1–2 always; step 3 once a result exists.
  const nextEnabled = step === 1 || step === 2 || (step === 3 && hasResult && !runActive);

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/50 md:items-center md:p-6"
      data-testid="wizard"
    >
      <div className="flex h-full w-full flex-col bg-ink-900 md:h-auto md:max-h-[88vh] md:w-full md:max-w-2xl md:rounded-sm md:border md:border-ink-500 md:shadow-2xl md:shadow-black/40">
        {/* Header: stepper + skip */}
        <div className="flex shrink-0 items-center gap-2 border-b border-ink-600 px-3 py-2 pt-[max(8px,env(safe-area-inset-top))] md:pt-2">
          <Stepper step={step} />
          <div className="flex-1" />
          <button
            className="shrink-0 whitespace-nowrap text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
            onClick={() => closeWizard()}
            data-testid="wizard-skip"
            title="Закрыть мастер и продолжить в обычном рабочем пространстве"
          >
            Пропустить шаги
          </button>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto p-3 md:p-4">
          {step === 1 && <Step1Constraints />}
          {step === 2 && <Step2Conditions />}
          {step === 3 && <Step3Generate />}
          {step === 4 && <Step4Summary />}
        </div>

        {/* Footer */}
        <div className="flex shrink-0 items-center gap-2 border-t border-ink-600 px-3 py-2 pb-[max(8px,env(safe-area-inset-bottom))] md:pb-2">
          {step > 1 && (
            <Btn variant="ghost" onClick={() => setWizardStep((step - 1) as 1 | 2 | 3)}>
              ← Назад
            </Btn>
          )}
          <div className="flex-1" />
          {step < 4 ? (
            <Btn
              variant="primary"
              className="min-w-[96px] font-semibold"
              disabled={!nextEnabled}
              data-testid="wizard-next"
              onClick={() => setWizardStep((step + 1) as 2 | 3 | 4)}
              title={
                step === 3 && !hasResult
                  ? "Сначала сгенерируйте план"
                  : step === 3 && runActive
                    ? "Дождитесь окончания генерации"
                    : undefined
              }
            >
              Далее →
            </Btn>
          ) : (
            <Btn
              variant="primary"
              className="min-w-[96px] font-semibold"
              data-testid="wizard-done"
              onClick={finish}
            >
              Готово
            </Btn>
          )}
        </div>
      </div>
    </div>
  );
}
