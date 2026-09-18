import { useCallback, useRef, useState } from "react";
import type { PipelineState } from "../api/types";
import { openProject, uploadFile } from "../state/actions";
import { useApp } from "../state/store";
import { Badge, Btn, Spinner } from "./ui";

function stateTone(s: PipelineState): "grey" | "green" | "amber" | "red" {
  if (s === "COMPLETED") return "green";
  if (s === "FAILED") return "red";
  if (s === "UPLOADED" || s === "CLASSIFYING" || s === "PARSING") return "grey";
  return "amber";
}

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

function RecentProjects() {
  const projects = useApp((s) => s.projects);
  const projectsError = useApp((s) => s.projectsError);
  const loadingId = useApp((s) => s.projectLoadingId);

  if (projectsError) return null; // backend down — the upload flow surfaces it
  if (!projects || projects.length === 0) return null;

  return (
    <div className="w-full border border-ink-600 bg-ink-900">
      <div className="border-b border-ink-600 px-2 py-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-400">
        Recent projects — shared
      </div>
      {/* Mobile: tappable list with large touch targets. */}
      <div className="max-h-72 overflow-y-auto md:hidden">
        {projects.map((p) => (
          <button
            key={p.id}
            className="flex w-full items-center gap-2 border-t border-ink-700/60 px-3 py-3 text-left first:border-t-0 active:bg-ink-800"
            onClick={() => void openProject(p.id)}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs text-ink-100">{p.name}</div>
              <div className="truncate font-mono text-2xs text-ink-400">
                {p.fileName} · {formatDate(p.createdAt)}
              </div>
            </div>
            {loadingId === p.id
              ? <Spinner className="h-2.5 w-2.5 shrink-0" />
              : <Badge tone={stateTone(p.state)}>{p.state}</Badge>}
          </button>
        ))}
      </div>
      {/* Desktop: dense table. */}
      <div className="hidden max-h-56 overflow-y-auto md:block">
        <table className="w-full text-left">
          <thead>
            <tr className="text-2xs uppercase tracking-wider text-ink-500">
              <th className="px-2 py-1 font-medium">Name</th>
              <th className="px-2 py-1 font-medium">File</th>
              <th className="px-2 py-1 font-medium">Created</th>
              <th className="px-2 py-1 text-right font-medium">State</th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr
                key={p.id}
                className="cursor-pointer border-t border-ink-700/60 text-xs text-ink-200 hover:bg-ink-800"
                onClick={() => void openProject(p.id)}
                title="Open project"
              >
                <td className="max-w-[160px] truncate px-2 py-1.5">{p.name}</td>
                <td className="max-w-[140px] truncate px-2 py-1.5 font-mono text-2xs text-ink-400">{p.fileName}</td>
                <td className="whitespace-nowrap px-2 py-1.5 font-mono text-2xs text-ink-400">{formatDate(p.createdAt)}</td>
                <td className="px-2 py-1.5 text-right">
                  {loadingId === p.id
                    ? <Spinner className="h-2.5 w-2.5" />
                    : <Badge tone={stateTone(p.state)}>{p.state}</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function DropZone() {
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

  return (
    <div
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
      <div className="m-auto flex w-full max-w-lg flex-col gap-3 px-4 py-6">
        <div
          className={`flex w-full flex-col items-center gap-4 border border-dashed px-8 py-10 ${
            dragOver ? "border-emerald-600" : "border-ink-500"
          }`}
        >
          <div className="font-mono text-2xs uppercase tracking-widest text-ink-500">
            GreenPlan · site plan intake
          </div>
          <div className="hidden text-center text-lg text-ink-100 md:block">Drop DWG / DXF</div>
          <div className="text-center text-lg text-ink-100 md:hidden">Upload a site plan</div>
          <div className="text-center text-xs text-ink-400">
            The plan is parsed, layers are classified and normalized to meters.
          </div>

          {uploading ? (
            <div className="flex items-center gap-2 text-xs text-ink-300">
              <Spinner /> Uploading and parsing…
            </div>
          ) : openingProject ? (
            <div className="flex items-center gap-2 text-xs text-ink-300">
              <Spinner /> Opening project…
            </div>
          ) : (
            <>
              <div className="hidden md:block">
                <Btn variant="primary" onClick={() => inputRef.current?.click()}>
                  Choose file…
                </Btn>
              </div>
              <Btn
                variant="primary"
                className="h-11 w-full max-w-xs text-sm md:hidden"
                onClick={() => inputRef.current?.click()}
              >
                Choose DWG / DXF file
              </Btn>
            </>
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
