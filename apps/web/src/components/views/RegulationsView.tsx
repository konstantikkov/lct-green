import { useEffect, useRef, useState } from "react";
import type {
  RegDocument, RegulatoryRule, RuleCandidate, RuleCandidateStatus,
} from "../../api/types";
import {
  activateRegCandidate, deactivateRegCandidate, ensureRegulationsLoaded,
  extractRegDocument, reviewRegCandidate, selectRegCandidate, selectRegDocument,
  setRegFilter, uploadRegDocument, verifyRegCandidate,
} from "../../state/regulations";
import { useApp } from "../../state/store";
import { RuleRoleBadge, ruleConditionNote } from "../regulatory";
import { Badge, Btn, Spinner, TextInput } from "../ui";

// --- Status presentation -----------------------------------------------------

const STATUS_LABEL: Record<RuleCandidateStatus, string> = {
  EXTRACTED: "Extracted",
  NEEDS_REVIEW: "Need review",
  VERIFIED: "Verified",
  ACTIVE: "Active",
  DEPRECATED: "Deprecated",
  REJECTED: "Rejected",
};

const STATUS_TONE: Record<RuleCandidateStatus, "grey" | "green" | "amber" | "red" | "blue"> = {
  EXTRACTED: "grey",
  NEEDS_REVIEW: "amber",
  VERIFIED: "green",
  ACTIVE: "green",
  DEPRECATED: "grey",
  REJECTED: "red",
};

const FILTERS: (RuleCandidateStatus | "ALL")[] =
  ["ALL", "NEEDS_REVIEW", "EXTRACTED", "VERIFIED", "ACTIVE", "DEPRECATED", "REJECTED"];

function CountChips({ doc }: { doc: RegDocument }) {
  const order: RuleCandidateStatus[] = ["VERIFIED", "ACTIVE", "NEEDS_REVIEW", "EXTRACTED", "DEPRECATED", "REJECTED"];
  const chips = order
    .map((s) => ({ status: s, count: doc.candidateCounts?.[s] ?? 0 }))
    .filter((c) => c.count > 0);
  if (chips.length === 0) return <span className="text-2xs text-ink-500">no rule candidates yet</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {chips.map((c) => (
        <Badge key={c.status} tone={STATUS_TONE[c.status]}>
          {c.count} {STATUS_LABEL[c.status]}
        </Badge>
      ))}
    </div>
  );
}

// --- Documents column ----------------------------------------------------------

function DocumentsColumn() {
  const docs = useApp((s) => s.regDocs);
  const docsError = useApp((s) => s.regDocsError);
  const uploadBusy = useApp((s) => s.regUploadBusy);
  const selectedId = useApp((s) => s.regSelectedDocId);
  const extractingId = useApp((s) => s.regExtractingDocId);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="flex h-full w-72 shrink-0 flex-col border-r border-ink-600 bg-ink-900">
      <div className="border-b border-ink-600 p-2">
        <Btn
          variant="primary"
          className="w-full"
          disabled={uploadBusy}
          onClick={() => inputRef.current?.click()}
          data-testid="reg-upload"
        >
          {uploadBusy ? <><Spinner /> Uploading…</> : "Upload document (PDF / DOCX)"}
        </Btn>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void uploadRegDocument(f);
            e.target.value = "";
          }}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {docsError && <div className="p-2 text-2xs text-red-600 dark:text-red-400">{docsError}</div>}
        {docs == null && !docsError && (
          <div className="flex items-center gap-2 p-3 text-2xs text-ink-500"><Spinner /> loading documents…</div>
        )}
        {docs != null && docs.length === 0 && (
          <div className="p-3 text-2xs leading-relaxed text-ink-500">
            No regulatory documents yet. Upload a PDF or DOCX of a normative act
            to extract rule candidates with full provenance.
          </div>
        )}
        {(docs ?? []).map((d) => (
          <button
            key={d.id}
            className={`block w-full border-b border-ink-700/60 px-2 py-2 text-left hover:bg-ink-850 ${
              d.id === selectedId ? "bg-ink-850" : ""
            }`}
            onClick={() => selectRegDocument(d.id)}
          >
            <div className="flex items-center gap-1.5">
              <span className="min-w-0 flex-1 truncate text-xs text-ink-100" title={d.fileName}>
                {d.title ?? d.fileName}
              </span>
              <Badge tone={d.status === "FAILED" ? "red" : d.status === "EXTRACTED" ? "green" : "grey"}>
                {d.status}
              </Badge>
            </div>
            <div className="mt-0.5 truncate font-mono text-2xs text-ink-500">
              {d.documentNumber ?? d.fileName}
              {d.pageCount != null ? ` · ${d.pageCount} p.` : ""} · {d.fragmentCount} fragments
            </div>
            <div className="mt-1"><CountChips doc={d} /></div>
            {(d.status === "PARSED" || d.status === "EXTRACTED") && (
              <div className="mt-1.5">
                <Btn
                  className="h-6 text-2xs"
                  disabled={extractingId != null}
                  onClick={() => void extractRegDocument(d.id)}
                >
                  {extractingId === d.id
                    ? <><Spinner className="h-2.5 w-2.5" /> Extracting…</>
                    : d.status === "EXTRACTED" ? "Re-extract rules" : "Extract rules"}
                </Btn>
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

// --- Candidate review ----------------------------------------------------------

function VerdictBadge({ c }: { c: RuleCandidate }) {
  const v = c.verification;
  if (!v || !v.verdict) return <Badge tone="grey">not verified</Badge>;
  const tone = v.verdict === "SUPPORTED" ? "green"
    : v.verdict === "UNSUPPORTED" ? "red"
    : "amber";
  return (
    <Badge tone={tone} title={v.notes ?? undefined}>
      {v.verdict}{v.numericLiteralFound ? "" : " · numeric literal NOT found"}
    </Badge>
  );
}

function EditForm({ c, onClose }: { c: RuleCandidate; onClose: () => void }) {
  const busyId = useApp((s) => s.regBusyCandidateId);
  const [value, setValue] = useState(c.constraint.value != null ? String(c.constraint.value) : "");
  const [unit, setUnit] = useState(c.constraint.unit ?? "");
  const [objectTypes, setObjectTypes] = useState(c.applicability.objectTypes.join(", "));

  const submit = async () => {
    const num = value.trim() === "" ? null : Number(value);
    await reviewRegCandidate(c.id, {
      action: "edit",
      edits: {
        constraint: {
          value: num != null && Number.isFinite(num) ? num : null,
          unit: unit.trim() === "" ? null : unit.trim(),
        },
        applicability: {
          objectTypes: objectTypes.split(",").map((s) => s.trim()).filter(Boolean),
        },
      },
    });
    onClose();
  };

  return (
    <div className="mt-2 space-y-1.5 border border-ink-600 bg-ink-850 p-2">
      <div className="grid grid-cols-2 gap-2">
        <label className="text-2xs text-ink-400">
          Value
          <TextInput value={value} onChange={setValue} className="mt-0.5" />
        </label>
        <label className="text-2xs text-ink-400">
          Unit
          <TextInput value={unit} onChange={setUnit} placeholder="m" className="mt-0.5" />
        </label>
      </div>
      <label className="block text-2xs text-ink-400">
        Object types (comma-separated)
        <TextInput value={objectTypes} onChange={setObjectTypes} className="mt-0.5" />
      </label>
      <div className="flex gap-1.5 pt-0.5">
        <Btn variant="primary" className="h-6 text-2xs" disabled={busyId === c.id} onClick={() => void submit()}>
          Save edit
        </Btn>
        <Btn variant="ghost" className="h-6 text-2xs" onClick={onClose}>Cancel</Btn>
      </div>
      <div className="text-2xs text-ink-500">Every edit bumps ruleVersion; the previous version is retained server-side.</div>
    </div>
  );
}

function CandidateReview({ c }: { c: RuleCandidate }) {
  const busyId = useApp((s) => s.regBusyCandidateId);
  const [editing, setEditing] = useState(false);
  const busy = busyId === c.id;

  const canApprove = c.status === "NEEDS_REVIEW" || c.status === "EXTRACTED" || c.status === "VERIFIED";
  const numericBlocked =
    c.constraint.value != null && c.verification != null && !c.verification.numericLiteralFound;
  const canActivate = c.status === "VERIFIED" && !numericBlocked;

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-2">
      {/* Left: source provenance */}
      <div>
        <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Source</div>
        <div className="border border-ink-600 bg-ink-900 p-2">
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-2xs text-ink-400">
            <span>page {c.source.page ?? "—"}</span>
            <span className="min-w-0 truncate" title={c.source.hierarchyPath}>{c.source.hierarchyPath}</span>
            <span>doc v{c.source.documentVersion}</span>
          </div>
          <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-amber-400/70 bg-amber-50/60 p-2 text-xs leading-relaxed text-ink-200 dark:bg-amber-950/20">
            {c.source.sourceText}
          </blockquote>
          {c.source.fragmentIds.length > 0 && (
            <div className="mt-1.5 truncate font-mono text-2xs text-ink-500" title={c.source.fragmentIds.join(", ")}>
              fragments: {c.source.fragmentIds.join(", ")}
            </div>
          )}
        </div>
        {c.exceptions.length > 0 && (
          <div className="mt-2">
            <div className="mb-0.5 text-2xs uppercase tracking-wider text-ink-500">Exceptions</div>
            <ul className="list-inside list-disc text-2xs text-ink-300">
              {c.exceptions.map((x, i) => <li key={i}>{x}</li>)}
            </ul>
          </div>
        )}
      </div>

      {/* Right: extracted rule + actions */}
      <div>
        <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Extracted rule</div>
        <div className="border border-ink-600 bg-ink-900 p-2">
          <div className="flex items-center gap-1.5">
            <Badge tone={STATUS_TONE[c.status]}>{STATUS_LABEL[c.status]}</Badge>
            <Badge tone="grey">{c.constraint.type}</Badge>
            {!c.machineExecutable && (
              <Badge tone="amber" title="Cannot be enforced automatically by the constraint engine">
                non-executable
              </Badge>
            )}
            <span className="ml-auto font-mono text-2xs text-ink-400">v{c.ruleVersion}</span>
          </div>

          <div className="mt-2 text-xs text-ink-100">
            <span className="font-semibold">{c.constraint.subject}</span>
            <span className="text-ink-500"> → </span>
            <span className="font-semibold">{c.constraint.referenceObject}</span>
          </div>
          <div className="mt-1 font-mono text-sm text-ink-100">
            {c.constraint.operator}{" "}
            {c.constraint.value != null ? c.constraint.value : "—"}{" "}
            {c.constraint.unit ?? ""}
          </div>

          <div className="mt-2 space-y-0.5 text-2xs text-ink-400">
            <div>jurisdiction: <span className="text-ink-300">{c.applicability.jurisdiction ?? "—"}</span></div>
            <div>plant types: <span className="text-ink-300">{c.applicability.plantType.join(", ") || "—"}</span></div>
            <div>object types: <span className="text-ink-300">{c.applicability.objectTypes.join(", ") || "—"}</span></div>
            {c.applicability.conditions.length > 0 && (
              <div>conditions: <span className="text-ink-300">{c.applicability.conditions.join("; ")}</span></div>
            )}
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-2xs text-ink-400">confidence {Math.round(c.confidence * 100)}%</span>
            <VerdictBadge c={c} />
          </div>
          {c.verification?.notes && (
            <p className="mt-1 text-2xs leading-relaxed text-ink-400">{c.verification.notes}</p>
          )}

          <div className="mt-3 flex flex-wrap gap-1.5 border-t border-ink-700 pt-2">
            <Btn className="h-6 text-2xs" disabled={busy} onClick={() => void verifyRegCandidate(c.id)}>
              {busy ? <Spinner className="h-2.5 w-2.5" /> : null} Verify
            </Btn>
            <Btn
              variant="primary" className="h-6 text-2xs"
              disabled={busy || !canApprove}
              onClick={() => void reviewRegCandidate(c.id, { action: "approve" })}
              title={canApprove ? "Mark as VERIFIED" : `Cannot approve from status ${c.status}`}
            >
              Approve
            </Btn>
            <Btn className="h-6 text-2xs" disabled={busy} onClick={() => setEditing((x) => !x)}>
              Edit
            </Btn>
            <Btn
              className="h-6 border-red-300 text-2xs text-red-700 dark:border-red-800 dark:text-red-300"
              disabled={busy}
              onClick={() => void reviewRegCandidate(c.id, { action: "reject" })}
            >
              Reject
            </Btn>
            {c.status === "ACTIVE" ? (
              <Btn className="h-6 text-2xs" disabled={busy} onClick={() => void deactivateRegCandidate(c.id)}>
                Deactivate
              </Btn>
            ) : (
              <Btn
                className="h-6 text-2xs"
                disabled={busy || !canActivate}
                onClick={() => void activateRegCandidate(c.id)}
                title={
                  c.status !== "VERIFIED"
                    ? "Only VERIFIED candidates can be activated"
                    : numericBlocked
                      ? "Numeric value was not literally found in the source — cannot activate"
                      : "Merge into the active rule registry"
                }
              >
                Activate
              </Btn>
            )}
          </div>

          {editing && <EditForm c={c} onClose={() => setEditing(false)} />}

          <div className="mt-2 border-t border-ink-700 pt-1.5 font-mono text-2xs text-ink-500">
            extracted by {c.extraction.model} · prompt {c.extraction.promptVersion}
            {c.reviewer ? ` · reviewed by ${c.reviewer}` : ""}
          </div>
        </div>
      </div>
    </div>
  );
}

function CandidatesPane() {
  const docId = useApp((s) => s.regSelectedDocId);
  const candidates = useApp((s) => s.regCandidates);
  const loading = useApp((s) => s.regCandidatesLoading);
  const filter = useApp((s) => s.regFilter);
  const selectedId = useApp((s) => s.regSelectedCandidateId);

  if (!docId) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-xs text-ink-500">
        Select a document on the left to review its extracted rule candidates.
      </div>
    );
  }

  const filtered = (candidates ?? []).filter((c) => filter === "ALL" || c.status === filter);
  const selected = filtered.find((c) => c.id === selectedId) ?? null;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-ink-600 px-2 py-1.5">
        {FILTERS.map((f) => (
          <button
            key={f}
            className={`rounded-sm border px-1.5 py-0.5 text-2xs transition-colors ${
              f === filter
                ? "border-ink-500 bg-ink-800 text-ink-100"
                : "border-transparent text-ink-400 hover:text-ink-200"
            }`}
            onClick={() => setRegFilter(f)}
          >
            {f === "ALL" ? "All" : STATUS_LABEL[f]}
          </button>
        ))}
        {loading && <Spinner className="ml-1 h-2.5 w-2.5" />}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="w-64 shrink-0 overflow-y-auto border-r border-ink-600">
          {filtered.length === 0 && !loading && (
            <div className="p-3 text-2xs text-ink-500">
              {candidates == null || candidates.length === 0
                ? "No candidates for this document yet — run extraction."
                : "No candidates match this filter."}
            </div>
          )}
          {filtered.map((c) => (
            <button
              key={c.id}
              className={`block w-full border-b border-ink-700/60 px-2 py-1.5 text-left hover:bg-ink-850 ${
                c.id === selectedId ? "bg-ink-850" : ""
              }`}
              onClick={() => selectRegCandidate(c.id)}
            >
              <div className="flex items-center gap-1.5">
                <span className="min-w-0 flex-1 truncate text-2xs text-ink-200" title={c.constraint.subject}>
                  {c.constraint.subject} → {c.constraint.referenceObject}
                </span>
                <Badge tone={STATUS_TONE[c.status]}>{STATUS_LABEL[c.status]}</Badge>
              </div>
              <div className="mt-0.5 flex items-center gap-2 font-mono text-2xs text-ink-500">
                <span>
                  {c.constraint.operator} {c.constraint.value ?? "—"} {c.constraint.unit ?? ""}
                </span>
                <span>{Math.round(c.confidence * 100)}%</span>
              </div>
            </button>
          ))}
        </div>
        {selected
          ? <CandidateReview c={selected} />
          : (
            <div className="flex flex-1 items-center justify-center p-6 text-xs text-ink-500">
              Select a candidate to review it against its source text.
            </div>
          )}
      </div>
    </div>
  );
}

// --- Rule registry (ACTIVE + YAML) — moved here from the old sidebar block ----

function RuleRow({ rule }: { rule: RegulatoryRule }) {
  const inactive = rule.effectiveSource === "inactive" || !rule.enabled;
  return (
    <div className={`border-b border-ink-700/60 py-1.5 last:border-0 ${inactive ? "opacity-50" : ""}`}>
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-200" title={rule.id}>
          {rule.id}
        </span>
        <RuleRoleBadge role={rule.role} />
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
        {ruleConditionNote(rule) && (
          <span className="shrink-0 text-ink-500" title="Условие применимости правила">
            {ruleConditionNote(rule)}
          </span>
        )}
        <span className="truncate" title={rule.source.title}>
          {rule.source.document}{rule.source.clause ? ` · ${rule.source.clause}` : ""}
        </span>
        <span className={rule.severity === "hard" ? "text-red-600/80 dark:text-red-400/80" : "text-ink-500"}>
          {rule.severity}
        </span>
      </div>
      {rule.source.excerpt && (
        <div className="mt-0.5 truncate text-2xs italic text-ink-500" title={rule.source.excerpt}>
          “{rule.source.excerpt}”
        </div>
      )}
    </div>
  );
}

function RegistryPane() {
  const rules = useApp((s) => s.rules);
  const rulesError = useApp((s) => s.rulesError);

  const active = rules?.filter((r) => r.enabled && r.effectiveSource !== "inactive") ?? [];
  const inactive = rules?.filter((r) => !r.enabled || r.effectiveSource === "inactive") ?? [];

  return (
    <div className="flex-1 overflow-y-auto p-3">
      <div className="mx-auto max-w-2xl">
        <p className="mb-3 text-2xs leading-relaxed text-ink-500">
          The rule registry the constraint engine actually uses: the hand-maintained
          YAML baseline plus every ACTIVE candidate extracted from uploaded documents
          (ids <span className="font-mono">REG_*</span>, status verified, full provenance).
        </p>
        {rulesError && <div className="text-2xs text-red-600 dark:text-red-400">{rulesError}</div>}
        {!rules && !rulesError && (
          <div className="flex items-center gap-2 text-2xs text-ink-500"><Spinner /> loading rules…</div>
        )}
        {rules && (
          <>
            <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">
              Active ({active.length})
            </div>
            {active.map((r) => <RuleRow key={r.id} rule={r} />)}
            {inactive.length > 0 && (
              <>
                <div className="mb-1 mt-3 text-2xs uppercase tracking-wider text-ink-500">
                  Inactive ({inactive.length})
                </div>
                {inactive.map((r) => <RuleRow key={r.id} rule={r} />)}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// --- View --------------------------------------------------------------------

export function RegulationsView() {
  const [tab, setTab] = useState<"review" | "registry">("review");
  const notice = useApp((s) => s.regNotice);

  useEffect(() => {
    ensureRegulationsLoaded();
  }, []);

  return (
    <div className="flex h-full w-full flex-col bg-ink-950" data-testid="regulations-view">
      <div className="flex shrink-0 items-center gap-1 border-b border-ink-600 bg-ink-900 px-3 py-1.5">
        {(["review", "registry"] as const).map((t) => (
          <button
            key={t}
            className={`rounded-sm border px-2 py-0.5 text-2xs uppercase tracking-wider transition-colors ${
              t === tab
                ? "border-ink-500 bg-ink-800 text-ink-100"
                : "border-transparent text-ink-400 hover:text-ink-200"
            }`}
            onClick={() => setTab(t)}
          >
            {t === "review" ? "Documents & review" : "Rule registry"}
          </button>
        ))}
      </div>

      {notice && (
        <div className="shrink-0 border-b border-amber-300 bg-amber-50 px-3 py-1.5 text-2xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          {notice}
        </div>
      )}

      {tab === "review" ? (
        <div className="flex min-h-0 flex-1">
          <DocumentsColumn />
          <CandidatesPane />
        </div>
      ) : (
        <RegistryPane />
      )}
    </div>
  );
}
