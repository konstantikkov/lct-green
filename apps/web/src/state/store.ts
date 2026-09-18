import { useSyncExternalStore } from "react";
import type {
  Capabilities, ChunkInfo, ConstraintsResponse, ExclusionZone, Explanation,
  IterationDetail, IterationSummary, Placement, PipelineState, ProjectDocument,
  ProjectListItem, RegulatoryRule, RunStatus, SpeciesInfo,
} from "../api/types";

export type ViewMode = "2d" | "3d";

/** Persisted like the theme; "simple" hides the 3D detail layer. */
export const MASSING_DETAIL_STORAGE_KEY = "greenplan.massingDetail";

function readInitialMassingDetail(): boolean {
  try {
    return localStorage.getItem(MASSING_DETAIL_STORAGE_KEY) !== "simple";
  } catch {
    return true;
  }
}

/** Mobile bottom-sheet tab ids (< md only). */
export type MobileTab = "layers" | "constraints" | "generate" | "iterations" | "inspector";
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

  /** Overall pipeline chip in the top bar. */
  pipelineState: PipelineState | null;

  /** Mobile-only UI (bottom sheet); inert on desktop. */
  mobileTab: MobileTab;
  sheetSnap: SheetSnap;
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
  pipelineState: null,
  mobileTab: "layers",
  sheetSnap: "peek",
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

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Selector hook. Selectors must return values stored in state (or primitives)
 * so reference equality short-circuits re-renders.
 */
export function useApp<T>(selector: (s: AppState) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state), () => selector(state));
}
