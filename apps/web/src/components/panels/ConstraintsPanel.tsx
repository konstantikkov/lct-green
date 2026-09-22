import { useState } from "react";
import type { AllowedBasis } from "../../api/types";
import { isDebug } from "../../debug";
import { buildConstraints, setOverlay } from "../../state/actions";
import { useApp, type OverlayState } from "../../state/store";
import { Checkbox, Section, Spinner } from "../ui";

/** Human-first wording; the jargon lives in the expandable details. */
const BASIS_INFO: Record<AllowedBasis, { warn: boolean; short: string; details: string }> = {
  site_boundary: {
    warn: false,
    short: "Область посадки: в границах участка",
    details:
      "На чертеже найден замкнутый контур границы участка — деревья размещаются только внутри него, за вычетом запретных зон.",
  },
  vegetation_evidence: {
    warn: false,
    short: "Область посадки: зелёные зоны (по существующему озеленению)",
    details:
      "Замкнутой границы участка на топоплане нет, поэтому область посадки построена вокруг существующего озеленения (дополняющая посадка).",
  },
  extents_fallback: {
    warn: true,
    short: "Область посадки: вся область чертежа",
    details:
      "Не удалось найти ни границу участка, ни зоны озеленения — используется вся область чертежа. Проверьте классификацию слоёв.",
  },
};

function BasisChip({ basis }: { basis: AllowedBasis }) {
  const [expanded, setExpanded] = useState(false);
  const info = BASIS_INFO[basis] ?? BASIS_INFO.extents_fallback;
  return (
    <div className="mt-2">
      <button
        className={`flex w-full items-start gap-1.5 rounded-sm border px-2 py-1.5 text-left text-2xs leading-snug ${
          info.warn
            ? "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
            : "border-ink-600 bg-ink-850 text-ink-300 hover:text-ink-100"
        }`}
        onClick={() => setExpanded((x) => !x)}
        title={info.details}
      >
        <span className="flex-1">{info.warn ? "⚠ " : ""}{info.short}</span>
        <span className="shrink-0 text-ink-500">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <p className="mt-1 px-0.5 text-2xs leading-relaxed text-ink-400">{info.details}</p>
      )}
    </div>
  );
}

const LEGEND: { key: keyof OverlayState; label: string; swatch: string }[] = [
  { key: "allowedArea", label: "Allowed planting", swatch: "bg-emerald-500/50" },
  { key: "exclusionZones", label: "Excluded zones", swatch: "bg-red-500/50" },
  { key: "existingVegetation", label: "Existing vegetation", swatch: "bg-green-600/50" },
  { key: "originalCad", label: "Original CAD", swatch: "bg-ink-400" },
];

/**
 * Constraints in PLAN: no giant tables — a quiet progress line while zones
 * build, then an overlay legend + a human-first planting-domain chip.
 * Individual zones are inspected by clicking them on the canvas.
 */
export function ConstraintsPanel() {
  const doc = useApp((s) => s.document);
  const constraints = useApp((s) => s.constraints);
  const loading = useApp((s) => s.constraintsLoading);
  const error = useApp((s) => s.constraintsError);
  const overlays = useApp((s) => s.overlays);
  if (!doc) return null;

  return (
    <Section title="Constraints">
      {loading && (
        <div className="flex items-center gap-2 py-0.5 text-2xs text-ink-400" data-testid="constraints-loading">
          <Spinner className="h-2.5 w-2.5" /> Building constraint zones…
        </div>
      )}

      {error && !loading && (
        <div className="border border-red-300 bg-red-50 p-1.5 text-2xs text-red-700 dark:border-red-800 dark:bg-red-950/50 dark:text-red-300">
          {error}
          <button
            className="ml-1.5 underline decoration-dotted hover:no-underline"
            onClick={() => void buildConstraints()}
          >
            Retry
          </button>
        </div>
      )}

      {constraints && (
        <>
          <div className="space-y-1" data-testid="overlay-legend">
            {LEGEND.map(({ key, label, swatch }) => (
              <div key={key} className="flex items-center gap-1.5">
                <span className={`h-2.5 w-2.5 shrink-0 rounded-[2px] ${swatch}`} />
                <Checkbox
                  checked={overlays[key]}
                  onChange={(v) => setOverlay(key, v)}
                  label={label}
                />
              </div>
            ))}
          </div>

          {constraints.allowedBasis && <BasisChip basis={constraints.allowedBasis} />}

          <div className="mt-1.5 text-2xs text-ink-500">
            Click a red zone on the canvas to see the rule behind it.
          </div>

          {isDebug && (
            <div className="mt-2 border-t border-ink-700 pt-1.5 font-mono text-2xs text-ink-500">
              <div>zones: {constraints.exclusionZones.length} · allowed: {constraints.allowedArea.areaM2.toLocaleString(undefined, { maximumFractionDigits: 0 })} m²</div>
              <div>site boundary: {constraints.siteBoundarySource}</div>
              {constraints.repairLog.length > 0 && (
                <div className="mt-1 max-h-24 overflow-y-auto">
                  {constraints.repairLog.map((line, i) => <div key={i}>{line}</div>)}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Section>
  );
}
