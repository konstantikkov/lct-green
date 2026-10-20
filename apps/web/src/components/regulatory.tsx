import type {
  CitedSource, RegulatoryRule, RegulatoryStatus, RegulatoryVerdict, RuleRole,
} from "../api/types";
import { normalizeCheckResult } from "../api/types";
import { Badge } from "./ui";
import { genusList, plural } from "../labels";

/**
 * Shared presentation of the DETERMINISTIC regulatory verdict (iteration 5).
 * This is fully separate from the LLM judge decision, which only means
 * "is another optimization iteration useful?" and never affects legality.
 */

const STATUS_PRESENTATION: Record<RegulatoryStatus, {
  tone: "green" | "amber" | "red";
  label: string;
  title: string;
}> = {
  COMPLIANT: {
    tone: "green",
    label: "СООТВЕТСТВУЕТ НОРМАМ",
    title: "Все применимые жёсткие проверки пройдены по верифицированным правилам",
  },
  NEEDS_REVIEW: {
    tone: "amber",
    label: "ТРЕБУЕТ ПРОВЕРКИ",
    title: "Нарушений нет, но часть проверок выполнена по неверифицированным (демо) значениям или есть блокеры",
  },
  NON_COMPLIANT: {
    tone: "red",
    label: "НЕ СООТВЕТСТВУЕТ",
    title: "Есть нарушения действующих правил (детерминированная проверка)",
  },
};

/** Human-readable Russian label for a machine blocker code. */
export function blockerLabel(blocker: string): string {
  if (blocker === "SITE_BOUNDARY_UNKNOWN") {
    return "Граница участка не определена (использованы границы чертежа)";
  }
  const unverified = /^UNVERIFIED_HARD_RULES:(\d+)$/.exec(blocker);
  if (unverified) {
    // A rule lands here when ANY of its checks is UNVERIFIED: a demo value,
    // a safe buffer, or an unconfirmed applicability condition (crown > 5 m
    // for СП 42 табл. 9.1) — so do not claim they are all demo values.
    const n = Number(unverified[1]);
    return `${n} ${plural(n, "правило не подтверждено", "правила не подтверждены", "правил не подтверждено")} для этого плана — ДЕМО-значения или не выполнено условие применимости (например, крона шире 5 м)`;
  }
  return blocker;
}

/** Main regulatory status chip. Missing verdict (old iterations) → amber
 *  "статус не рассчитан". Always carries data-testid="regulatory-status". */
export function RegulatoryStatusChip({ verdict }: { verdict: RegulatoryVerdict | null }) {
  if (!verdict) {
    return (
      <Badge
        wrap
        tone="amber"
        data-testid="regulatory-status"
        title="Итерация создана до внедрения детерминированной проверки соответствия"
      >
        Соответствие: статус не рассчитан (старая генерация — перегенерируйте)
      </Badge>
    );
  }
  const p = STATUS_PRESENTATION[verdict.status];
  return (
    <Badge wrap tone={p.tone} data-testid="regulatory-status" title={p.title}>
      {p.label}
    </Badge>
  );
}

/**
 * Prominent verdict block: status chip + regulatory coverage + blockers.
 * Used at the TOP of QualityCard, wizard step 4 and the audit header.
 */
export function RegulatoryVerdictBlock({ verdict }: { verdict: RegulatoryVerdict | null }) {
  return (
    <div data-testid="regulatory-verdict">
      <RegulatoryStatusChip verdict={verdict} />
      {verdict && (
        <>
          <div className="mt-1 text-2xs leading-relaxed text-ink-400">
            Нормативное покрытие:{" "}
            <span className="font-mono text-ink-200">
              {Math.round(verdict.coverage.regulatoryCoverage * 100)}%
            </span>{" "}
            проверок по верифицированным правилам
          </div>
          {verdict.blockers.length > 0 && (
            <div className="mt-0.5 space-y-0.5">
              {verdict.blockers.map((b) => (
                <div key={b} className="text-2xs leading-relaxed text-amber-700 dark:text-amber-300" title={b}>
                  • {blockerLabel(b)}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Per-check badge covering the 4 new statuses AND legacy lowercase values. */
export function CheckResultBadge({ result }: { result: string }) {
  const r = normalizeCheckResult(result);
  if (r === "PASS") return <Badge tone="green" title="Проверка пройдена по верифицированному правилу">ВЫПОЛНЕНО</Badge>;
  if (r === "FAIL") return <Badge tone="red" title="Нарушение действующего правила">НАРУШЕНИЕ</Badge>;
  if (r === "UNVERIFIED") {
    return (
      <Badge tone="amber" title="Геометрия удовлетворяет правилу, но значение правила — непроверенное ДЕМО-допущение">
        НЕ ВЕРИФИЦИРОВАНО
      </Badge>
    );
  }
  return <Badge tone="grey" title="Не применимо: такого препятствия нет на чертеже">Н/П</Badge>;
}

/** Muted role badge: safe_buffer / design_heuristic are NOT legal norms.
 *  role === "regulatory" (or absent on old payloads) renders nothing. */
export function RuleRoleBadge({ role }: { role?: RuleRole }) {
  if (role === "safe_buffer") {
    return <Badge tone="grey" title="Технологический отступ — не юридическая норма">технологический отступ</Badge>;
  }
  if (role === "design_heuristic") {
    return <Badge tone="grey" title="Проектная эвристика — не юридическая норма">эвристика</Badge>;
  }
  return null;
}

/** Mini-note for conditional rules: crown limit (СП 42 табл. 9.1), crossing
 *  type and speed (623-ПП п. 9.4.2), genus restriction (623-ПП п. 4.2.8). */
export function ruleConditionNote(
  rule: Pick<RegulatoryRule, "condition"> & { appliesTo?: RegulatoryRule["appliesTo"] },
): string | null {
  const parts: string[] = [];
  const c = rule.condition;
  if (c?.crownDiameterMaxM != null) parts.push(`крона ≤ ${c.crownDiameterMaxM} м`);
  if (c?.unsignalizedCrossing) parts.push("нерегулируемый перекрёсток");
  if (c?.speedLimitKmhMax != null) parts.push(`до ${c.speedLimitKmhMax} км/ч`);
  const genera = rule.appliesTo?.speciesGenus;
  if (genera?.length) parts.push(`только: ${genusList(genera)}`);
  return parts.length ? parts.join(", ") : null;
}

/** "ППМ № 743-ПП · п. 3.6.3, таблица 3.6.1; ППМ № 623-ПП · п. 4.2.4" */
export function alsoCitedText(list: Pick<CitedSource, "document" | "clause">[] | null | undefined): string | null {
  if (!list?.length) return null;
  return list.map((s) => (s.clause ? `${s.document} · ${s.clause}` : s.document)).join("; ");
}

const RELATION_HINTS: Record<string, string> = {
  same_value: "устанавливает то же значение",
  reference: "требует норму отсылкой к таблице МГСН 1.01-99",
};

/** Tooltip: one line per act with how it relates to the main source. */
export function alsoCitedTitle(list: Pick<CitedSource, "document" | "clause" | "relation">[]): string {
  return list
    .map((s) => `${s.document}${s.clause ? ` · ${s.clause}` : ""}`
      + (s.relation && RELATION_HINTS[s.relation] ? ` — ${RELATION_HINTS[s.relation]}` : ""))
    .join("\n");
}
