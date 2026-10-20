import { useState } from "react";
import { api } from "../api/client";
import { isDebug } from "../debug";
import { generateNow, newProject, openProject, setNav } from "../state/actions";
import { useApp, useStage, type NavView } from "../state/store";
import { toggleTheme, useTheme } from "../theme";
import { ActionIcon, Badge, Btn, Dropdown, Spinner } from "./ui";
import type { PipelineState } from "../api/types";
import { pipelineStateLabel } from "../labels";

function stateTone(s: PipelineState | null): "grey" | "green" | "amber" | "red" {
  if (!s) return "grey";
  if (s === "COMPLETED") return "green";
  if (s === "FAILED") return "red";
  if (s === "UPLOADED" || s === "CLASSIFYING" || s === "PARSING") return "grey";
  return "amber";
}

function SunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}

function ProjectMenu() {
  const doc = useApp((s) => s.document);
  const projects = useApp((s) => s.projects);
  const loadingId = useApp((s) => s.projectLoadingId);
  const [open, setOpen] = useState(false);
  if (!doc) return null;

  return (
    <Dropdown
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button
          className="flex min-w-0 items-center gap-1.5 rounded-sm border border-transparent px-1.5 py-0.5 text-xs text-ink-100 hover:border-ink-600 hover:bg-ink-800"
          title="Сменить проект"
        >
          <span className="max-w-[180px] truncate">{doc.fileName}</span>
          <span className="text-ink-400">▾</span>
        </button>
      }
    >
      <div className="px-2 pb-1 pt-0.5 text-2xs uppercase tracking-wider text-ink-500">Проекты</div>
      {(projects ?? []).map((p) => (
        <button
          key={p.id}
          className={`flex w-full items-center gap-1.5 px-2 py-1 text-left text-2xs hover:bg-ink-700 ${
            p.id === doc.projectId ? "text-emerald-700 dark:text-emerald-300" : "text-ink-200"
          }`}
          onClick={() => {
            setOpen(false);
            if (p.id !== doc.projectId) void openProject(p.id);
          }}
        >
          <span className="min-w-0 flex-1 truncate" title={`${p.name} · ${p.fileName}`}>{p.name}</span>
          {loadingId === p.id && <Spinner className="h-2.5 w-2.5" />}
          <span className="shrink-0 font-mono text-ink-500">{p.state}</span>
        </button>
      ))}
      {projects != null && projects.length === 0 && (
        <div className="px-2 py-1 text-2xs text-ink-500">нет сохранённых проектов</div>
      )}
      <div className="mt-1 border-t border-ink-600 pt-1">
        <button
          className="w-full px-2 py-1 text-left text-2xs text-ink-200 hover:bg-ink-700"
          onClick={() => {
            setOpen(false);
            newProject();
          }}
        >
          + Новый проект…
        </button>
      </div>
    </Dropdown>
  );
}

/** Mobile-only "⋯" menu: exports + project switcher live here below `md`. */
function MobileOverflowMenu() {
  const doc = useApp((s) => s.document);
  const projects = useApp((s) => s.projects);
  const loadingId = useApp((s) => s.projectLoadingId);
  const hasIterations = useApp((s) => s.iterations.length > 0);
  const [open, setOpen] = useState(false);
  const canExport = doc != null && hasIterations;

  return (
    <div className="md:hidden">
      <Dropdown
        open={open}
        onOpenChange={setOpen}
        trigger={
          <button
            className="inline-flex h-7 w-7 items-center justify-center rounded-sm border border-ink-600 bg-ink-800 text-ink-300 transition-colors hover:bg-ink-700 hover:text-ink-100"
            title="Ещё действия"
          >
            ⋯
          </button>
        }
      >
        <button
          className="flex w-full items-center gap-2 px-2 py-2 text-left text-xs text-ink-200 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!canExport}
          onClick={() => {
            setOpen(false);
            if (doc) window.open(api.exportDxfUrl(doc.projectId), "_blank");
          }}
        >
          <ActionIcon name="dxf" /> Экспорт DXF
        </button>
        <button
          className="flex w-full items-center gap-2 px-2 py-2 text-left text-xs text-ink-200 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!canExport}
          title="Только слои AI_GREENING_* в координатах исходного чертежа — подключить к генплану как внешнюю ссылку"
          onClick={() => {
            setOpen(false);
            if (doc) window.open(api.exportDxfUrl(doc.projectId, "result"), "_blank");
          }}
        >
          Экспорт DXF — только слои результата
        </button>
        <button
          className="flex w-full items-center gap-2 px-2 py-2 text-left text-xs text-ink-200 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!canExport}
          onClick={() => {
            setOpen(false);
            if (doc) window.open(api.exportReportUrl(doc.projectId), "_blank");
          }}
        >
          <ActionIcon name="report" /> Экспорт отчёта
        </button>
        <div className="my-1 border-t border-ink-600" />
        <div className="px-2 pb-1 pt-0.5 text-2xs uppercase tracking-wider text-ink-500">Проекты</div>
        {(projects ?? []).map((p) => (
          <button
            key={p.id}
            className={`flex w-full items-center gap-1.5 px-2 py-2 text-left text-xs hover:bg-ink-700 ${
              doc && p.id === doc.projectId ? "text-emerald-700 dark:text-emerald-300" : "text-ink-200"
            }`}
            onClick={() => {
              setOpen(false);
              if (p.id !== doc?.projectId) void openProject(p.id);
            }}
          >
            <span className="min-w-0 flex-1 truncate">{p.name}</span>
            {loadingId === p.id && <Spinner className="h-2.5 w-2.5" />}
            <span className="shrink-0 font-mono text-2xs text-ink-500">{p.state}</span>
          </button>
        ))}
        {projects != null && projects.length === 0 && (
          <div className="px-2 py-1 text-2xs text-ink-500">нет сохранённых проектов</div>
        )}
        <div className="mt-1 border-t border-ink-600 pt-1">
          <button
            className="w-full px-2 py-2 text-left text-xs text-ink-200 hover:bg-ink-700"
            onClick={() => {
              setOpen(false);
              newProject();
            }}
          >
            + Новый проект…
          </button>
        </div>
      </Dropdown>
    </div>
  );
}

/**
 * Slim top-level switcher. PLAN and RUNS are the primary pair; REGULATIONS and
 * QUALITY are intentionally de-emphasized (smaller, muted, right-aligned).
 */
function NavTabs() {
  const nav = useApp((s) => s.nav);
  const iterationCount = useApp((s) => s.iterations.length);

  const primary: { id: NavView; label: string; badge?: number; testid: string }[] = [
    { id: "plan", label: "ПЛАН", testid: "nav-plan" },
    {
      id: "runs", label: "ЗАПУСКИ",
      badge: iterationCount > 1 ? iterationCount : undefined,
      testid: "nav-runs",
    },
  ];

  return (
    <div className="hidden items-center overflow-hidden rounded-sm border border-ink-600 md:flex">
      {primary.map((t) => (
        <button
          key={t.id}
          data-testid={t.testid}
          className={`flex h-6 items-center gap-1 px-2.5 text-2xs font-semibold tracking-wider transition-colors ${
            nav === t.id
              ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-800/70 dark:text-emerald-100"
              : "bg-ink-900 text-ink-400 hover:text-ink-100"
          }`}
          onClick={() => setNav(t.id)}
        >
          {t.label}
          {t.badge != null && (
            <span className="rounded-full bg-ink-700 px-1 font-mono text-2xs leading-3 text-ink-200">
              {t.badge}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

/** Muted secondary nav (REGULATIONS · QUALITY), right-aligned.
 *  Shared with the dashboard header (pass className="flex"). */
export function SecondaryNav({ className = "hidden md:flex" }: { className?: string }) {
  const nav = useApp((s) => s.nav);
  const items: { id: NavView; label: string; testid: string }[] = [
    { id: "regulations", label: "Нормативы", testid: "nav-regulations" },
  ];
  return (
    <div className={`items-center gap-0.5 ${className}`}>
      {items.map((t) => (
        <button
          key={t.id}
          data-testid={t.testid}
          className={`ui-button inline-flex h-7 items-center justify-center rounded-sm border border-ink-600 px-2.5 text-xs transition-colors ${nav===t.id?'bg-ink-700 text-ink-100':'bg-ink-800 text-ink-200 hover:bg-ink-700 hover:text-ink-100'}`}
          onClick={() => setNav(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Sun/moon theme switch — shared by the TopBar and the dashboard header. */
export function ThemeToggle() {
  const theme = useTheme();
  return (
    <button
      className="inline-flex h-7 w-7 items-center justify-center rounded-sm border border-ink-600 bg-ink-800 text-ink-300 transition-colors hover:bg-ink-700 hover:text-ink-100"
      onClick={toggleTheme}
      title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
    >
      {theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

export function TopBar() {
  const doc = useApp((s) => s.document);
  const pipelineState = useApp((s) => s.pipelineState);
  const runActive = useApp((s) => s.runActive);
  const hasIterations = useApp((s) => s.iterations.length > 0);
  const stage = useStage();

  return (
    <header className="flex h-[calc(3rem+env(safe-area-inset-top))] shrink-0 items-center gap-2.5 border-b border-ink-600 bg-ink-900 px-3 pt-[env(safe-area-inset-top)]">
      <button
        className="text-sm font-semibold tracking-tight text-ink-100 transition-opacity hover:opacity-75"
        onClick={() => newProject()}
        title="На главную (список проектов)"
        data-testid="logo-home"
      >
        Green<span className="text-emerald-600 dark:text-emerald-400">Plan</span>
      </button>

      {doc && (
        <>
          <div className="hidden h-5 w-px bg-ink-600 md:block" />

          {/* Desktop: full project switcher; mobile: just the truncated file name. */}
          <div className="hidden min-w-0 md:block">
            <ProjectMenu />
          </div>
          <span className="min-w-0 max-w-[30vw] truncate text-xs text-ink-100 md:hidden">
            {doc.fileName}
          </span>

          <NavTabs />

          {isDebug && (
            <div className="hidden min-w-0 items-center gap-2.5 lg:flex">
              <span className="font-mono text-2xs text-ink-500">{doc.format}</span>
              <span className="font-mono text-2xs text-ink-500">{doc.cadVersion}</span>
              <span className="font-mono text-2xs text-ink-500">единицы: {doc.units}</span>
              <span className="font-mono text-2xs text-ink-500">
                {doc.entityCount.toLocaleString()} объектов / {doc.layerCount} слоёв
              </span>
            </div>
          )}
        </>
      )}

      <div className="flex-1" />

      <SecondaryNav />

      {doc && pipelineState && (
        <Badge tone={stateTone(pipelineState)}>
          {runActive && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
          {pipelineStateLabel(pipelineState)}
        </Badge>
      )}

      {doc && stage !== "empty" && (
        <Btn
          variant="primary"
          className="hidden font-semibold md:inline-flex"
          disabled={runActive}
          onClick={() => generateNow()}
          title="Сгенерировать план посадок с текущими настройками"
          data-testid="generate-button"
        >
          {runActive ? <><Spinner /> Генерация…</> : "Сгенерировать план посадок"}
        </Btn>
      )}

      {doc && hasIterations && (
        <div className="hidden items-center gap-2 md:flex">
          <Btn
            onClick={() => window.open(api.exportDxfUrl(doc.projectId), "_blank")}
            title="Скачать DXF со сгенерированными посадками"
            data-testid="export-dxf"
          >
            <ActionIcon name="dxf" /> Экспорт DXF
          </Btn>
          <Btn
            onClick={() => window.open(api.exportReportUrl(doc.projectId), "_blank")}
            title="Скачать отчёт в JSON"
            data-testid="export-report"
          >
            <ActionIcon name="report" /> Экспорт отчёта
          </Btn>
        </div>
      )}

      {doc && <div className="flex items-center gap-2">
        <Btn disabled={runActive} onClick={async () => {
          try {
            const r = await fetch(`/api/projects/${doc.projectId}/refresh-import`, {method: "POST"});
            if (!r.ok) throw new Error(await r.text());
            await openProject(doc.projectId);
            window.alert("Импорт обновлён. Сгенерируйте план заново для проверки по полной геометрии.");
          } catch (e) { window.alert(String(e)); }
        }} title="Повторно прочитать сохранённый CAD новым импортёром; после обновления пересчитайте план"><ActionIcon name="refresh" /><span className="sr-only md:not-sr-only">Обновить импорт</span></Btn>
        <Btn onClick={() => window.open(`/api/projects/${doc.projectId}/cad-preview.svg`, "_blank")} title="Исходный CAD: текст, штриховки, блоки и цвета без фильтрации по классификации"><ActionIcon name="cad" /><span className="sr-only md:not-sr-only">CAD оригинал</span></Btn>
      </div>}
      {doc && <MobileOverflowMenu />}

      <ThemeToggle />
    </header>
  );
}
