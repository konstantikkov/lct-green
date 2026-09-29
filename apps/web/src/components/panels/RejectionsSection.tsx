import { useState } from "react";
import type { RejectionReason } from "../../api/types";
import { setOverlay } from "../../state/actions";
import { useApp } from "../../state/store";
import { categoryLabel, plural } from "../../labels";

const GEOMETRIC: Record<string, string> = {
  OUTSIDE_SITE: "вне границы участка",
  NOT_PLANTING_AREA: "вне области посадки (не проектный газон / не зона озеленения)",
  NARROW_STRIP: "узкая полоса меньше 1 м между запретными зонами",
};

/** "Почему здесь нельзя сажать" — rejected candidates grouped by reason, each
 *  rule reason with its norm (ТЗ п. 8: причина отклонения со ссылкой на НПА). */
export function RejectionsSection() {
  const rej = useApp((s) => s.activeIteration?.rejections);
  const rules = useApp((s) => s.rules);
  const shown = useApp((s) => s.overlays.rejectedCandidates);
  const [all, setAll] = useState(false);
  if (!rej || rej.total === 0) return null;

  const ruleById = new Map((rules ?? []).map((r) => [r.id, r]));
  const describe = (item: RejectionReason) => {
    const k = item.key;
    if (k.startsWith("ON_OBJECT:")) return { title: `на объекте: ${categoryLabel(k.slice(10)).toLowerCase()}`, norm: null };
    if (GEOMETRIC[k]) return { title: GEOMETRIC[k], norm: null };
    const r = ruleById.get(k);
    if (!r) return { title: k, norm: null };
    const dist = r.effectiveDistanceM != null ? ` — ${r.effectiveDistanceM} м` : "";
    // safe buffers and heuristics are the project's own choices, not norms
    const norm = r.role === "safe_buffer"
      ? "технологический буфер проекта — не норма"
      : r.role === "design_heuristic"
        ? "проектная рекомендация — не норма"
        : r.source.clause
          ? `${r.source.document} · ${r.source.clause}`
          : `${r.source.document} (пункт не подтверждён)`;
    return { title: `${r.source.title}${dist}`, norm };
  };
  const list = rej.byReason.filter((r) => r.count > 0);
  const visible = all ? list : list.slice(0, 6);

  return (
    <div className="border-b border-ink-600 px-2 py-2" data-testid="rejections">
      <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-300">
        Почему здесь нельзя сажать
      </div>
      <p className="mb-1.5 text-2xs leading-relaxed text-ink-400">
        Отклонено {rej.total.toLocaleString()} {plural(rej.total, "точка-кандидат", "точки-кандидата", "точек-кандидатов")}.
        Главная причина каждой точки и норма, которая её запрещает:
      </p>
      <div className="space-y-1">
        {visible.map((item) => {
          const d = describe(item);
          return (
            <div key={item.key} className="text-2xs leading-snug" title={item.key}>
              <div className="flex gap-1.5">
                <span className="w-12 shrink-0 text-right font-mono text-ink-300">{item.count.toLocaleString()}</span>
                <span className="min-w-0 flex-1 text-ink-200">{d.title}</span>
              </div>
              {(d.norm || item.involved > item.count) && (
                <div className="ml-[3.375rem] text-ink-500">
                  {d.norm}
                  {d.norm && item.involved > item.count ? " · " : ""}
                  {item.involved > item.count ? `ещё в ${(item.involved - item.count).toLocaleString()} как доп. причина` : ""}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex gap-3 text-2xs">
        {list.length > 6 && (
          <button className="text-ink-400 underline decoration-dotted hover:text-ink-200" onClick={() => setAll(!all)}>
            {all ? "Свернуть" : `Все причины (${list.length})`}
          </button>
        )}
        <button
          className="text-ink-400 underline decoration-dotted hover:text-ink-200"
          onClick={() => setOverlay("rejectedCandidates", !shown)}
          data-testid="toggle-rejected"
        >
          {shown ? "Скрыть точки на плане" : "Показать точки на плане"}
        </button>
      </div>
    </div>
  );
}
