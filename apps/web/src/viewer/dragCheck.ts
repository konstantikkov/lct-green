import { api } from "../api/client";
import { normalizeCheckResult } from "../api/types";
import type { LiveCheck, PlacementLiveCheck, PlantKind } from "../api/types";
import { categoryLabel, plural } from "../labels";

/**
 * Live regulatory check while dragging a planting (iteration 6).
 *
 * DragChecker throttles GET /placements/check to ~4/s: single in-flight
 * request, the latest coordinates are kept as a trailing call, and responses
 * arriving after reset() (drag ended) or out of order are dropped.
 */

const MIN_INTERVAL_MS = 250; // ~4 calls per second

export interface DragCheckParams {
  projectId: string;
  x: number;
  y: number;
  kind: PlantKind;
  speciesId: string;
  excludeId: string;
}

export class DragChecker {
  private pending: { p: DragCheckParams; cb: (r: PlacementLiveCheck) => void } | null = null;
  private inFlight = false;
  private lastStart = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private seq = 0;
  private lastApplied = 0;
  private epoch = 0;

  request(p: DragCheckParams, cb: (r: PlacementLiveCheck) => void): void {
    this.pending = { p, cb };
    this.pump();
  }

  /** Drag ended/cancelled: drop the trailing call and in-flight responses. */
  reset(): void {
    this.pending = null;
    this.epoch++;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private pump(): void {
    if (this.inFlight || !this.pending) return;
    const wait = this.lastStart + MIN_INTERVAL_MS - Date.now();
    if (wait > 0) {
      if (!this.timer) {
        this.timer = setTimeout(() => {
          this.timer = null;
          this.pump();
        }, wait);
      }
      return;
    }
    const { p, cb } = this.pending;
    this.pending = null;
    const mySeq = ++this.seq;
    const myEpoch = this.epoch;
    this.inFlight = true;
    this.lastStart = Date.now();
    api.checkPlacementPosition(p.projectId, p)
      .then((res) => {
        if (myEpoch === this.epoch && mySeq > this.lastApplied) {
          this.lastApplied = mySeq;
          cb(res);
        }
      })
      .catch(() => {
        /* transient live-check errors during drag are non-fatal */
      })
      .finally(() => {
        this.inFlight = false;
        this.pump(); // trailing call with the latest coordinates
      });
  }
}

// ---------------------------------------------------------------------------
// Annotation HTML (written straight to the DOM, like the hover tooltip)
// ---------------------------------------------------------------------------

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const VERDICT_UI = {
  OK: { text: "Позиция допустима", cls: "text-emerald-600 dark:text-emerald-400" },
  UNVERIFIED: { text: "Не подтверждено", cls: "text-amber-600 dark:text-amber-400" },
  VIOLATIONS: { text: "Нарушения", cls: "text-red-600 dark:text-red-400" },
} as const;

const RED = "text-red-600 dark:text-red-400";
const AMBER = "text-amber-600 dark:text-amber-400";
const GREEN = "text-emerald-600 dark:text-emerald-400";

/** "Здание: 3.2 м ≥ 5.0 м — СП 42.13330, табл. 9.1" */
function checkLine(c: LiveCheck, mark: string, markCls: string): string {
  const name = categoryLabel(c.obstacleCategory) || c.obstacleCategory;
  const nums = c.actualM != null && c.requiredM != null
    ? `${c.actualM.toFixed(1)} м ${esc(c.comparator)} ${c.requiredM.toFixed(1)} м`
    : "нет данных";
  const src = `${c.source.document}${c.source.clause ? `, ${c.source.clause}` : ""}`;
  return (
    `<div class="flex items-start gap-1.5">` +
    `<span class="shrink-0 ${markCls}">${mark}</span>` +
    `<span class="min-w-0 flex-1 leading-snug">${esc(name)}: ${nums}` +
    ` <span class="text-ink-500">— ${esc(src)}</span></span></div>`
  );
}

/**
 * Floating drag annotation: verdict header, then every FAIL, then UNVERIFIED
 * (capped), then the 3 tightest PASS margins. `null` = first check pending.
 */
export function dragAnnotationHtml(r: PlacementLiveCheck | null): string {
  if (!r) {
    return (
      `<div class="text-xs font-semibold text-ink-300">Проверка…</div>` +
      `<div class="mt-1 text-2xs text-ink-500">Esc — отменить перемещение</div>`
    );
  }
  const v = VERDICT_UI[r.verdict] ?? VERDICT_UI.UNVERIFIED;
  const rows: string[] = [
    `<div class="mb-1 text-xs font-semibold ${v.cls}">${v.text}</div>`,
  ];
  if (!r.insideAllowed) {
    rows.push(
      `<div class="flex items-start gap-1.5"><span class="shrink-0 ${RED}">✕</span>` +
      `<span class="leading-snug ${RED}">Вне допустимой зоны посадки</span></div>`,
    );
  }
  const sp = r.spacing;
  if (sp && sp.result !== "PASS") {
    const cls = sp.result === "FAIL" ? RED : AMBER;
    const mark = sp.result === "FAIL" ? "✕" : "!";
    const nums = sp.nearestM != null
      ? `${sp.nearestM.toFixed(1)} м ≥ ${sp.requiredM.toFixed(1)} м`
      : `требуется ≥ ${sp.requiredM.toFixed(1)} м`;
    rows.push(
      `<div class="flex items-start gap-1.5"><span class="shrink-0 ${cls}">${mark}</span>` +
      `<span class="min-w-0 flex-1 leading-snug">Шаг посадки: ${nums}` +
      ` <span class="text-ink-500">— ${esc(sp.ruleId)}</span></span></div>`,
    );
  }

  const fails: LiveCheck[] = [];
  const unverified: LiveCheck[] = [];
  const passes: LiveCheck[] = [];
  for (const c of r.checks) {
    const res = normalizeCheckResult(c.result);
    if (res === "FAIL") fails.push(c);
    else if (res === "UNVERIFIED") unverified.push(c);
    else if (res === "PASS" && c.actualM != null && c.requiredM != null) passes.push(c);
  }
  for (const c of fails) rows.push(checkLine(c, "✕", RED));
  const UNVERIFIED_CAP = 4;
  for (const c of unverified.slice(0, UNVERIFIED_CAP)) rows.push(checkLine(c, "!", AMBER));
  const restUnverified = unverified.length - UNVERIFIED_CAP;
  if (restUnverified > 0) {
    rows.push(
      `<div class="text-2xs ${AMBER}">ещё ${restUnverified} ` +
      `${plural(restUnverified, "непроверенное правило", "непроверенных правила", "непроверенных правил")}</div>`,
    );
  }
  // The 3 tightest PASS margins — where the position is closest to a limit.
  passes.sort((a, b) => (a.actualM! - a.requiredM!) - (b.actualM! - b.requiredM!));
  for (const c of passes.slice(0, 3)) rows.push(checkLine(c, "✓", GREEN));

  rows.push(`<div class="mt-1 text-2xs text-ink-500">Esc — отменить перемещение</div>`);
  return rows.join("");
}
