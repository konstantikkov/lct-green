import { setOverlay } from "../../state/actions";
import { useApp } from "../../state/store";

/** "Газон" — the recommended lawn (ТЗ §2 п. 3 «травянистые покрытия»): area,
 *  why it goes there and the norm behind it, with a plan toggle. */
export function LawnSection() {
  const lawn = useApp((s) => s.activeIteration?.lawn);
  const rules = useApp((s) => s.rules);
  const shown = useApp((s) => s.overlays.lawn);
  if (!lawn || lawn.areaM2 <= 0) return null;

  const rule = (rules ?? []).find((r) => r.id === lawn.ruleId);
  const norms = rule
    ? [`${rule.source.document} · ${rule.source.clause}`,
       ...(rule.alsoCited ?? []).map((c) => `${c.document} · ${c.clause}`)]
    : [];

  return (
    <div className="border-b border-ink-600 px-2 py-2" data-testid="lawn">
      <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-300">Газон</div>
      <div className="mb-1 text-xs text-ink-100">
        {Math.round(lawn.areaM2).toLocaleString()} м²
        <span className="text-ink-400"> · {lawn.polygonCount.toLocaleString()} уч.</span>
      </div>
      <p className="mb-1 text-2xs leading-relaxed text-ink-400">{lawn.explanation}</p>
      {norms.length > 0 && (
        <div className="mb-1 space-y-0.5 text-2xs text-ink-500">
          {norms.map((n) => <div key={n}>{n}</div>)}
        </div>
      )}
      <button
        className="text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
        onClick={() => setOverlay("lawn", !shown)}
        data-testid="toggle-lawn"
      >
        {shown ? "Скрыть газон на плане" : "Показать газон на плане"}
      </button>
    </div>
  );
}
