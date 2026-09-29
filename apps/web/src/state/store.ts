import { useSyncExternalStore } from "react";
import type {
  Capabilities, ChunkInfo, ComplianceAudit, ConstraintsResponse, ExclusionZone,
  Explanation, IterationDetail, IterationSummary, Placement, PipelineState,
  ProjectDocument, ProjectListItem, ProjectSummary, RegDocument, RegulatoryRule,
  RuleCandidate, RuleCandidateStatus, RunStatus, SpeciesInfo, Strategy,
} from "../api/types";

export type ViewMode = "2d" | "3d";

/** Progressive-disclosure stage, derived from state (never stored). */
export type Stage = "empty" | "imported" | "generated";

/** Top-level views. PLAN is the default workflow; the rest are secondary. */
export type NavView = "plan" | "regulations" | "runs" | "quality" | "audit";

/** Workspace tab strip (PLAN view): exactly one section panel open at a time. */
export type WorkspaceTab = "interpretation" | "constraints" | "generate" | "result";

/** Compliance-audit fetch lifecycle. */
export type AuditStatus = "idle" | "loading" | "loaded" | "no_result" | "error";

/** Step-by-step wizard after a fresh upload: 1 Ограничения · 2 Условия ·
 *  3 Генерация · 4 Итоги. */
export type WizardStep = 1 | 2 | 3 | 4;

/** Generation options shared by the TopBar button and the Generate panel. */
export interface GenOptions {
  /** null = "all species" (catalog may not be loaded yet). */
  speciesIds: string[] | null;
  strategy: Strategy;
  targetCount: string;   // raw input; "" = Auto
  seed: string;          // advanced
  maxIterations: string; // advanced
  useLlmJudge: boolean;  // advanced
}

/** Persisted like the theme; "simple" hides the 3D detail layer. */
export const MASSING_DETAIL_STORAGE_KEY = "greenplan.massingDetail";

function readInitialMassingDetail(): boolean {
  try {
    return localStorage.getItem(MASSING_DETAIL_STORAGE_KEY) !== "simple";
  } catch {
    return true;
  }
}

/** Mobile bottom-sheet content (< md only): the active workspace-tab panel
 *  or the inspector for the current canvas selection. */
export type MobileTab = "panel" | "inspector";
/** Mobile bottom-sheet snap states (< md only). */
export type SheetSnap = "peek" | "half" | "full";

export type Selection =
  | { type: "placement"; placement: Placement }
  | { type: "zone"; zone: ExclusionZone }
  | null;

export interface OverlayState {
  originalCad: boolean;
  exclusionZones: boolean;
  allowedArea: boolean;
  rejectedCandidates: boolean;
  trees: boolean;
  shrubs: boolean;
  existingVegetation: boolean;
  chunkGrid: boolean;
}

export interface AppState {
  capabilities: Capabilities | null;
  document: ProjectDocument | null;
  uploading: boolean;
  uploadError: { code: string | null; message: string } | null;

  /** Shared project list (no auth — all projects are common). */
  projects: ProjectListItem[] | null;
  projectsError: string | null;
  /** Project id currently being opened from the list. */
  projectLoadingId: string | null;

  /** Rich admin-dashboard listing (GET /api/projects/summary). */
  summaries: ProjectSummary[] | null;
  summariesError: string | null;
  /** Non-fatal dashboard note (unknown deep-link project id, delete failure). */
  routeError: string | null;

  viewMode: ViewMode;
  /** 3D massing: true = Detailed (CAD underlay, lanterns, fences), false = Simple. */
  massingDetail: boolean;

  layerVisibility: Record<string, boolean>;
  overlays: OverlayState;
  chunks: ChunkInfo[] | null;

  rules: RegulatoryRule[] | null;
  rulesError: string | null;
  species: SpeciesInfo[] | null;
  speciesError: string | null;

  constraints: ConstraintsResponse | null;
  constraintsLoading: boolean;
  constraintsError: string | null;

  run: RunStatus | null;
  runActive: boolean;
  runError: string | null;

  iterations: IterationSummary[];
  activeIteration: IterationDetail | null;
  iterationLoadingId: string | null;

  selection: Selection;
  explanation: Explanation | null;
  explanationLoading: boolean;
  explanationError: string | null;

  /** Brief status toast after a manual placement move (auto-dismissed). */
  moveToast: { text: string; tone: "ok" | "warn" | "error" } | null;

  /** Overall pipeline chip in the top bar. */
  pipelineState: PipelineState | null;

  /** Mobile-only UI (bottom sheet); inert on desktop. */
  mobileTab: MobileTab;
  sheetSnap: SheetSnap;

  /** Top-level view (PLAN / REGULATIONS / RUNS / QUALITY / AUDIT). */
  nav: NavView;
  /** Active workspace tab in the PLAN view (drives desktop panel AND sheet). */
  workspaceTab: WorkspaceTab;
  /** Desktop-only: the workspace panel is hidden (canvas full-width). */
  panelCollapsed: boolean;
  /** Shared generation options (TopBar button + Generate panel). */
  genOptions: GenOptions;

  /** Compliance audit (GET /api/projects/{id}/compliance). */
  audit: ComplianceAudit | null;
  auditStatus: AuditStatus;
  auditError: string | null;

  // --- Regulations feature (WS4) ---
  regDocs: RegDocument[] | null;
  regDocsError: string | null;
  regUploadBusy: boolean;
  /** Non-fatal notices, e.g. 409 LLM_UNAVAILABLE on extract. */
  regNotice: string | null;
  regSelectedDocId: string | null;
  /** Document id with a running extraction poll. */
  regExtractingDocId: string | null;
  regCandidates: RuleCandidate[] | null;
  regCandidatesLoading: boolean;
  regFilter: RuleCandidateStatus | "ALL";
  regSelectedCandidateId: string | null;
  /** Candidate id with an in-flight verify/review/activate call. */
  regBusyCandidateId: string | null;

  // --- Step-by-step wizard (opens automatically after a successful upload) ---
  wizardOpen: boolean;
  wizardStep: WizardStep;
  /** Rule candidates gathered in step 2 (free text + document extraction). */
  wizardCandidates: RuleCandidate[];
  /** Non-fatal step-2 notice (e.g. LLM_UNAVAILABLE) — amber, step passable. */
  wizardNotice: string | null;
  /** Text parse or document upload+extract in flight. */
  wizardBusy: boolean;
  wizardBusyLabel: string | null;
  /** Candidate id with an in-flight approve/edit/reject chain. */
  wizardCandidateBusyId: string | null;
  /** Per-candidate error messages (e.g. NUMERIC_LITERAL_NOT_FOUND). */
  wizardCandidateErrors: Record<string, string>;
}

/** empty (no project) → imported (doc loaded) → generated (≥1 iteration). */
export function stageOf(s: AppState): Stage {
  if (!s.document) return "empty";
  return s.iterations.length > 0 ? "generated" : "imported";
}

export function useStage(): Stage {
  return useApp(stageOf);
}

export const initialOverlays: OverlayState = {
  originalCad: true,
  exclusionZones: true,
  allowedArea: true,
  rejectedCandidates: false,
  trees: true,
  shrubs: true,
  existingVegetation: true,
  chunkGrid: false,
};

const initialState: AppState = {
  capabilities: null,
  document: null,
  uploading: false,
  uploadError: null,
  projects: null,
  projectsError: null,
  projectLoadingId: null,
  summaries: null,
  summariesError: null,
  routeError: null,
  viewMode: "2d",
  massingDetail: readInitialMassingDetail(),
  layerVisibility: {},
  overlays: initialOverlays,
  chunks: null,
  rules: null,
  rulesError: null,
  species: null,
  speciesError: null,
  constraints: null,
  constraintsLoading: false,
  constraintsError: null,
  run: null,
  runActive: false,
  runError: null,
  iterations: [],
  activeIteration: null,
  iterationLoadingId: null,
  selection: null,
  explanation: null,
  explanationLoading: false,
  explanationError: null,
  moveToast: null,
  pipelineState: null,
  mobileTab: "panel",
  sheetSnap: "peek",
  nav: "plan",
  workspaceTab: "interpretation",
  panelCollapsed: false,
  audit: null,
  auditStatus: "idle",
  auditError: null,
  genOptions: {
    speciesIds: null,
    strategy: "balanced",
    targetCount: "",
    seed: "42",
    maxIterations: "3",
    useLlmJudge: true,
  },
  regDocs: null,
  regDocsError: null,
  regUploadBusy: false,
  regNotice: null,
  regSelectedDocId: null,
  regExtractingDocId: null,
  regCandidates: null,
  regCandidatesLoading: false,
  regFilter: "ALL",
  regSelectedCandidateId: null,
  regBusyCandidateId: null,
  wizardOpen: false,
  wizardStep: 1,
  wizardCandidates: [],
  wizardNotice: null,
  wizardBusy: false,
  wizardBusyLabel: null,
  wizardCandidateBusyId: null,
  wizardCandidateErrors: {},
};

let state: AppState = initialState;
const listeners = new Set<() => void>();

export function getState(): AppState {
  return state;
}

export function setState(partial: Partial<AppState> | ((s: AppState) => Partial<AppState>)) {
  const patch = typeof partial === "function" ? partial(state) : partial;
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

/** Also used by the hand-rolled router to sync the URL with the state. */
export function subscribeStore(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Selector hook. Selectors must return values stored in state (or primitives)
 * so reference equality short-circuits re-renders.
 */
export function useApp<T>(selector: (s: AppState) => T): T {
  return useSyncExternalStore(subscribeStore, () => selector(state), () => selector(state));
}
