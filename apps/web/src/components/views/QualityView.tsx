import { useEffect, useState } from "react";
import { Spinner } from "../ui";

type FetchState =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "loaded"; data: unknown };

/**
 * Developer-facing eval results viewer. Tries the statically served
 * eval-results output of `make eval`; renders nothing fabricated when absent.
 */
export function QualityView() {
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const path of ["/eval-results/results.json", "/eval-results"]) {
        try {
          const res = await fetch(path, { headers: { Accept: "application/json" } });
          if (!res.ok) continue;
          const ct = res.headers.get("content-type") ?? "";
          if (!ct.includes("json")) continue;
          const data: unknown = await res.json();
          if (!cancelled) setState({ kind: "loaded", data });
          return;
        } catch {
          /* try next path */
        }
      }
      if (!cancelled) setState({ kind: "missing" });
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="h-full w-full overflow-y-auto bg-ink-950" data-testid="quality-view">
      <div className="mx-auto max-w-3xl px-4 py-6">
        <h1 className="text-sm font-semibold uppercase tracking-wider text-ink-200">Quality — evaluation results</h1>
        <p className="mt-2 text-xs leading-relaxed text-ink-400">
          Developer-facing view of the offline eval &amp; regression pipeline
          (deterministic export/geometry checks, golden dataset cases, and the
          optional GPT evaluator). Results are produced by <span className="font-mono">make eval</span>{" "}
          and written to <span className="font-mono">eval-results/&lt;timestamp&gt;/results.json</span>.
          See <span className="font-mono">docs/improvement-plan.md</span> (WS3) and{" "}
          <span className="font-mono">evals/</span> in the repository.
        </p>

        <div className="mt-4">
          {state.kind === "loading" && (
            <div className="flex items-center gap-2 text-xs text-ink-500"><Spinner /> checking for results…</div>
          )}
          {state.kind === "missing" && (
            <div className="border border-ink-600 bg-ink-900 p-3 text-xs text-ink-400">
              No evaluation results. Run <span className="font-mono text-ink-200">make eval</span>.
            </div>
          )}
          {state.kind === "loaded" && (
            <pre className="max-h-[70vh] overflow-auto border border-ink-600 bg-ink-900 p-3 font-mono text-2xs leading-relaxed text-ink-300">
              {JSON.stringify(state.data, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
