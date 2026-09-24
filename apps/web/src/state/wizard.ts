import { api, ApiError } from "../api/client";
import type { RuleCandidate } from "../api/types";
import { buildConstraints } from "./actions";
import { getState, setState, type WizardStep } from "./store";

/** Step-2 ("Условия") + wizard lifecycle actions.
 *  Candidates gathered here follow the normal review flow via the existing
 *  regulations endpoints; ACTIVE ones merge into GET /api/rules and the
 *  constraint engine picks them up automatically. */

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

const LLM_UNAVAILABLE_MSG =
  "Локальная LLM недоступна — автоматический разбор условий сейчас невозможен. " +
  "Шаг можно пропустить: нажмите «Далее», условия необязательны.";

export function openWizard(step: WizardStep = 1) {
  setState({ wizardOpen: true, wizardStep: step });
}

export function closeWizard() {
  setState({ wizardOpen: false });
}

export function setWizardStep(step: WizardStep) {
  setState({ wizardStep: step });
}

// --- Candidate list helpers ---------------------------------------------------

function upsertCandidates(incoming: RuleCandidate[]) {
  setState((s) => {
    const map = new Map(s.wizardCandidates.map((c) => [c.id, c] as const));
    for (const c of incoming) map.set(c.id, c);
    return { wizardCandidates: [...map.values()] };
  });
}

function replaceCandidate(updated: RuleCandidate) {
  setState((s) => ({
    wizardCandidates: s.wizardCandidates.map((c) => (c.id === updated.id ? updated : c)),
  }));
}

function setCandidateError(id: string, message: string | null) {
  setState((s) => {
    const next = { ...s.wizardCandidateErrors };
    if (message == null) delete next[id];
    else next[id] = message;
    return { wizardCandidateErrors: next };
  });
}

/** Count of conditions activated through the wizard candidate list. */
export function wizardActivatedCount(): number {
  return getState().wizardCandidates.filter((c) => c.status === "ACTIVE").length;
}

// --- Step 2: free-text condition ----------------------------------------------

/** Returns true when at least one candidate was extracted (clears the input). */
export async function wizardParseText(text: string): Promise<boolean> {
  const trimmed = text.trim();
  if (trimmed === "" || getState().wizardBusy) return false;
  setState({ wizardBusy: true, wizardBusyLabel: "Разбор условия…", wizardNotice: null });
  try {
    const res = await api.parseRegulationText(trimmed);
    upsertCandidates(res.candidates);
    if (res.candidates.length === 0) {
      setState({ wizardNotice: "Из текста не удалось извлечь ни одного условия — уточните формулировку." });
      return false;
    }
    return true;
  } catch (e) {
    const code = e instanceof ApiError ? e.code : null;
    setState({
      wizardNotice: code === "LLM_UNAVAILABLE"
        ? LLM_UNAVAILABLE_MSG
        : `Не удалось разобрать условие: ${errMsg(e)}`,
    });
    return false;
  } finally {
    setState({ wizardBusy: false, wizardBusyLabel: null });
  }
}

// --- Step 2: PDF/DOCX document ------------------------------------------------

function candidateTotal(counts: Record<string, number>): number {
  return Object.values(counts).reduce((a, b) => a + b, 0);
}

export async function wizardUploadConditionDoc(file: File) {
  if (getState().wizardBusy) return;
  setState({ wizardBusy: true, wizardBusyLabel: "Загрузка документа…", wizardNotice: null });
  try {
    const doc = await api.uploadRegDocument(file);
    // Keep the regulations view's document list fresh if it was loaded.
    setState((s) => ({
      regDocs: s.regDocs ? [doc, ...s.regDocs.filter((d) => d.id !== doc.id)] : s.regDocs,
    }));

    setState({ wizardBusyLabel: "Извлечение правил из документа…" });
    const before = candidateTotal(doc.candidateCounts ?? {});
    await api.extractRegDocument(doc.id);

    // Poll candidateCounts (per contract) every 2.5 s for up to 2 minutes.
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2500));
      if (!getState().wizardOpen) return; // wizard dismissed — stop quietly
      let fresh;
      try {
        fresh = await api.getRegDocument(doc.id);
      } catch {
        continue;
      }
      if (fresh.status === "FAILED") {
        setState({ wizardNotice: "Извлечение не удалось (документ в статусе FAILED)." });
        return;
      }
      if (candidateTotal(fresh.candidateCounts) > before || fresh.status === "EXTRACTED") {
        const candidates = await api.listRegCandidates({ documentId: doc.id });
        upsertCandidates(candidates);
        if (candidates.length === 0) {
          setState({ wizardNotice: "Из документа не удалось извлечь условий." });
        }
        return;
      }
    }
    setState({ wizardNotice: "Извлечение идёт дольше обычного — кандидаты появятся во вкладке Regulations." });
  } catch (e) {
    const code = e instanceof ApiError ? e.code : null;
    setState({
      wizardNotice: code === "LLM_UNAVAILABLE"
        ? LLM_UNAVAILABLE_MSG
        : code === "UNSUPPORTED_FORMAT"
          ? "Неподдерживаемый формат — принимаются только PDF и DOCX."
          : `Не удалось обработать документ: ${errMsg(e)}`,
    });
  } finally {
    setState({ wizardBusy: false, wizardBusyLabel: null });
  }
}

// --- Step 2: candidate review actions ------------------------------------------

async function refreshRulesQuietly() {
  try {
    setState({ rules: await api.getRules(), rulesError: null });
  } catch {
    /* non-fatal */
  }
}

const NUMERIC_MSG =
  "Числовое значение не найдено дословно в тексте источника — активация запрещена. " +
  "Исправьте значение через «Редактировать» (по тексту) или отклоните условие.";

/** Подтвердить: verify (if needed) → approve → activate. */
export async function wizardApproveCandidate(id: string) {
  const c = getState().wizardCandidates.find((x) => x.id === id);
  if (!c || getState().wizardCandidateBusyId) return;
  setState({ wizardCandidateBusyId: id });
  setCandidateError(id, null);
  try {
    let cur = c;
    if (!cur.verification) {
      cur = await api.verifyRegCandidate(cur.id);
      replaceCandidate(cur);
    }
    // Client-side pre-check mirrors the server rule for numeric constraints.
    if (cur.constraint.value != null && cur.verification && !cur.verification.numericLiteralFound) {
      setCandidateError(id, NUMERIC_MSG);
      return;
    }
    if (cur.status !== "VERIFIED" && cur.status !== "ACTIVE") {
      cur = await api.patchRegCandidate(cur.id, { action: "approve" });
      replaceCandidate(cur);
    }
    if (cur.status === "VERIFIED") {
      cur = await api.activateRegCandidate(cur.id);
      replaceCandidate(cur);
    }
    // Activation invalidates cached constraints server-side — rebuild now so
    // generation and the overlays use the new rule immediately.
    void refreshRulesQuietly();
    void buildConstraints();
  } catch (e) {
    const code = e instanceof ApiError ? e.code : null;
    setCandidateError(id, code === "NUMERIC_LITERAL_NOT_FOUND" ? NUMERIC_MSG : errMsg(e));
  } finally {
    setState({ wizardCandidateBusyId: null });
  }
}

/** Редактировать: PATCH edit (bumps ruleVersion), then approve. */
export async function wizardEditCandidate(
  id: string,
  edits: { value: number | null; unit: string | null; objectTypes: string[] },
) {
  if (getState().wizardCandidateBusyId) return;
  setState({ wizardCandidateBusyId: id });
  setCandidateError(id, null);
  try {
    let cur = await api.patchRegCandidate(id, {
      action: "edit",
      edits: {
        constraint: { value: edits.value, unit: edits.unit },
        applicability: { objectTypes: edits.objectTypes },
      },
    });
    replaceCandidate(cur);
    if (cur.status !== "VERIFIED" && cur.status !== "ACTIVE") {
      cur = await api.patchRegCandidate(id, { action: "approve" });
      replaceCandidate(cur);
    }
  } catch (e) {
    setCandidateError(id, errMsg(e));
  } finally {
    setState({ wizardCandidateBusyId: null });
  }
}

export async function wizardRejectCandidate(id: string) {
  if (getState().wizardCandidateBusyId) return;
  setState({ wizardCandidateBusyId: id });
  setCandidateError(id, null);
  try {
    replaceCandidate(await api.patchRegCandidate(id, { action: "reject" }));
  } catch (e) {
    setCandidateError(id, errMsg(e));
  } finally {
    setState({ wizardCandidateBusyId: null });
  }
}
