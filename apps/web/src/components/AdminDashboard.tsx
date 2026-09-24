import { useCallback, useEffect, useRef, useState } from "react";
import type { ProjectSummary } from "../api/types";
import { loadProjectSummaries, openExample, openProject, uploadFile } from "../state/actions";
import { ensureRegulationsLoaded } from "../state/regulations";
import { useApp } from "../state/store";
import { Badge, Btn, Spinner } from "./ui";

function formatDate(v: string | number): string {
  // Contract says ISO string; current backend emits unix seconds — accept both.
  const d = typeof v === "number" || /^\d+(\.\d+)?$/.test(String(v))
    ? new Date(Number(v) * 1000)
    : new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString(undefined, {
    year: "2-digit", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

// --- Decorative plant band ----------------------------------------------------
// Same scalloped-crown CAD-plan glyph language as viewer/plantSymbols.ts,
// rendered as a subtle inline SVG frieze (muted greens, light+dark aware).

/** Scalloped "cloud" crown path: n outward arcs between points on radius r. */
function cloudPathD(cx: number, cy: number, r: number, n: number): string {
  let d = "";
  for (let i = 0; i <= n; i++) {
    const a0 = (i / n) * Math.PI * 2 - Math.PI / 2;
    const x = cx + Math.cos(a0) * r;
    const y = cy + Math.sin(a0) * r;
    if (i === 0) {
      d += `M ${x.toFixed(1)} ${y.toFixed(1)}`;
    } else {
      const aMid = ((i - 0.5) / n) * Math.PI * 2 - Math.PI / 2;
      const qx = cx + Math.cos(aMid) * r * 1.33;
      const qy = cy + Math.sin(aMid) * r * 1.33;
      d += ` Q ${qx.toFixed(1)} ${qy.toFixed(1)} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }
  }
  return d + " Z";
}

interface Glyph {
  kind: "tree" | "shrub";
  x: number;
  r: number;
  lobes: number;
  tone: number;
}

/** Deterministic varied row — no Math.random so screenshots stay stable. */
const BAND_GLYPHS: Glyph[] = [
  { kind: "tree", x: 26, r: 15, lobes: 9, tone: 0 },
  { kind: "shrub", x: 56, r: 7, lobes: 0, tone: 1 },
  { kind: "tree", x: 88, r: 11, lobes: 8, tone: 2 },
  { kind: "tree", x: 124, r: 17, lobes: 10, tone: 1 },
  { kind: "shrub", x: 156, r: 6, lobes: 0, tone: 0 },
  { kind: "tree", x: 188, r: 13, lobes: 9, tone: 2 },
  { kind: "shrub", x: 216, r: 8, lobes: 0, tone: 2 },
  { kind: "tree", x: 250, r: 16, lobes: 11, tone: 0 },
  { kind: "tree", x: 288, r: 10, lobes: 8, tone: 1 },
  { kind: "shrub", x: 316, r: 7, lobes: 0, tone: 0 },
  { kind: "tree", x: 350, r: 14, lobes: 9, tone: 2 },
  { kind: "shrub", x: 380, r: 6, lobes: 0, tone: 1 },
  { kind: "tree", x: 412, r: 17, lobes: 10, tone: 0 },
  { kind: "tree", x: 450, r: 11, lobes: 8, tone: 1 },
  { kind: "shrub", x: 478, r: 8, lobes: 0, tone: 2 },
  { kind: "tree", x: 512, r: 15, lobes: 9, tone: 0 },
  { kind: "shrub", x: 542, r: 6, lobes: 0, tone: 1 },
  { kind: "tree", x: 574, r: 12, lobes: 8, tone: 2 },
  { kind: "tree", x: 610, r: 16, lobes: 10, tone: 1 },
];

const GLYPH_TONES = [
  "text-emerald-700/70 dark:text-emerald-400/60",
  "text-green-800/60 dark:text-green-500/50",
  "text-emerald-600/50 dark:text-emerald-300/40",
];

function PlantBand() {
  const GROUND = 52;
  return (
    <svg
      viewBox="0 0 640 60"
      className="h-12 w-full max-w-3xl select-none sm:h-14"
      aria-hidden="true"
      fill="none"
    >
      <line
        x1="4" y1={GROUND} x2="636" y2={GROUND}
        stroke="currentColor" strokeWidth="1"
        className="text-ink-500" strokeDasharray="1 5" strokeLinecap="round"
      />
      {BAND_GLYPHS.map((g, i) => {
        const cls = GLYPH_TONES[g.tone];
        if (g.kind === "shrub") {
          const cy = GROUND - g.r - 1;
          return (
            <g key={i} stroke="currentColor" className={cls}>
              <circle cx={g.x} cy={cy} r={g.r} strokeWidth="1.4" fill="currentColor" fillOpacity="0.08" />
              <circle cx={g.x} cy={cy} r={g.r * 0.62} strokeWidth="1" />
              <circle cx={g.x} cy={cy} r="0.9" fill="currentColor" stroke="none" />
            </g>
          );
        }
        const cy = GROUND - g.r - 8;
        return (
          <g key={i} stroke="currentColor" className={cls}>
            <path d={cloudPathD(g.x, cy, g.r, g.lobes)} strokeWidth="1.4" fill="currentColor" fillOpacity="0.08" strokeLinejoin="round" />
            <line x1={g.x} y1={cy + g.r * 0.7} x2={g.x} y2={GROUND} strokeWidth="1.2" />
            <circle cx={g.x} cy={cy} r="1.1" fill="currentColor" stroke="none" />
          </g>
        );
      })}
    </svg>
  );
}

// --- Stats strip ----------------------------------------------------------------

function StatChip({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div
      className="flex items-baseline gap-1.5 rounded-sm border border-ink-600 bg-ink-900 px-2.5 py-1"
      title={title}
    >
      <span className="font-mono text-sm text-ink-100">{value}</span>
      <span className="text-2xs text-ink-400">{label}</span>
    </div>
  );
}

function StatsStrip() {
  const summaries = useApp((s) => s.summaries);
  const summariesError = useApp((s) => s.summariesError);
  const regDocs = useApp((s) => s.regDocs);
  const regDocsError = useApp((s) => s.regDocsError);
  const rules = useApp((s) => s.rules);

  const chips: { label: string; value: string; title?: string }[] = [];
  if (summaries && !summariesError) {
    chips.push({ label: "проектов", value: String(summaries.length), title: "Projects" });
    const trees = summaries.reduce((acc, p) => acc + (p.result?.treeCount ?? 0), 0);
    chips.push({ label: "деревьев размещено", value: String(trees), title: "Trees placed (selected results)" });
  }
  if (regDocs && !regDocsError) {
    chips.push({ label: "документов", value: String(regDocs.length), title: "Regulatory documents" });
  }
  if (rules) {
    const active = rules.filter((r) => r.enabled && r.effectiveSource !== "inactive").length;
    chips.push({ label: "активных правил", value: String(active), title: "Active rules (effective in the engine)" });
  }
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {chips.map((c) => <StatChip key={c.label} {...c} />)}
    </div>
  );
}

// --- Upload zone ---------------------------------------------------------------

function UploadZone({ dragOver }: { dragOver: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const uploading = useApp((s) => s.uploading);
  const uploadError = useApp((s) => s.uploadError);
  const capabilities = useApp((s) => s.capabilities);
  const openingProject = useApp((s) => s.projectLoadingId) != null;

  const onFiles = useCallback((files: FileList | null) => {
    const file = files?.[0];
    if (file) void uploadFile(file);
  }, []);

  const converterMissing = uploadError?.code === "DWG_CONVERTER_UNAVAILABLE";
  const busy = uploading || openingProject;

  return (
    <div
      className={`flex w-full flex-col items-center gap-2.5 rounded-sm border border-dashed px-6 py-5 ${
        dragOver ? "border-emerald-600 bg-ink-900" : "border-ink-500"
      }`}
    >
      <div className="text-sm text-ink-200">Перетащите DWG / DXF сюда</div>

      {busy ? (
        <div className="flex items-center gap-2 py-0.5 text-xs text-ink-300">
          <Spinner /> {uploading ? "Загрузка и разбор чертежа…" : "Открытие проекта…"}
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <Btn variant="primary" onClick={() => inputRef.current?.click()}>
            Выбрать файл…
          </Btn>
          <Btn
            variant="ghost"
            className="text-ink-400"
            onClick={() => void openExample()}
            title="Load the bundled example site plan"
            data-testid="open-example"
          >
            Открыть пример
          </Btn>
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept=".dxf,.dwg"
        className="hidden"
        onChange={(e) => {
          onFiles(e.target.files);
          e.target.value = "";
        }}
      />

      {uploadError && !converterMissing && (
        <div className="w-full border border-red-300 bg-red-50 p-2 text-2xs text-red-700 dark:border-red-800 dark:bg-red-950/50 dark:text-red-300">
          <span className="font-semibold">Upload failed{uploadError.code ? ` (${uploadError.code})` : ""}:</span>{" "}
          {uploadError.message}
        </div>
      )}

      {converterMissing && (
        <div className="w-full border border-amber-300 bg-amber-50 p-2.5 text-2xs leading-relaxed text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200">
          <div className="mb-1 font-semibold uppercase tracking-wide">DWG converter unavailable</div>
          <p className="text-amber-800/90 dark:text-amber-200/90">
            This file is a binary DWG. The backend converts DWG → DXF with an external tool,
            and none was found. Install one of:
          </p>
          <ul className="my-1 list-inside list-disc text-amber-800/90 dark:text-amber-200/90">
            <li><span className="font-mono">ODA File Converter</span> — set <span className="font-mono">ODA_CONVERTER_PATH</span></li>
            <li><span className="font-mono">LibreDWG dwg2dxf</span> — set <span className="font-mono">DWG2DXF_PATH</span> (or have it on PATH)</li>
          </ul>
          <p className="text-amber-800/90 dark:text-amber-200/90">
            Then restart the API. Alternatively, export the drawing as DXF and upload that instead.
          </p>
          <p className="mt-1 text-amber-700/80 dark:text-amber-400/80">Backend message: {uploadError?.message}</p>
        </div>
      )}

      {capabilities && !capabilities.dwgConverter.available && !uploadError && (
        <div className="text-2xs text-ink-500">
          Note: DWG converter not detected on the backend — DXF only.
        </div>
      )}
    </div>
  );
}

// --- Projects table --------------------------------------------------------------

function judgeTone(d: string): "green" | "amber" | "violet" {
  if (d === "ACCEPT") return "green";
  if (d === "RERUN") return "amber";
  return "violet";
}

function pct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

/** Result cell: counts + violations badge + judge decision chip. */
function ResultCell({ r }: { r: ProjectSummary["result"] }) {
  if (!r) return <span className="text-ink-500">—</span>;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="whitespace-nowrap font-mono text-2xs text-ink-200">
        {r.treeCount ?? "—"} дер. / {r.shrubCount ?? "—"} куст.
      </span>
      <Badge tone={r.hardViolations > 0 ? "red" : "green"} title="hard violations">
        {r.hardViolations > 0 ? `${r.hardViolations} наруш.` : "0 наруш."}
      </Badge>
      {r.judgeDecision && (
        <Badge tone={judgeTone(r.judgeDecision)} title="judge decision">
          {r.judgeDecision === "MANUAL_REVIEW" ? "REVIEW" : r.judgeDecision}
        </Badge>
      )}
    </div>
  );
}

/** Actions: open + direct export downloads (plain <a download> per contract). */
function ActionsCell({ p }: { p: ProjectSummary }) {
  const loadingId = useApp((s) => s.projectLoadingId);
  const aCls =
    "inline-flex h-6 items-center rounded-sm border border-ink-600 bg-ink-800 px-1.5 font-sans text-2xs " +
    "text-ink-300 transition-colors hover:bg-ink-700 hover:text-ink-100 whitespace-nowrap";
  return (
    <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
      <button className={aCls} onClick={() => void openProject(p.id)} title="Open project">
        {loadingId === p.id ? <Spinner className="h-2.5 w-2.5" /> : "Открыть"}
      </button>
      {p.exports ? (
        <>
          <a className={aCls} href={p.exports.dxf} download title="Download DXF with plantings" data-testid="download-dxf">
            DXF ↓
          </a>
          <a className={aCls} href={p.exports.report} download title="Download JSON report" data-testid="download-report">
            Отчёт ↓
          </a>
        </>
      ) : (
        <span className="px-1 text-2xs text-ink-600" title="No generated result yet">— · —</span>
      )}
    </div>
  );
}

function UncertainCell({ n }: { n: number | null }) {
  if (n == null) return <span className="text-ink-500">—</span>;
  return n > 0 ? (
    <span className="text-amber-600 dark:text-amber-400" title={`${n} uncertain layers`}>⚠ {n}</span>
  ) : (
    <span className="text-emerald-600 dark:text-emerald-400">✓</span>
  );
}

function CadCell({ p }: { p: ProjectSummary }) {
  if (p.entityCount == null && p.layerCount == null && !p.units) {
    return <span className="text-ink-500">—</span>;
  }
  return (
    <span className="whitespace-nowrap font-mono text-2xs text-ink-300">
      {p.entityCount?.toLocaleString() ?? "—"} · {p.layerCount ?? "—"} сл. · {p.units ?? "—"}
    </span>
  );
}

function ProjectsTable({ summaries }: { summaries: ProjectSummary[] }) {
  return (
    <div className="hidden overflow-x-auto rounded-sm border border-ink-600 bg-ink-900 md:block">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-ink-600 text-2xs uppercase tracking-wider text-ink-500">
            <th className="px-2 py-1.5 font-medium">Файл</th>
            <th className="px-2 py-1.5 font-medium">Загружен</th>
            <th className="px-2 py-1.5 font-medium">CAD</th>
            <th className="px-2 py-1.5 font-medium" title="Uncertain layer classifications">Слои ⚠</th>
            <th className="px-2 py-1.5 font-medium">Результат</th>
            <th className="px-2 py-1.5 font-medium" title="coverage · distribution">Качество</th>
            <th className="px-2 py-1.5 text-right font-medium">Действия</th>
          </tr>
        </thead>
        <tbody>
          {summaries.map((p) => (
            <tr
              key={p.id}
              data-testid="project-row"
              className="cursor-pointer border-b border-ink-700/60 last:border-0 hover:bg-ink-850"
              onClick={() => void openProject(p.id)}
            >
              <td className="max-w-[220px] px-2 py-1.5">
                <div className="flex items-center gap-1.5">
                  <span className="min-w-0 truncate text-xs text-ink-100" title={p.fileName}>{p.name}</span>
                  {p.format && <Badge tone="grey">{p.format}</Badge>}
                </div>
              </td>
              <td className="whitespace-nowrap px-2 py-1.5 font-mono text-2xs text-ink-400">
                {formatDate(p.createdAt)}
              </td>
              <td className="px-2 py-1.5"><CadCell p={p} /></td>
              <td className="px-2 py-1.5 text-2xs"><UncertainCell n={p.uncertainLayers} /></td>
              <td className="px-2 py-1.5"><ResultCell r={p.result} /></td>
              <td className="whitespace-nowrap px-2 py-1.5 font-mono text-2xs text-ink-300">
                {p.result ? `${pct(p.result.coverageScore)} · ${pct(p.result.distributionScore)}` : "—"}
              </td>
              <td className="px-2 py-1.5"><ActionsCell p={p} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Mobile: the table collapses to cards (existing pattern). */
function ProjectCards({ summaries }: { summaries: ProjectSummary[] }) {
  return (
    <div className="space-y-1.5 md:hidden">
      {summaries.map((p) => (
        <div
          key={p.id}
          data-testid="project-row"
          className="cursor-pointer rounded-sm border border-ink-600 bg-ink-900 p-2"
          onClick={() => void openProject(p.id)}
        >
          <div className="flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-xs font-semibold text-ink-100">{p.name}</span>
            {p.format && <Badge tone="grey">{p.format}</Badge>}
            <span className="shrink-0 font-mono text-2xs text-ink-500">{formatDate(p.createdAt)}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-ink-400">
            <CadCell p={p} />
            <UncertainCell n={p.uncertainLayers} />
            {p.result && (
              <span className="font-mono text-ink-300">
                {pct(p.result.coverageScore)} · {pct(p.result.distributionScore)}
              </span>
            )}
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <ResultCell r={p.result} />
            <ActionsCell p={p} />
          </div>
        </div>
      ))}
    </div>
  );
}

// --- Dashboard --------------------------------------------------------------------

/**
 * Start screen: decorated admin dashboard — wordmark, plant-glyph band,
 * stats strip, prominent upload zone and the full projects table with
 * per-project CAD meta, result metrics and export downloads.
 */
export function AdminDashboard() {
  const [dragOver, setDragOver] = useState(false);
  const summaries = useApp((s) => s.summaries);
  const summariesError = useApp((s) => s.summariesError);

  useEffect(() => {
    void loadProjectSummaries();
    ensureRegulationsLoaded(); // "Документов" stat chip
  }, []);

  const sorted = summaries
    ? [...summaries].sort((a, b) => Number(b.createdAt) - Number(a.createdAt))
    : null;

  return (
    <div
      data-testid="admin-dashboard"
      className="h-full w-full overflow-y-auto bg-ink-950"
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files?.[0];
        if (file) void uploadFile(file);
      }}
    >
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-4 pb-10 pt-8">
        {/* Header row: wordmark + tagline */}
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <div className="text-2xl font-semibold tracking-tight text-ink-100">
            Green<span className="text-emerald-600 dark:text-emerald-400">Plan</span>
          </div>
          <div className="text-xs text-ink-400">
            Автоматическое размещение озеленения по нормативным ограничениям
          </div>
        </div>

        <PlantBand />

        <UploadZone dragOver={dragOver} />

        <StatsStrip />

        <div>
          <div className="mb-1.5 flex items-baseline justify-between">
            <div className="text-2xs font-semibold uppercase tracking-wider text-ink-400">Проекты</div>
            {summariesError && (
              <button
                className="text-2xs text-ink-500 underline decoration-dotted hover:text-ink-300"
                onClick={() => void loadProjectSummaries()}
              >
                обновить
              </button>
            )}
          </div>

          {sorted == null && !summariesError && (
            <div className="flex items-center gap-2 py-3 text-2xs text-ink-500">
              <Spinner /> загрузка проектов…
            </div>
          )}
          {summariesError && (
            <div className="py-2 text-2xs text-ink-500">
              Список проектов недоступен: {summariesError}
            </div>
          )}
          {sorted != null && sorted.length === 0 && (
            <div className="py-2 text-2xs text-ink-500">
              Проектов пока нет — загрузите чертёж или откройте пример.
            </div>
          )}
          {sorted != null && sorted.length > 0 && (
            <>
              <ProjectsTable summaries={sorted} />
              <ProjectCards summaries={sorted} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
