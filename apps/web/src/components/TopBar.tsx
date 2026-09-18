import { api } from "../api/client";
import { useApp } from "../state/store";
import { Badge, Btn } from "./ui";
import type { PipelineState } from "../api/types";

function stateTone(s: PipelineState | null): "grey" | "green" | "amber" | "red" {
  if (!s) return "grey";
  if (s === "COMPLETED") return "green";
  if (s === "FAILED") return "red";
  if (s === "UPLOADED" || s === "CLASSIFYING" || s === "PARSING") return "grey";
  return "amber";
}

export function TopBar() {
  const doc = useApp((s) => s.document);
  const pipelineState = useApp((s) => s.pipelineState);
  const runActive = useApp((s) => s.runActive);
  const hasIterations = useApp((s) => s.iterations.length > 0);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-ink-600 bg-ink-900 px-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold tracking-tight text-ink-100">
          Green<span className="text-emerald-400">Plan</span>
        </span>
        <span className="text-2xs uppercase tracking-widest text-ink-500">CAD greening</span>
      </div>

      <div className="h-5 w-px bg-ink-600" />

      {doc ? (
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="truncate text-xs text-ink-100" title={doc.fileName}>{doc.fileName}</span>
          <span className="font-mono text-2xs text-ink-400">{doc.format}</span>
          <span className="font-mono text-2xs text-ink-400">{doc.cadVersion}</span>
          <span className="font-mono text-2xs text-ink-400">units: {doc.units}</span>
          <span className="font-mono text-2xs text-ink-400">
            {doc.entityCount.toLocaleString()} entities / {doc.layerCount} layers
          </span>
        </div>
      ) : (
        <span className="text-xs text-ink-500">no file loaded</span>
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
    </header>
  );
}
