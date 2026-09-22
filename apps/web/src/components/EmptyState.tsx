import { useCallback, useRef, useState } from "react";
import { openExample, openProject, uploadFile } from "../state/actions";
import { useApp } from "../state/store";
import { Btn, Spinner } from "./ui";

function formatDate(v: string | number): string {
  // Contract says ISO string; current backend emits unix seconds — accept both.
  const d = typeof v === "number" || /^\d+(\.\d+)?$/.test(v)
    ? new Date(Number(v) * 1000)
    : new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString(undefined, {
    year: "2-digit", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

/** Quiet secondary list — rendered only when projects exist. */
function RecentProjects() {
  const projects = useApp((s) => s.projects);
  const projectsError = useApp((s) => s.projectsError);
  const loadingId = useApp((s) => s.projectLoadingId);

  if (projectsError) return null; // backend down — the upload flow surfaces it
  if (!projects || projects.length === 0) return null;

  return (
    <div className="w-full" data-testid="recent-projects">
      <div className="mb-1 px-1 text-2xs uppercase tracking-wider text-ink-500">
        Recent projects
      </div>
      <div className="max-h-56 overflow-y-auto">
        {projects.map((p) => (
          <button
            key={p.id}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-2 text-left hover:bg-ink-850 md:py-1.5"
            onClick={() => void openProject(p.id)}
            title={`${p.fileName} — open project`}
          >
            <span className="min-w-0 flex-1 truncate text-xs text-ink-300">{p.name}</span>
            <span className="shrink-0 font-mono text-2xs text-ink-500">{formatDate(p.createdAt)}</span>
            {loadingId === p.id && <Spinner className="h-2.5 w-2.5 shrink-0" />}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Near-blank landing screen: wordmark, tagline, drop zone, [Open example].
 * No panels, no status-bar clutter, no export buttons at this stage.
 */
export function EmptyState() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
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
      data-testid="empty-state"
      className={`flex h-full w-full overflow-y-auto transition-colors ${
        dragOver ? "bg-ink-850" : "bg-ink-950"
      }`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        onFiles(e.dataTransfer.files);
      }}
    >
      {/* m-auto centers when content fits and keeps the top reachable when it scrolls */}
      <div className="m-auto flex w-full max-w-md flex-col items-center gap-6 px-4 py-10">
        <div className="text-center">
          <div className="text-3xl font-semibold tracking-tight text-ink-100">
            Green<span className="text-emerald-600 dark:text-emerald-400">Plan</span>
          </div>
          <div className="mt-2 text-sm leading-relaxed text-ink-400">
            Автоматическое размещение озеленения по нормативным ограничениям
          </div>
        </div>

        <div
          className={`flex w-full flex-col items-center gap-3 rounded-sm border border-dashed px-8 py-8 ${
            dragOver ? "border-emerald-600" : "border-ink-500"
          }`}
        >
          <div className="text-center text-base text-ink-200">Drop DWG / DXF</div>

          {busy ? (
            <div className="flex items-center gap-2 py-1 text-xs text-ink-300">
              <Spinner /> {uploading ? "Uploading and parsing…" : "Opening project…"}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Btn variant="primary" onClick={() => inputRef.current?.click()}>
                Choose file…
              </Btn>
              <Btn
                variant="ghost"
                className="text-ink-400"
                onClick={() => void openExample()}
                title="Load the bundled example site plan"
                data-testid="open-example"
              >
                Open example
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

        <RecentProjects />
      </div>
    </div>
  );
}
