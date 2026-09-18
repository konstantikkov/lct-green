import type { ExclusionZone, Explanation, Placement } from "../api/types";
import { select } from "../state/actions";
import { useApp } from "../state/store";
import { Badge, KV, Spinner } from "./ui";

const DISTANCE_LABELS: Record<string, string> = {
  building: "Building",
  road: "Road",
  sidewalk: "Sidewalk",
  utility: "Utility",
  boundary: "Site boundary",
  existing_tree: "Existing tree",
  nearest_planting: "Nearest planting",
};

function SourceBadge({ source }: { source: "verified" | "assumption" }) {
  return source === "assumption"
    ? <Badge tone="amber" title="Distance is an unverified assumption (demo value)">ASSUMED</Badge>
    : <Badge tone="green" title="Backed by a verified regulatory source">VERIFIED</Badge>;
}

function ChecksList({ explanation }: { explanation: Explanation }) {
  return (
    <div className="space-y-1.5">
      {explanation.checks.map((c, i) => (
        <div key={i} className="border border-ink-700 bg-ink-900/60 p-1.5">
          <div className="flex items-center gap-1.5">
            <span className={c.result === "passed" ? "text-emerald-600 dark:text-emerald-400" : "text-ink-500"}>
              {c.result === "passed" ? "✓" : "–"}
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-200" title={c.ruleId}>
              {c.ruleId}
            </span>
            <SourceBadge source={c.effectiveSource} />
          </div>
          <div className="mt-1 flex items-center gap-2 font-mono text-2xs text-ink-300">
            <span className="text-ink-500">{c.obstacleCategory}</span>
            {c.actualM != null && c.requiredM != null ? (
              <span>
                {c.actualM.toFixed(2)} m <span className="text-ink-500">{c.comparator}</span>{" "}
                {c.requiredM.toFixed(2)} m
              </span>
            ) : (
              <span className="text-ink-500">n/a</span>
            )}
          </div>
          <div className="mt-0.5 truncate text-2xs text-ink-500" title={c.source.title}>
            {c.source.document}
            {c.source.clause ? ` · ${c.source.clause}` : ""} · {c.source.status}
          </div>
        </div>
      ))}
    </div>
  );
}

function PlacementInspector({ placement }: { placement: Placement }) {
  const species = useApp((s) => s.species);
  const iteration = useApp((s) => s.activeIteration);
  const explanation = useApp((s) => s.explanation);
  const loading = useApp((s) => s.explanationLoading);
  const error = useApp((s) => s.explanationError);

  const sp = species?.find((x) => x.id === placement.speciesId);
  const distances = Object.entries(placement.distances);

  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 flex items-center gap-2">
          <span className="text-xs font-semibold text-ink-100">
            {sp?.displayName ?? placement.speciesId}
          </span>
          <Badge tone={placement.kind === "TREE" ? "green" : "blue"}>{placement.kind}</Badge>
        </div>
        <KV k="id" v={placement.id} />
        <KV
          k="position"
          v={`${placement.position[0].toFixed(2)}, ${placement.position[1].toFixed(2)} m`}
        />
        <KV k="crown radius" v={`${placement.crownRadiusM.toFixed(2)} m`} />
        <KV k="iteration" v={iteration ? `#${iteration.index}` : "—"} />
        <KV k="score" v={placement.score.toFixed(3)} />
      </div>

      {distances.length > 0 && (
        <div>
          <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Distances</div>
          <table className="w-full">
            <tbody>
              {distances.map(([key, val]) => (
                <tr key={key} className="border-b border-ink-700/50 last:border-0">
                  <td className="py-0.5 text-2xs text-ink-400">{DISTANCE_LABELS[key] ?? key}</td>
                  <td className="py-0.5 text-right font-mono text-2xs text-ink-100">
                    {val != null ? `${val.toFixed(2)} m` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div>
        <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Regulatory checks</div>
        {loading && (
          <div className="flex items-center gap-2 text-2xs text-ink-500"><Spinner /> loading explanation…</div>
        )}
        {error && <div className="text-2xs text-red-600 dark:text-red-400">{error}</div>}
        {explanation && (
          <>
            <p className="mb-2 text-2xs leading-relaxed text-ink-300">{explanation.summary}</p>
            {explanation.llmNarrative && (
              <p className="mb-2 border-l-2 border-ink-600 pl-2 text-2xs italic leading-relaxed text-ink-400">
                {explanation.llmNarrative}
              </p>
            )}
            <ChecksList explanation={explanation} />
          </>
        )}
      </div>
    </div>
  );
}

function ZoneInspector({ zone }: { zone: ExclusionZone }) {
  const rules = useApp((s) => s.rules);
  const rule = rules?.find((r) => r.id === zone.ruleId);
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 flex items-center gap-2">
          <span className="text-xs font-semibold text-ink-100">Exclusion zone</span>
          <SourceBadge source={zone.effectiveSource} />
        </div>
        <KV k="id" v={zone.id} />
        <KV k="rule" v={zone.ruleId} />
        <KV k="obstacle" v={zone.obstacleCategory} mono={false} />
        <KV k="source layer" v={zone.sourceLayer} mono={false} />
        <KV k="offset distance" v={`${zone.distanceM.toFixed(2)} m`} />
      </div>
      <div>
        <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Regulatory source</div>
        <div className="border border-ink-700 bg-ink-900/60 p-1.5 text-2xs">
          <div className="text-ink-200">{zone.source.document}</div>
          {zone.source.clause && <div className="mt-0.5 text-ink-400">clause {zone.source.clause}</div>}
          <div className="mt-0.5 text-ink-500">status: {zone.source.status}</div>
          {rule && (
            <>
              <div className="mt-1 text-ink-400">{rule.source.title}</div>
              {rule.assumption && zone.effectiveSource === "assumption" && (
                <div className="mt-1 border-t border-ink-700 pt-1 text-amber-700 dark:text-amber-300/90">
                  {rule.assumption.note}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function Inspector() {
  const selection = useApp((s) => s.selection);
  if (!selection) return null;

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-ink-600 bg-ink-900">
      <div className="flex h-8 shrink-0 items-center justify-between border-b border-ink-600 px-2">
        <span className="text-2xs font-semibold uppercase tracking-wider text-ink-300">Inspector</span>
        <button
          className="px-1 text-ink-500 hover:text-ink-100"
          onClick={() => select(null)}
          title="Close"
        >
          ✕
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {selection.type === "placement"
          ? <PlacementInspector placement={selection.placement} />
          : <ZoneInspector zone={selection.zone} />}
      </div>
    </aside>
  );
}
