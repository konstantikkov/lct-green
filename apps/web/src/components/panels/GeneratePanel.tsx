import { useEffect, useMemo, useState } from "react";
import type { PlantKind, RunStage, Strategy } from "../../api/types";
import { startGeneration } from "../../state/actions";
import { useApp } from "../../state/store";
import { Btn, Checkbox, Section, Select, Spinner, TextInput } from "../ui";

const STRATEGIES: { value: Strategy; label: string }[] = [
  { value: "balanced", label: "Balanced" },
  { value: "maximum_trees", label: "Maximum trees" },
  { value: "maximum_canopy", label: "Maximum canopy" },
  { value: "street_greening", label: "Street greening" },
];

const STAGE_LABELS: Partial<Record<RunStage["stage"], string>> = {
  GENERATING_CANDIDATES: "Generating candidates",
  OPTIMIZING: "Optimizing",
  VALIDATING: "Validating",
  BUILDING_CONSTRAINTS: "Building constraints",
  JUDGING: "LLM judge",
  RERUNNING: "Re-running",
};

function StageChecklist() {
  const run = useApp((s) => s.run);
  if (!run) return null;
  return (
    <div className="mt-2 space-y-0.5 border-t border-ink-700 pt-2">
      {run.stages.map((st, i) => (
        <div key={i} className="flex items-center gap-1.5 text-2xs">
          <span className="w-3.5 text-center">
            {st.status === "done" && <span className="text-emerald-400">✓</span>}
            {st.status === "active" && <Spinner className="h-2.5 w-2.5" />}
            {st.status === "error" && <span className="text-red-400">✕</span>}
            {st.status === "pending" && <span className="text-ink-600">·</span>}
          </span>
          <span className={
            st.status === "active" ? "text-ink-100"
            : st.status === "done" ? "text-ink-300"
            : st.status === "error" ? "text-red-300"
            : "text-ink-500"
          }>
            {STAGE_LABELS[st.stage] ?? st.stage}
            {st.detail ? <span className="text-ink-500"> — {st.detail}</span> : null}
          </span>
        </div>
      ))}
      {run.currentIteration > 0 && (
        <div className="pt-0.5 font-mono text-2xs text-ink-500">iteration {run.currentIteration}</div>
      )}
    </div>
  );
}

export function GeneratePanel() {
  const doc = useApp((s) => s.document);
  const species = useApp((s) => s.species);
  const speciesError = useApp((s) => s.speciesError);
  const capabilities = useApp((s) => s.capabilities);
  const runActive = useApp((s) => s.runActive);
  const runError = useApp((s) => s.runError);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [strategy, setStrategy] = useState<Strategy>("balanced");
  const [targetCount, setTargetCount] = useState("");
  const [seed, setSeed] = useState("42");
  const [maxIterations, setMaxIterations] = useState("3");
  const [useLlmJudge, setUseLlmJudge] = useState(true);

  // Preselect all species when the catalog first loads (demo-friendly default).
  useEffect(() => {
    if (species && species.length > 0) {
      setSelected(new Set(species.map((sp) => sp.id)));
    }
  }, [species]);

  const chosen = useMemo(
    () => (species ?? []).filter((sp) => selected.has(sp.id)),
    [species, selected],
  );
  if (!doc) return null;

  const plantKinds = [...new Set(chosen.map((sp) => sp.kind))] as PlantKind[];
  const canGenerate = chosen.length > 0 && !runActive;

  const toggleSpecies = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const submit = () => {
    const tc = targetCount.trim() === "" ? null : Number(targetCount);
    void startGeneration({
      plantKinds: plantKinds.length > 0 ? plantKinds : ["TREE"],
      speciesIds: chosen.map((sp) => sp.id),
      strategy,
      targetCount: tc != null && Number.isFinite(tc) ? tc : null,
      seed: Number.isFinite(Number(seed)) ? Number(seed) : 42,
      maxIterations: Math.max(1, Number(maxIterations) || 3),
      useLlmJudge,
    });
  };

  return (
    <Section title="Generate">
      <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Species</div>
      {!species && !speciesError && (
        <div className="flex items-center gap-2 text-2xs text-ink-500"><Spinner /> loading species…</div>
      )}
      {speciesError && <div className="text-2xs text-red-400">species: {speciesError}</div>}
      {species && (
        <div className="max-h-40 space-y-0.5 overflow-y-auto pr-1">
          {species.map((sp) => (
            <label
              key={sp.id}
              className="flex cursor-pointer items-center gap-1.5 py-px text-xs text-ink-200 hover:text-ink-100"
            >
              <input
                type="checkbox"
                checked={selected.has(sp.id)}
                onChange={() => toggleSpecies(sp.id)}
                className="h-3 w-3 accent-emerald-600"
              />
              <span className={sp.kind === "TREE" ? "text-emerald-400" : "text-lime-400"}>
                {sp.kind === "TREE" ? "◉" : "◎"}
              </span>
              <span className="min-w-0 flex-1 truncate" title={sp.displayNameRu}>{sp.displayName}</span>
              <span className="font-mono text-2xs text-ink-500">r{sp.parameters.crownRadiusM}</span>
              {sp.regulatoryStatus === "unknown" && (
                <span className="text-2xs text-amber-400" title="Regulatory status unknown">?</span>
              )}
            </label>
          ))}
        </div>
      )}

      <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1.5">
        <label className="col-span-2 text-2xs text-ink-400">
          Strategy
          <Select
            value={strategy}
            onChange={(v) => setStrategy(v as Strategy)}
            options={STRATEGIES}
            className="mt-0.5"
          />
        </label>
        <label className="text-2xs text-ink-400">
          Target count
          <TextInput value={targetCount} onChange={setTargetCount} placeholder="Auto" className="mt-0.5" />
        </label>
        <label className="text-2xs text-ink-400">
          Seed
          <TextInput value={seed} onChange={setSeed} className="mt-0.5" />
        </label>
        <label className="text-2xs text-ink-400">
          Max iterations
          <TextInput value={maxIterations} onChange={setMaxIterations} className="mt-0.5" />
        </label>
        <div className="flex items-end pb-1">
          <Checkbox
            checked={useLlmJudge}
            onChange={setUseLlmJudge}
            label={
              <span>
                LLM judge
                {capabilities && (!capabilities.localLlm.enabled || !capabilities.localLlm.reachable) && (
                  <span className="ml-1 text-ink-500" title="Local LLM unreachable — heuristic fallback will be used">
                    (fallback)
                  </span>
                )}
              </span>
            }
          />
        </div>
      </div>

      <Btn
        variant="primary"
        className="mt-2.5 h-8 w-full text-[13px]"
        disabled={!canGenerate}
        onClick={submit}
        title={chosen.length === 0 ? "Select at least one species" : undefined}
      >
        {runActive ? <><Spinner /> Generating…</> : "Generate planting plan"}
      </Btn>

      {runError && (
        <div className="mt-2 border border-red-800 bg-red-950/50 p-1.5 text-2xs text-red-300">{runError}</div>
      )}
      <StageChecklist />
    </Section>
  );
}
