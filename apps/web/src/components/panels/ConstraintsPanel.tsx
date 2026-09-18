import type { RegulatoryRule } from "../../api/types";
import { buildConstraints, select } from "../../state/actions";
import { useApp } from "../../state/store";
import { Badge, Btn, Section, Spinner } from "../ui";

function RuleRow({ rule }: { rule: RegulatoryRule }) {
  const inactive = rule.effectiveSource === "inactive" || !rule.enabled;
  return (
    <div className={`border-b border-ink-700/60 py-1.5 last:border-0 ${inactive ? "opacity-50" : ""}`}>
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-200" title={rule.id}>
          {rule.id}
        </span>
        {inactive ? (
          <Badge tone="grey" title={`Inactive: ${rule.status === "missing_source_data" ? "missing_source_data" : "disabled"}`}>
            INACTIVE
          </Badge>
        ) : rule.effectiveSource === "assumption" ? (
          <Badge tone="amber" title={rule.assumption?.note ?? "Distance is an unverified assumption"}>
            ASSUMED
          </Badge>
        ) : (
          <Badge tone="green" title={`${rule.source.document}${rule.source.clause ? ` · ${rule.source.clause}` : ""}`}>
            VERIFIED
          </Badge>
        )}
      </div>
      <div className="mt-0.5 flex items-center gap-2 text-2xs text-ink-400">
        <span className="font-mono">
          {rule.effectiveDistanceM != null ? `${rule.effectiveDistanceM} m` : "—"}
        </span>
        <span className="truncate" title={rule.source.title}>
          {rule.source.document}{rule.source.clause ? ` · ${rule.source.clause}` : ""}
        </span>
        <span className={rule.severity === "hard" ? "text-red-400/80" : "text-ink-500"}>
          {rule.severity}
        </span>
      </div>
      {inactive && (
        <div className="mt-0.5 text-2xs text-ink-500">
          reason: {rule.status === "missing_source_data" ? "missing_source_data" : "disabled by user"}
        </div>
      )}
    </div>
  );
}

export function ConstraintsPanel() {
  const doc = useApp((s) => s.document);
  const constraints = useApp((s) => s.constraints);
  const loading = useApp((s) => s.constraintsLoading);
  const error = useApp((s) => s.constraintsError);
  const rules = useApp((s) => s.rules);
  if (!doc) return null;

  const activeRules = rules?.filter((r) => r.enabled && r.effectiveSource !== "inactive") ?? [];
  const inactiveRules = rules?.filter((r) => !r.enabled || r.effectiveSource === "inactive") ?? [];

  return (
    <Section title="Constraints">
      <Btn onClick={() => void buildConstraints()} disabled={loading} className="w-full">
        {loading ? <><Spinner /> Building…</> : constraints ? "Rebuild constraints" : "Build constraints"}
      </Btn>

      {error && (
        <div className="mt-2 border border-red-800 bg-red-950/50 p-1.5 text-2xs text-red-300">{error}</div>
      )}

      {constraints && (
        <div className="mt-2 space-y-1 font-mono text-2xs text-ink-300">
          <div className="flex justify-between">
            <span className="text-ink-400">exclusion zones</span>
            <span>{constraints.exclusionZones.length}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-400">allowed area</span>
            <span>{constraints.allowedArea.areaM2.toLocaleString(undefined, { maximumFractionDigits: 0 })} m²</span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-400">site boundary</span>
            <span className={constraints.siteBoundarySource === "extents_fallback" ? "text-amber-400" : ""}>
              {constraints.siteBoundarySource}
            </span>
          </div>
        </div>
      )}

      {constraints && constraints.exclusionZones.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">
            Zones (click to inspect)
          </div>
          <div className="max-h-36 overflow-y-auto pr-1">
            {constraints.exclusionZones.map((z) => (
              <button
                key={z.id}
                className="flex w-full items-center gap-1.5 py-0.5 text-left hover:bg-ink-800"
                onClick={() => select({ type: "zone", zone: z })}
              >
                <span className="h-2 w-2 shrink-0 rounded-[2px] bg-red-500/60" />
                <span className="min-w-0 flex-1 truncate text-2xs text-ink-300">
                  {z.obstacleCategory} · {z.sourceLayer}
                </span>
                <span className="font-mono text-2xs text-ink-500">{z.distanceM} m</span>
                {z.effectiveSource === "assumption" && <Badge tone="amber">A</Badge>}
              </button>
            ))}
          </div>
        </div>
      )}

      {rules && (
        <div className="mt-2">
          <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">
            Rules — active ({activeRules.length})
          </div>
          {activeRules.map((r) => <RuleRow key={r.id} rule={r} />)}
          {inactiveRules.length > 0 && (
            <>
              <div className="mb-1 mt-2 text-2xs uppercase tracking-wider text-ink-500">
                Inactive ({inactiveRules.length})
              </div>
              {inactiveRules.map((r) => <RuleRow key={r.id} rule={r} />)}
            </>
          )}
        </div>
      )}
    </Section>
  );
}
