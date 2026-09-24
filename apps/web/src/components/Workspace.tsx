import { openAudit, setNav, setWorkspaceTab, togglePanelCollapsed } from "../state/actions";
import { openWizard } from "../state/wizard";
import { useApp, type WorkspaceTab } from "../state/store";
import { Btn, Spinner } from "./ui";
import { ConstraintsPanel } from "./panels/ConstraintsPanel";
import { GeneratePanel } from "./panels/GeneratePanel";
import { LayersPanel } from "./panels/LayersPanel";
import { QualityCard } from "./panels/QualityCard";

/** Compact interpretation summary — file meta above the layers list. */
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

/** Secondary section below the quality card: entry into the законность view. */
function AuditSection() {
  const audit = useApp((s) => s.audit);
  const status = useApp((s) => s.auditStatus);
  return (
    <div className="border-b border-ink-600 px-2 py-2">
      <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-300">
        Аудит соответствия
      </div>
      <p className="mb-1.5 text-2xs leading-relaxed text-ink-400">
        Детерминированная проверка каждой посадки по всем действующим нормам
        (реестр + правила из документов) с точными расстояниями и ссылками.
      </p>
      {audit && status === "loaded" && (
        <div className="mb-1.5 flex items-center gap-1.5 font-mono text-2xs text-ink-300">
          <span>{audit.summary.placements} посадок</span>
          <span className="text-ink-500">·</span>
          {audit.summary.withViolations > 0 ? (
            <span className="text-red-600 dark:text-red-400">
              {audit.summary.withViolations} с нарушениями
            </span>
          ) : (
            <span className="text-emerald-600 dark:text-emerald-400">все соответствуют</span>
          )}
        </div>
      )}
      <Btn
        className="w-full"
        onClick={() => openAudit()}
        title="Compliance audit against all effective regulations"
        data-testid="open-audit"
      >
        {status === "loading" ? <><Spinner /> Проверка…</> : "Аудит соответствия нормам"}
      </Btn>
    </div>
  );
}

/** Результат tab: summary line + quality review + compliance-audit entry. */
export function ResultPanelContent() {
  const hasIterations = useApp((s) => s.iterations.length > 0);
  if (!hasIterations) {
    return (
      <div className="p-3 text-xs text-ink-500">
        Сначала сгенерируйте план — вкладка «Генерация».
      </div>
    );
  }
  return (
    <>
      <ResultSummary />
      <QualityCard />
      <AuditSection />
    </>
  );
}

/** Content of a single workspace tab — shared by desktop panel and sheet. */
export function WorkspaceTabContent({ tab }: { tab: WorkspaceTab }) {
  switch (tab) {
    case "interpretation":
      return (
        <>
          <InterpretationHeader />
          <LayersPanel />
        </>
      );
    case "constraints":
      return <ConstraintsPanel />;
    case "generate":
      return <GeneratePanel />;
    case "result":
      return <ResultPanelContent />;
  }
}

// --- Tab strip ---------------------------------------------------------------

const TAB_DEFS: { id: WorkspaceTab; label: string; title: string; testid: string }[] = [
  { id: "interpretation", label: "Интерпретация", title: "Interpretation — CAD layers & classification", testid: "tab-interpretation" },
  { id: "constraints", label: "Ограничения", title: "Constraints — exclusion zones & planting domain", testid: "tab-constraints" },
  { id: "generate", label: "Генерация", title: "Generate — species, strategy, run progress", testid: "tab-generate" },
  { id: "result", label: "Результат", title: "Result — placements, quality review, audit", testid: "tab-result" },
];

function TabGlyph({ tab }: { tab: WorkspaceTab }) {
  const doc = useApp((s) => s.document);
  const constraints = useApp((s) => s.constraints);
  const constraintsLoading = useApp((s) => s.constraintsLoading);
  const constraintsError = useApp((s) => s.constraintsError);
  const runActive = useApp((s) => s.runActive);
  const iterations = useApp((s) => s.iterations);

  switch (tab) {
    case "interpretation": {
      const uncertain = doc?.layers.filter((l) => l.classification.requiresReview).length ?? 0;
      return uncertain > 0 ? (
        <span className="text-amber-600 dark:text-amber-400" title={`${uncertain} uncertain layers`}>
          ⚠{uncertain}
        </span>
      ) : (
        <span className="text-emerald-600 dark:text-emerald-400">✓</span>
      );
    }
    case "constraints":
      if (constraintsLoading) return <Spinner className="h-2.5 w-2.5" />;
      if (constraints) return <span className="text-emerald-600 dark:text-emerald-400">✓</span>;
      if (constraintsError) return <span className="text-red-600 dark:text-red-400">⚠</span>;
      return null;
    case "generate":
      return runActive
        ? <Spinner className="h-2.5 w-2.5" />
        : <span className="text-ink-400">▶</span>;
    case "result": {
      if (iterations.length === 0) return <span className="text-ink-500">—</span>;
      const it = iterations.find((x) => x.selected) ?? iterations[iterations.length - 1];
      return (
        <span className="font-mono text-emerald-700 dark:text-emerald-300" title="trees placed">
          {it.metrics.treeCount}
        </span>
      );
    }
  }
}

/**
 * Horizontal workspace tab strip directly under the TopBar (PLAN view).
 * Exactly one section panel is open at a time; the same strip drives the
 * desktop left panel and the mobile bottom sheet.
 */
export function WorkspaceTabs() {
  const active = useApp((s) => s.workspaceTab);
  const collapsed = useApp((s) => s.panelCollapsed);
  const hasResult = useApp((s) => s.iterations.length > 0);

  return (
    <div
      className="flex h-8 shrink-0 items-stretch overflow-x-auto border-b border-ink-600 bg-ink-900 pl-1"
      data-testid="workspace-tabs"
    >
      <button
        className="hidden items-center px-1.5 text-xs text-ink-500 transition-colors hover:text-ink-100 md:flex"
        onClick={() => togglePanelCollapsed()}
        title={collapsed ? "Показать панель" : "Скрыть панель (холст во всю ширину)"}
      >
        {collapsed ? "»" : "«"}
      </button>
      {TAB_DEFS.map((t) => {
        const disabled = t.id === "result" && !hasResult;
        const isActive = t.id === active && !collapsed;
        return (
          <button
            key={t.id}
            data-testid={t.testid}
            disabled={disabled}
            title={t.title}
            className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 text-2xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
              isActive
                ? "border-emerald-500 bg-ink-850 text-ink-100"
                : "border-transparent text-ink-400 hover:text-ink-100"
            }`}
            onClick={() => setWorkspaceTab(t.id)}
          >
            {t.label}
            <TabGlyph tab={t.id} />
          </button>
        );
      })}
      <div className="min-w-2 flex-1" />
      <button
        data-testid="open-wizard"
        className="mr-1 flex shrink-0 items-center gap-1 self-center rounded-sm border border-ink-600 bg-ink-800 px-2 py-0.5 text-2xs text-ink-300 transition-colors hover:bg-ink-700 hover:text-ink-100"
        onClick={() => openWizard(1)}
        title="Пошаговый мастер: Ограничения → Условия → Генерация → Итоги"
      >
        Мастер
      </button>
    </div>
  );
}

/** Desktop-only compact left panel showing the active workspace tab. */
export function WorkspacePanel() {
  const doc = useApp((s) => s.document);
  const tab = useApp((s) => s.workspaceTab);
  const collapsed = useApp((s) => s.panelCollapsed);
  if (!doc || collapsed) return null;

  return (
    <aside className="hidden w-[300px] shrink-0 flex-col overflow-y-auto border-r border-ink-600 bg-ink-900 md:flex">
      <WorkspaceTabContent tab={tab} />
    </aside>
  );
}
