import { useState } from "react";
import { normalizeCheckResult, parseRegulatoryVerdict } from "../../api/types";
import type { ComplianceAudit, CompliancePlacement } from "../../api/types";
import { loadAudit, selectAuditPlacement, setNav, setWorkspaceTab } from "../../state/actions";
import { useApp } from "../../state/store";
import { RegulatoryVerdictBlock } from "../regulatory";
import { Badge, Btn, Checkbox, Spinner } from "../ui";

const PAGE = 500;

function SourceBadge({ source }: { source: string }) {
  return source === "verified"
    ? <Badge tone="green" title="Подтверждено нормативным источником">ПОДТВЕРЖДЕНО</Badge>
    : <Badge tone="amber" title="Расстояние — непроверенное допущение (демо-значение)">ДОПУЩЕНИЕ</Badge>;
}

// --- Header -------------------------------------------------------------------

function AuditSummary({ audit }: { audit: ComplianceAudit }) {
  const s = audit.summary;
  const verdict = parseRegulatoryVerdict(audit.regulatory);
  return (
    <div className="rounded-sm border border-ink-600 bg-ink-900 p-3" data-testid="audit-summary">
      {/* Deterministic regulatory verdict for the audited iteration. */}
      <div className="mb-2 border-b border-ink-700 pb-2">
        <RegulatoryVerdictBlock verdict={verdict} />
        {verdict && (
          <div className="mt-1 font-mono text-2xs text-ink-500">
            {verdict.checkCounts.pass} PASS · {verdict.checkCounts.fail} FAIL ·{" "}
            {verdict.checkCounts.unverified} UNVERIFIED · {verdict.checkCounts.notApplicable} N/A
          </div>
        )}
      </div>
      <div className="text-sm leading-relaxed text-ink-100">
        <span className="font-mono font-semibold">{s.placements}</span> посадок
        <span className="text-ink-600"> · </span>
        <span className="font-mono font-semibold text-emerald-600 dark:text-emerald-400">{s.compliant}</span> соответствуют
        <span className="text-ink-600"> · </span>
        <span className={`font-mono font-semibold ${s.withViolations > 0 ? "text-red-600 dark:text-red-400" : "text-ink-100"}`}>{s.withViolations}</span> с нарушениями
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Badge tone="grey" title="Версия реестра правил">реестр v{audit.rulesetVersion}</Badge>
        <Badge tone="blue" title="Действующие правила REG_*, извлечённые из загруженных документов">
          {audit.documentDerivedRules} правил из документов
        </Badge>
        <Badge tone="green" title="Правила, подтверждённые нормативным источником">{audit.verifiedRules} подтверждённых</Badge>
        <Badge tone="amber" title="Правила, применённые как непроверенные допущения">{audit.assumedRules} по допущениям</Badge>
        <span className="font-mono text-2xs text-ink-500">{s.checksTotal} проверок</span>
      </div>
    </div>
  );
}

// --- По документам ---------------------------------------------------------------

function ByDocumentTable({ audit }: { audit: ComplianceAudit }) {
  const entries = Object.entries(audit.byDocument);
  if (entries.length === 0) return null;
  return (
    <section>
      <h2 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">По документам</h2>
      <div className="overflow-x-auto rounded-sm border border-ink-600 bg-ink-900">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-ink-600 text-2xs uppercase tracking-wider text-ink-500">
              <th className="px-2 py-1 font-medium">Документ</th>
              <th className="px-2 py-1 text-right font-medium">Проверок</th>
              <th className="px-2 py-1 text-right font-medium">Пройдено</th>
              <th className="px-2 py-1 text-right font-medium" title="Проверки по неверифицированным (демо) значениям">Не вериф.</th>
              <th className="px-2 py-1 text-right font-medium">Нарушений</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([doc, st]) => (
              <tr key={doc} className="border-b border-ink-700/60 last:border-0">
                <td className="max-w-[380px] truncate px-2 py-1 text-2xs text-ink-200" title={doc}>{doc}</td>
                <td className="px-2 py-1 text-right font-mono text-2xs text-ink-300">{st.checks}</td>
                <td className="px-2 py-1 text-right font-mono text-2xs text-ink-300">{st.passed}</td>
                <td className={`px-2 py-1 text-right font-mono text-2xs ${
                  (st.unverified ?? 0) > 0 ? "text-amber-600 dark:text-amber-400" : "text-ink-500"
                }`}>
                  {st.unverified ?? "—"}
                </td>
                <td className={`px-2 py-1 text-right font-mono text-2xs ${
                  st.failed > 0 ? "font-semibold text-red-600 dark:text-red-400" : "text-ink-500"
                }`}>
                  {st.failed}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// --- По правилам -------------------------------------------------------------------

function RuleRow({ ruleId, audit }: { ruleId: string; audit: ComplianceAudit }) {
  const [expanded, setExpanded] = useState(false);
  const st = audit.byRule[ruleId];
  return (
    <>
      <tr
        className="cursor-pointer border-b border-ink-700/60 last:border-0 hover:bg-ink-850"
        onClick={() => setExpanded((x) => !x)}
      >
        <td className="px-2 py-1">
          <span className="inline-block w-3 text-2xs text-ink-500">{expanded ? "▾" : "▸"}</span>
          <span className="font-mono text-2xs text-ink-200">{ruleId}</span>
        </td>
        <td className="px-2 py-1 text-right font-mono text-2xs text-ink-300">
          {st.requiredM != null ? `${st.requiredM} м` : "—"}
        </td>
        <td className="px-2 py-1"><SourceBadge source={st.effectiveSource} /></td>
        <td className="px-2 py-1 text-right font-mono text-2xs text-ink-300">{st.passed}</td>
        <td className={`px-2 py-1 text-right font-mono text-2xs ${
          (st.unverified ?? 0) > 0 ? "text-amber-600 dark:text-amber-400" : "text-ink-500"
        }`}>
          {st.unverified ?? "—"}
        </td>
        <td className={`px-2 py-1 text-right font-mono text-2xs ${
          st.failed > 0 ? "font-semibold text-red-600 dark:text-red-400" : "text-ink-500"
        }`}>
          {st.failed}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-ink-700/60 bg-ink-950/50 last:border-0">
          <td colSpan={6} className="px-2 py-1.5 pl-7 text-2xs text-ink-400">
            <div>Документ: <span className="text-ink-200">{st.document}</span></div>
            <div className="mt-0.5 font-mono text-ink-500">
              {st.checks} проверок · {st.passed} пройдено
              {st.unverified != null ? ` · ${st.unverified} не вериф.` : ""} · {st.failed} нарушений
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ByRuleTable({ audit }: { audit: ComplianceAudit }) {
  const ids = Object.keys(audit.byRule);
  if (ids.length === 0) return null;
  return (
    <section>
      <h2 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-400">По правилам</h2>
      <div className="overflow-x-auto rounded-sm border border-ink-600 bg-ink-900">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-ink-600 text-2xs uppercase tracking-wider text-ink-500">
              <th className="px-2 py-1 font-medium">Правило</th>
              <th className="px-2 py-1 text-right font-medium">Мин. дист.</th>
              <th className="px-2 py-1 font-medium">Источник</th>
              <th className="px-2 py-1 text-right font-medium">Пройдено</th>
              <th className="px-2 py-1 text-right font-medium" title="Проверки по неверифицированным (демо) значениям">Не вериф.</th>
              <th className="px-2 py-1 text-right font-medium">Нарушений</th>
            </tr>
          </thead>
          <tbody>
            {ids.map((id) => <RuleRow key={id} ruleId={id} audit={audit} />)}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// --- Посадки -------------------------------------------------------------------------

function PlacementRow({ p }: { p: CompliancePlacement }) {
  const species = useApp((s) => s.species);
  const sp = species?.find((x) => x.id === p.speciesId);
  const bad = p.verdict === "VIOLATIONS";
  const unverified = p.checks.filter((c) => normalizeCheckResult(c.result) === "UNVERIFIED").length;
  return (
    <div
      data-testid="audit-row"
      className="flex cursor-pointer items-center gap-2 border-b border-ink-700/60 px-2 py-1 last:border-0 hover:bg-ink-850"
      onClick={() => void selectAuditPlacement(p.id)}
      title="Показать на холсте и открыть инспектор"
    >
      <span className="min-w-0 flex-1 truncate text-xs text-ink-200">
        {sp?.displayNameRu ?? p.speciesId}
        <span className="ml-1.5 font-mono text-2xs text-ink-500">{p.id}</span>
      </span>
      <span className="hidden whitespace-nowrap font-mono text-2xs text-ink-400 sm:inline">
        {p.position[0].toFixed(1)}, {p.position[1].toFixed(1)} м
      </span>
      {bad && p.failingRules.length > 0 && (
        <span
          className="hidden max-w-[220px] truncate font-mono text-2xs text-red-600 dark:text-red-400 lg:inline"
          title={p.failingRules.join(", ")}
        >
          {p.failingRules.join(" ")}
        </span>
      )}
      {unverified > 0 && (
        <span
          className="whitespace-nowrap font-mono text-2xs text-amber-600 dark:text-amber-400"
          title="Проверки по неверифицированным (демо) значениям"
        >
          {unverified} не вериф.
        </span>
      )}
      <Badge tone={bad ? "red" : "green"}>{p.verdict}</Badge>
    </div>
  );
}

function PlacementsList({ audit }: { audit: ComplianceAudit }) {
  const [violationsOnly, setViolationsOnly] = useState(false);
  const [limit, setLimit] = useState(PAGE);

  const rows = violationsOnly
    ? audit.placements.filter((p) => p.verdict === "VIOLATIONS")
    : audit.placements;
  const visible = rows.slice(0, limit);

  return (
    <section>
      <div className="mb-1 flex items-center justify-between gap-2">
        <h2 className="text-2xs font-semibold uppercase tracking-wider text-ink-400">
          Посадки <span className="font-mono text-ink-500">({rows.length})</span>
        </h2>
        <Checkbox
          checked={violationsOnly}
          onChange={(v) => { setViolationsOnly(v); setLimit(PAGE); }}
          label={<span className="text-2xs">только с нарушениями</span>}
        />
      </div>
      <div className="rounded-sm border border-ink-600 bg-ink-900">
        {visible.map((p) => <PlacementRow key={p.id} p={p} />)}
        {rows.length === 0 && (
          <div className="px-2 py-2 text-2xs text-ink-500">
            {violationsOnly ? "Нарушений нет — все посадки соответствуют нормам." : "Посадок нет."}
          </div>
        )}
      </div>
      {rows.length > limit && (
        <button
          className="mt-1.5 text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
          onClick={() => setLimit((l) => l + PAGE)}
        >
          Показать ещё {Math.min(PAGE, rows.length - limit)} (из {rows.length})
        </button>
      )}
    </section>
  );
}

// --- View -----------------------------------------------------------------------------

/** Cross-link to the regulations upload/extract workflow. */
function RegulationsCrossLink() {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-sm border border-ink-600 bg-ink-900 px-2.5 py-2 text-2xs leading-relaxed text-ink-400">
      <span className="min-w-0 flex-1">
        Загрузите новые нормативные документы (PDF/DOCX) в разделе «Нормативы» —
        активированные правила автоматически участвуют в аудите.
      </span>
      <Btn onClick={() => setNav("regulations")} title="Открыть раздел нормативов">
        Нормативы →
      </Btn>
    </div>
  );
}

/** Dedicated full "Аудит соответствия нормам" (законность) view. */
export function AuditView() {
  const audit = useApp((s) => s.audit);
  const status = useApp((s) => s.auditStatus);
  const error = useApp((s) => s.auditError);

  return (
    <div className="h-full w-full overflow-y-auto bg-ink-950" data-testid="audit-view">
      <div className="mx-auto flex max-w-4xl flex-col gap-4 px-4 py-5">
        <div className="flex items-center gap-3">
          <button
            className="text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
            onClick={() => { setNav("plan"); setWorkspaceTab("result"); }}
          >
            ← Результат
          </button>
          <h1 className="text-sm font-semibold uppercase tracking-wider text-ink-200">
            Аудит соответствия нормам
          </h1>
        </div>

        {status === "loading" && !audit && (
          <div className="flex items-center gap-2 py-4 text-2xs text-ink-500">
            <Spinner /> проверка всех посадок по действующим нормам…
          </div>
        )}

        {status === "no_result" && (
          <div className="py-2 text-xs text-ink-400">
            Сначала сгенерируйте план.{" "}
            <button
              className="underline decoration-dotted hover:text-ink-200"
              onClick={() => { setNav("plan"); setWorkspaceTab("generate"); }}
            >
              К генерации →
            </button>
          </div>
        )}

        {status === "error" && (
          <div className="border border-red-300 bg-red-50 p-2 text-2xs text-red-700 dark:border-red-800 dark:bg-red-950/50 dark:text-red-300">
            {error}
            <button
              className="ml-1.5 underline decoration-dotted hover:no-underline"
              onClick={() => void loadAudit()}
            >
              Повторить
            </button>
          </div>
        )}

        {audit && status === "loaded" && (
          <>
            <AuditSummary audit={audit} />
            <RegulationsCrossLink />
            <ByDocumentTable audit={audit} />
            <ByRuleTable audit={audit} />
            <PlacementsList audit={audit} />
          </>
        )}
      </div>
    </div>
  );
}
