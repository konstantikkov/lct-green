import { useState } from "react";
import { api } from "../api/client";
import { newProject, openProject } from "../state/actions";
import { useApp } from "../state/store";
import { toggleTheme, useTheme } from "../theme";
import { Badge, Btn, Dropdown, Spinner } from "./ui";
import type { PipelineState } from "../api/types";

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
  if (!doc) return <span className="text-xs text-ink-500">no file loaded</span>;

  return (
    <Dropdown
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button
          className="flex min-w-0 items-center gap-1.5 rounded-sm border border-transparent px-1.5 py-0.5 text-xs text-ink-100 hover:border-ink-600 hover:bg-ink-800"
          title="Switch project"
        >
          <span className="max-w-[220px] truncate">{doc.fileName}</span>
          <span className="text-ink-400">▾</span>
        </button>
      }
    >
      <div className="px-2 pb-1 pt-0.5 text-2xs uppercase tracking-wider text-ink-500">Projects</div>
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
        <div className="px-2 py-1 text-2xs text-ink-500">no saved projects</div>
      )}
      <div className="mt-1 border-t border-ink-600 pt-1">
        <button
          className="w-full px-2 py-1 text-left text-2xs text-ink-200 hover:bg-ink-700"
          onClick={() => {
            setOpen(false);
            newProject();
          }}
        >
          + New project…
        </button>
      </div>
    </Dropdown>
  );
}

export function TopBar() {
  const doc = useApp((s) => s.document);
  const pipelineState = useApp((s) => s.pipelineState);
  const runActive = useApp((s) => s.runActive);
  const hasIterations = useApp((s) => s.iterations.length > 0);
  const theme = useTheme();

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-ink-600 bg-ink-900 px-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold tracking-tight text-ink-100">
          Green<span className="text-emerald-600 dark:text-emerald-400">Plan</span>
        </span>
        <span className="text-2xs uppercase tracking-widest text-ink-500">CAD greening</span>
      </div>

      <div className="h-5 w-px bg-ink-600" />

      <ProjectMenu />

      {doc && (
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="font-mono text-2xs text-ink-400">{doc.format}</span>
          <span className="font-mono text-2xs text-ink-400">{doc.cadVersion}</span>
          <span className="font-mono text-2xs text-ink-400">units: {doc.units}</span>
          <span className="font-mono text-2xs text-ink-400">
            {doc.entityCount.toLocaleString()} entities / {doc.layerCount} layers
          </span>
        </div>
      )}

      <div className="flex-1" />

      {pipelineState && (
        <Badge tone={stateTone(pipelineState)}>
          {runActive && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
          {pipelineState}
        </Badge>
      )}

      <Btn
        disabled={!doc || !hasIterations}
        onClick={() => doc && window.open(api.exportDxfUrl(doc.projectId), "_blank")}
        title={hasIterations ? "Download DXF with generated plantings" : "Generate a plan first"}
      >
        Export DXF
      </Btn>
      <Btn
        disabled={!doc || !hasIterations}
        onClick={() => doc && window.open(api.exportReportUrl(doc.projectId), "_blank")}
        title={hasIterations ? "Download JSON report" : "Generate a plan first"}
      >
        Export report
      </Btn>

      <button
        className="inline-flex h-7 w-7 items-center justify-center rounded-sm border border-ink-600 bg-ink-800 text-ink-300 transition-colors hover:bg-ink-700 hover:text-ink-100"
        onClick={toggleTheme}
        title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      >
        {theme === "dark" ? <SunIcon /> : <MoonIcon />}
      </button>
    </header>
  );
}
