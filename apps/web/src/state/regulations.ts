import { api, ApiError } from "../api/client";
import type { CandidateReviewPatch, RuleCandidate, RuleCandidateStatus } from "../api/types";
import { getState, setState } from "./store";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export async function loadRegDocuments() {
  try {
    setState({ regDocs: await api.listRegDocuments(), regDocsError: null });
  } catch (e) {
    setState({ regDocsError: errMsg(e) });
  }
}

/** Called when the REGULATIONS view opens; loads documents once. */
export function ensureRegulationsLoaded() {
  if (getState().regDocs == null) void loadRegDocuments();
}

export async function uploadRegDocument(file: File) {
  setState({ regUploadBusy: true, regNotice: null });
  try {
    const doc = await api.uploadRegDocument(file);
    const docs = getState().regDocs ?? [];
    setState({
      regUploadBusy: false,
      regDocs: [doc, ...docs.filter((d) => d.id !== doc.id)],
      regSelectedDocId: doc.id,
    });
    void loadRegCandidates(doc.id);
  } catch (e) {
    const code = e instanceof ApiError ? e.code : null;
    setState({
      regUploadBusy: false,
      regNotice: code === "UNSUPPORTED_FORMAT"
        ? "Неподдерживаемый формат — принимаются только документы PDF и DOCX."
        : `Не удалось загрузить документ: ${errMsg(e)}`,
    });
  }
}

export function selectRegDocument(documentId: string | null) {
  setState({ regSelectedDocId: documentId, regSelectedCandidateId: null, regNotice: null });
  if (documentId) void loadRegCandidates(documentId);
  else setState({ regCandidates: null });
}

export function setRegFilter(filter: RuleCandidateStatus | "ALL") {
  setState({ regFilter: filter, regSelectedCandidateId: null });
}

export function selectRegCandidate(candidateId: string | null) {
  setState({ regSelectedCandidateId: candidateId });
}

export async function loadRegCandidates(documentId: string) {
  setState({ regCandidatesLoading: true });
  try {
    const candidates = await api.listRegCandidates({ documentId });
    // Ignore stale responses if the user switched documents meanwhile.
    if (getState().regSelectedDocId === documentId) {
      setState({ regCandidates: candidates, regCandidatesLoading: false });
    }
  } catch (e) {
    setState({ regCandidatesLoading: false, regNotice: `Failed to load candidates: ${errMsg(e)}` });
  }
}

async function refreshRegDocument(documentId: string) {
  try {
    const doc = await api.getRegDocument(documentId);
    const docs = getState().regDocs ?? [];
    setState({ regDocs: docs.map((d) => (d.id === doc.id ? doc : d)) });
    return doc;
  } catch {
    return null;
  }
}

function candidateTotal(counts: Record<string, number>): number {
  return Object.values(counts).reduce((a, b) => a + b, 0);
}

/**
 * Starts LLM extraction for a document, then polls the document until its
 * candidateCounts grow (or ~2 min pass). 409 LLM_UNAVAILABLE → clear notice.
 */
export async function extractRegDocument(documentId: string) {
  if (getState().regExtractingDocId) return;
  setState({ regNotice: null, regExtractingDocId: documentId });
  const before = getState().regDocs?.find((d) => d.id === documentId);
  const beforeTotal = before ? candidateTotal(before.candidateCounts) : 0;
  try {
    await api.extractRegDocument(documentId);
  } catch (e) {
    const code = e instanceof ApiError ? e.code : null;
    setState({
      regExtractingDocId: null,
      regNotice: code === "LLM_UNAVAILABLE"
        ? "Для извлечения правил нужна локальная LLM, а она сейчас недоступна. " +
          "Запустите сервер модели (или проверьте /api/capabilities) и повторите."
        : `Не удалось извлечь правила: ${errMsg(e)}`,
    });
    return;
  }
  // Poll candidateCounts (per contract) every 2.5 s for up to 2 minutes.
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2500));
    if (getState().regExtractingDocId !== documentId) return; // superseded
    const doc = await refreshRegDocument(documentId);
    if (!doc) continue;
    if (doc.status === "FAILED") {
      setState({ regExtractingDocId: null, regNotice: "Извлечение завершилось ошибкой на сервере (статус документа — ОШИБКА)." });
      return;
    }
    if (candidateTotal(doc.candidateCounts) > beforeTotal || doc.status === "EXTRACTED") {
      setState({ regExtractingDocId: null });
      if (getState().regSelectedDocId === documentId) void loadRegCandidates(documentId);
      return;
    }
  }
  setState({
    regExtractingDocId: null,
    regNotice: "Извлечение идёт дольше обычного — обновите список документов чуть позже.",
  });
}

function replaceCandidate(updated: RuleCandidate) {
  const list = getState().regCandidates;
  if (list) {
    setState({ regCandidates: list.map((c) => (c.id === updated.id ? updated : c)) });
  }
  // Status changes shift the per-document counters; refresh quietly.
  void refreshRegDocument(updated.documentId);
}

async function candidateCall(candidateId: string, call: () => Promise<RuleCandidate>) {
  setState({ regBusyCandidateId: candidateId, regNotice: null });
  try {
    replaceCandidate(await call());
  } catch (e) {
    setState({ regNotice: errMsg(e) });
  } finally {
    setState({ regBusyCandidateId: null });
  }
}

export function verifyRegCandidate(candidateId: string) {
  return candidateCall(candidateId, () => api.verifyRegCandidate(candidateId));
}

export function reviewRegCandidate(candidateId: string, patch: CandidateReviewPatch) {
  return candidateCall(candidateId, () => api.patchRegCandidate(candidateId, patch));
}

export async function activateRegCandidate(candidateId: string) {
  await candidateCall(candidateId, () => api.activateRegCandidate(candidateId));
  void refreshGlobalRules(); // ACTIVE candidates merge into GET /api/rules
}

export async function deactivateRegCandidate(candidateId: string) {
  await candidateCall(candidateId, () => api.deactivateRegCandidate(candidateId));
  void refreshGlobalRules();
}

async function refreshGlobalRules() {
  try {
    setState({ rules: await api.getRules(), rulesError: null });
  } catch {
    /* non-fatal — registry view will show the stale list */
  }
}
